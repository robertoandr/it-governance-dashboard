from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import structlog
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    wait_random,
)

from app.integrations.github.auth import AuthProvider
from app.integrations.github.config import GitHubSettings
from app.integrations.github.exceptions import (
    GitHubAPIError,
    GitHubAuthError,
    GitHubNotFoundError,
    GitHubRateLimitError,
    GitHubServerError,
    GitHubValidationError,
)
from app.integrations.github.models import (
    CodeQLAlert,
    DependabotAlert,
    PullRequest,
    RateLimitInfo,
    WorkflowRun,
)

logger = structlog.get_logger(__name__)

_RATE_LIMIT_WARN_THRESHOLD = 10


class GitHubClient:
    """Async HTTP client for the GitHub REST API v3.

    Handles authentication, pagination, rate-limit detection, and
    retries with exponential back-off + jitter.

    Usage::

        async with GitHubClient(settings, auth) as client:
            prs = await client.list_pull_requests("owner/repo")
    """

    def __init__(self, settings: GitHubSettings, auth: AuthProvider) -> None:
        self._settings = settings
        self._auth = auth
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers = await self._auth.get_headers()
            self._client = httpx.AsyncClient(
                base_url=self._settings.api_base_url,
                headers=headers,
                timeout=self._settings.timeout,
                http2=True,
                follow_redirects=True,
            )
        return self._client

    async def __aenter__(self) -> GitHubClient:
        await self._get_client()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _raise_for_status(self, response: httpx.Response) -> None:
        """Map HTTP error codes to typed exceptions."""
        if response.is_success:
            return
        request_id = response.headers.get("X-GitHub-Request-Id")
        body = response.text
        status = response.status_code

        if status == 401:
            raise GitHubAuthError("Unauthorized", status, body, request_id)
        if status == 403:
            remaining = int(response.headers.get("X-RateLimit-Remaining", "1"))
            if remaining == 0:
                reset_at = int(response.headers.get("X-RateLimit-Reset", "0"))
                raise GitHubRateLimitError(
                    "Rate limit exhausted",
                    reset_at=reset_at,
                    status_code=status,
                    response_body=body,
                    request_id=request_id,
                )
            raise GitHubAuthError("Forbidden", status, body, request_id)
        if status == 404:
            raise GitHubNotFoundError("Not found", status, body, request_id)
        if status == 422:
            raise GitHubValidationError("Unprocessable entity", status, body, request_id)
        if status >= 500:
            raise GitHubServerError(f"Server error {status}", status, body, request_id)
        raise GitHubAPIError(f"Unexpected status {status}", status, body, request_id)

    def _check_rate_limit_headers(self, response: httpx.Response) -> None:
        remaining_str = response.headers.get("X-RateLimit-Remaining")
        if remaining_str is None:
            return
        remaining = int(remaining_str)
        if remaining < _RATE_LIMIT_WARN_THRESHOLD:
            reset_at = response.headers.get("X-RateLimit-Reset", "?")
            logger.warning(
                "github_rate_limit_low",
                remaining=remaining,
                reset_at=reset_at,
            )

    async def _request(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> httpx.Response:
        """Execute a single request with retry decoration applied externally."""
        client = await self._get_client()
        t0 = time.monotonic()
        logger.debug("github_request_start", method=method, path=path)
        response = await client.request(method, path, **kwargs)
        elapsed = time.monotonic() - t0
        logger.debug(
            "github_request_done",
            method=method,
            path=path,
            status=response.status_code,
            elapsed_ms=round(elapsed * 1000),
        )
        self._check_rate_limit_headers(response)
        self._raise_for_status(response)
        return response

    def _make_retrying_request(self) -> Any:
        """Return a tenacity-wrapped version of ``_request``."""

        @retry(
            retry=retry_if_exception_type((httpx.TransportError, GitHubServerError)),
            wait=wait_exponential(multiplier=1, min=2, max=30) + wait_random(0, 2),
            stop=stop_after_attempt(self._settings.max_retries),
            before_sleep=before_sleep_log(logger, logging.WARNING),
            reraise=True,
        )
        async def _retrying(method: str, path: str, **kwargs: Any) -> httpx.Response:
            return await self._request(method, path, **kwargs)

        return _retrying

    async def _paginate(self, path: str, params: dict[str, Any]) -> AsyncIterator[list[dict[str, Any]]]:
        """Yield pages of results, following ``Link: rel="next"`` headers."""
        retrying = self._make_retrying_request()
        next_url: str | None = path
        while next_url:
            response = await retrying("GET", next_url, params=params)
            yield response.json()
            link = response.headers.get("Link", "")
            raw_next = _parse_next_link(link)
            if raw_next:
                # Parse the absolute next-page URL into path + params so httpx
                # resolves it cleanly against its base_url without URL-encoding
                # the query string when it is embedded directly in the path string.
                parsed = urlparse(raw_next)
                next_url = parsed.path
                params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            else:
                next_url = None
                params = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def list_pull_requests(
        self,
        repo: str,
        state: str = "all",
    ) -> list[PullRequest]:
        """List pull requests for a repository.

        Args:
            repo: Repository in ``owner/repo`` format.
            state: Filter by state: ``open``, ``closed``, or ``all``.

        Returns:
            List of :class:`PullRequest` models.
        """
        results: list[PullRequest] = []
        params: dict[str, Any] = {
            "state": state,
            "per_page": self._settings.per_page,
        }
        async for page in self._paginate(f"/repos/{repo}/pulls", params):
            for item in page:
                item["repo"] = repo
                results.append(PullRequest.model_validate(item))
        return results

    async def list_workflow_runs(
        self,
        repo: str,
        per_page: int = 100,
    ) -> list[WorkflowRun]:
        """List recent workflow runs for a repository.

        Args:
            repo: Repository in ``owner/repo`` format.
            per_page: Maximum results per page (GitHub max: 100).

        Returns:
            List of :class:`WorkflowRun` models.
        """
        results: list[WorkflowRun] = []
        params: dict[str, Any] = {"per_page": per_page}
        async for page in self._paginate(f"/repos/{repo}/actions/runs", params):
            items = page if isinstance(page, list) else page.get("workflow_runs", [])
            for item in items:
                item["repo"] = repo
                results.append(WorkflowRun.model_validate(item))
        return results

    async def list_codeql_alerts(self, repo: str) -> list[CodeQLAlert]:
        """List open Code Scanning (CodeQL) alerts for a repository.

        Args:
            repo: Repository in ``owner/repo`` format.

        Returns:
            List of :class:`CodeQLAlert` models.
        """
        results: list[CodeQLAlert] = []
        params: dict[str, Any] = {"per_page": self._settings.per_page}
        async for page in self._paginate(f"/repos/{repo}/code-scanning/alerts", params):
            for item in page:
                item["repo"] = repo
                results.append(CodeQLAlert.model_validate(item))
        return results

    async def list_dependabot_alerts(self, repo: str) -> list[DependabotAlert]:
        """List Dependabot vulnerability alerts for a repository.

        Args:
            repo: Repository in ``owner/repo`` format.

        Returns:
            List of :class:`DependabotAlert` models.
        """
        results: list[DependabotAlert] = []
        params: dict[str, Any] = {"per_page": self._settings.per_page}
        async for page in self._paginate(f"/repos/{repo}/dependabot/alerts", params):
            for item in page:
                item["repo"] = repo
                results.append(DependabotAlert.model_validate(item))
        return results

    async def get_rate_limit(self) -> RateLimitInfo:
        """Return current API rate limit status.

        Returns:
            :class:`RateLimitInfo` parsed from the ``/rate_limit`` endpoint.
        """
        retrying = self._make_retrying_request()
        response = await retrying("GET", "/rate_limit")
        data = response.json()["rate"]
        return RateLimitInfo.model_validate(data)


# ------------------------------------------------------------------
# Module-level helper
# ------------------------------------------------------------------


def _parse_next_link(link_header: str) -> str | None:
    """Extract the ``rel="next"`` URL from a GitHub ``Link`` header.

    Args:
        link_header: Raw value of the ``Link`` response header.

    Returns:
        The next-page URL, or ``None`` if there is no next page.
    """
    for part in link_header.split(","):
        part = part.strip()
        if 'rel="next"' in part:
            url_part = part.split(";")[0].strip()
            return url_part.strip("<>")
    return None
