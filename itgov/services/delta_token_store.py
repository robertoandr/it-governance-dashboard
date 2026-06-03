from __future__ import annotations

import hashlib
import os

import aiosqlite
import structlog

log = structlog.get_logger(__name__)

_DB_PATH = os.environ.get("DELTA_TOKEN_DB_PATH", "/data/delta_tokens.db")

_DDL = """
CREATE TABLE IF NOT EXISTS delta_tokens (
    resource   TEXT NOT NULL,
    tenant_id  TEXT NOT NULL,
    delta_link TEXT NOT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (resource, tenant_id)
);
"""


def _safe_link_repr(delta_link: str) -> str:
    """Return a truncated hash of the delta_link safe for logging."""
    h = hashlib.sha256(delta_link.encode()).hexdigest()[:12]
    return f"sha256:{h}…"


class DeltaTokenStore:
    """Async SQLite store for Graph API delta links.

    The delta_link value is a full URL containing a skiptoken that encodes
    tenant state. It must never appear in logs or exception messages.
    """

    def __init__(self, db_path: str = _DB_PATH) -> None:
        self._db_path = db_path

    async def _ensure_schema(self, conn: aiosqlite.Connection) -> None:
        await conn.execute(_DDL)
        await conn.commit()

    async def get(self, resource: str, tenant_id: str) -> str | None:
        """Return the stored delta_link or None if not found.

        Args:
            resource: Graph resource name, e.g. "servicePrincipals".
            tenant_id: Microsoft tenant ID.

        Returns:
            The delta_link string, or None on first run.
        """
        async with aiosqlite.connect(self._db_path) as conn:
            await self._ensure_schema(conn)
            async with conn.execute(
                "SELECT delta_link FROM delta_tokens WHERE resource = ? AND tenant_id = ?",
                (resource, tenant_id),
            ) as cursor:
                row = await cursor.fetchone()

        found = row is not None
        log.debug(
            "delta_token.get",
            resource=resource,
            tenant_id=tenant_id,
            found=found,
            link_hash=_safe_link_repr(row[0]) if row else None,
        )
        return row[0] if row else None

    async def set(self, resource: str, tenant_id: str, delta_link: str) -> None:
        """Upsert a delta_link for the given resource + tenant.

        Args:
            resource: Graph resource name.
            tenant_id: Microsoft tenant ID.
            delta_link: New deltaLink URL from Graph API response.
        """
        async with aiosqlite.connect(self._db_path) as conn:
            await self._ensure_schema(conn)
            await conn.execute(
                """
                INSERT INTO delta_tokens (resource, tenant_id, delta_link, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(resource, tenant_id) DO UPDATE SET
                    delta_link = excluded.delta_link,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (resource, tenant_id, delta_link),
            )
            await conn.commit()

        log.info(
            "delta_token.set",
            resource=resource,
            tenant_id=tenant_id,
            link_hash=_safe_link_repr(delta_link),
        )

    async def clear(self, resource: str, tenant_id: str) -> None:
        """Delete the stored delta_link, forcing next run to do a full scan.

        Args:
            resource: Graph resource name.
            tenant_id: Microsoft tenant ID.
        """
        async with aiosqlite.connect(self._db_path) as conn:
            await self._ensure_schema(conn)
            await conn.execute(
                "DELETE FROM delta_tokens WHERE resource = ? AND tenant_id = ?",
                (resource, tenant_id),
            )
            await conn.commit()

        log.warning(
            "delta_token.cleared",
            resource=resource,
            tenant_id=tenant_id,
        )
