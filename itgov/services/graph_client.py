from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
LOGIN_URL = "https://login.microsoftonline.com"

_SP_SELECT = "id,displayName,appId,servicePrincipalType,signInActivity,passwordCredentials,keyCredentials,appRoles"
_SP_DELTA_URL = f"{GRAPH_BASE}/servicePrincipals/delta?$select={_SP_SELECT}&$top=999"


class GraphAuthError(Exception):
    pass


class GraphRateLimitError(Exception):
    def __init__(self, retry_after: int = 60):
        self.retry_after = retry_after
        super().__init__(f"Rate limited; retry after {retry_after}s")


def _get_credentials() -> tuple[str, str, str]:
    """Read credentials from env — never log the returned values."""
    return (
        os.environ["AZURE_TENANT_ID"],
        os.environ["AZURE_CLIENT_ID"],
        os.environ["AZURE_CLIENT_SECRET"],
    )


async def _fetch_token(client: httpx.AsyncClient) -> str:
    tenant_id, client_id, client_secret = _get_credentials()
    resp = await client.post(
        f"{LOGIN_URL}/{tenant_id}/oauth2/v2.0/token",
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": "https://graph.microsoft.com/.default",
        },
    )
    if resp.status_code != 200:
        # Log status only — never log body (may contain token details)
        logger.error("Token request failed with status %d", resp.status_code)
        raise GraphAuthError(f"Token request failed: HTTP {resp.status_code}")
    return resp.json()["access_token"]


@retry(
    retry=retry_if_exception_type(httpx.TransportError),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    reraise=True,
)
async def _get(client: httpx.AsyncClient, url: str, token: str) -> dict:
    resp = await client.get(url, headers={"Authorization": f"Bearer {token}"})
    if resp.status_code == 429:
        retry_after = int(resp.headers.get("Retry-After", "60"))
        raise GraphRateLimitError(retry_after)
    resp.raise_for_status()
    return resp.json()


class GraphClient:
    """Async Microsoft Graph API client with delta query support."""

    def __init__(self, timeout: float = 60.0) -> None:
        self._timeout = timeout
        self.last_delta_link: str | None = None

    async def get_service_principals_delta(
        self,
        tenant_id: str,
        delta_link: str | None = None,
    ) -> AsyncIterator[dict]:
        """Yield Service Principals using Graph delta queries.

        Args:
            tenant_id: Microsoft tenant ID (used for logging only).
            delta_link: Previous deltaLink for incremental sync.
                        None triggers a full initial scan.

        Yields:
            Individual SP objects from the Graph API response.

        After iteration completes, ``self.last_delta_link`` holds the new
        deltaLink to persist for the next run.
        """
        self.last_delta_link = None
        start_url = delta_link if delta_link is not None else _SP_DELTA_URL
        mode = "delta" if delta_link is not None else "full"
        logger.info("graph.sp_delta.start tenant=%s mode=%s", tenant_id, mode)

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            token = await _fetch_token(client)
            url: str | None = start_url
            page = 0

            while url:
                data = await _get(client, url, token)
                page += 1
                items = data.get("value", [])
                logger.debug("graph.sp_delta.page page=%d items=%d", page, len(items))

                for sp in items:
                    yield sp

                next_link = data.get("@odata.nextLink")
                delta_link_response = data.get("@odata.deltaLink")

                if delta_link_response:
                    # End of this sync cycle — store the deltaLink for next run
                    self.last_delta_link = delta_link_response
                    url = None
                elif next_link:
                    url = next_link
                else:
                    url = None

        logger.info(
            "graph.sp_delta.done tenant=%s mode=%s pages=%d delta_obtained=%s",
            tenant_id,
            mode,
            page,
            self.last_delta_link is not None,
        )


# Module-level convenience for simple full-scan usage (backwards compat)
async def iter_service_principals(timeout: float = 60.0) -> AsyncIterator[dict]:
    """Yield all Service Principals via delta full-scan (no persistence)."""
    client = GraphClient(timeout=timeout)
    tenant_id = os.environ.get("AZURE_TENANT_ID", "unknown")
    async for sp in client.get_service_principals_delta(tenant_id):
        yield sp
