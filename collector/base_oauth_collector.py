"""Generic OAuth2 client-credentials collector base class.

Handles token caching, Retry-After (429) back-off, and @odata.nextLink
pagination so concrete collectors (Entra ID, Acronis, …) only need to
implement _collect().
"""

from __future__ import annotations

import time
from collections.abc import Generator
from typing import Any

import msal
import requests
import structlog

log = structlog.get_logger(__name__)

_TOKEN_EXPIRY_BUFFER = 60  # refresh token this many seconds before it expires


class BaseOAuthCollector:
    """OAuth2 client-credentials base for REST API collectors.

    Args:
        tenant_id: OAuth2 tenant/realm identifier.
        client_id: Application (client) ID.
        client_secret: Client secret value — NEVER logged.
        scopes: OAuth2 scopes for the token request.
        authority: Token endpoint authority URL.
    """

    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        scopes: list[str],
        authority: str | None = None,
    ) -> None:
        self._scopes = scopes
        self._app = msal.ConfidentialClientApplication(
            client_id,
            authority=authority or f"https://login.microsoftonline.com/{tenant_id}",
            client_credential=client_secret,
        )
        self._cached_token: str | None = None
        self._token_expires_at: float = 0.0

    # ── Token management ─────────────────────────────────────────────────────

    def _get_token(self) -> str:
        """Return a valid access token, refreshing via MSAL cache if needed."""
        now = time.monotonic()
        if self._cached_token and now < self._token_expires_at:
            return self._cached_token

        result = self._app.acquire_token_for_client(scopes=self._scopes)
        if "access_token" not in result:
            error = result.get("error_description", result.get("error", "unknown"))
            raise RuntimeError(f"OAuth2 token acquisition failed: {error}")

        self._cached_token = result["access_token"]
        expires_in = result.get("expires_in", 3600)
        self._token_expires_at = now + expires_in - _TOKEN_EXPIRY_BUFFER
        log.info("oauth_token_acquired", expires_in=expires_in)
        return self._cached_token

    # ── HTTP helpers ─────────────────────────────────────────────────────────

    def _get(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET a single page, honouring Retry-After on 429."""
        headers = {"Authorization": f"Bearer {self._get_token()}"}
        for attempt in range(4):
            resp = requests.get(url, headers=headers, params=params, timeout=30)
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 30))
                log.warning("rate_limited", url=url, retry_after=retry_after, attempt=attempt)
                time.sleep(retry_after)
                headers["Authorization"] = f"Bearer {self._get_token()}"
                continue
            resp.raise_for_status()
            return resp.json()
        raise RuntimeError(f"Exceeded retry attempts for {url}")

    def _paginate(self, url: str, params: dict[str, Any] | None = None) -> Generator[dict[str, Any], None, None]:
        """Yield every item from a paginated OData endpoint (@odata.nextLink)."""
        next_url: str | None = url
        while next_url:
            page = self._get(next_url, params=params if next_url == url else None)
            yield from page.get("value", [])
            next_url = page.get("@odata.nextLink")

    # ── Entry point ──────────────────────────────────────────────────────────

    def collect(self) -> None:
        """Run the collection cycle. Must be implemented by subclasses."""
        raise NotImplementedError
