"""Testes do GitHubCollectorClient."""

from __future__ import annotations

from datetime import UTC

import httpx
import pytest
import respx

from app.collectors.github.client import GitHubCollectorClient
from app.integrations.github.exceptions import (
    GitHubAuthError,
    GitHubRateLimitError,
    GitHubServerError,
)
from tests.collectors.github.conftest import _GITHUB_API, _REPO, rl_headers

_PULLS_URL = f"{_GITHUB_API}/repos/{_REPO}/pulls"
_RUNS_URL = f"{_GITHUB_API}/repos/{_REPO}/actions/runs"

_SINCE_OLD = "2020-01-01T00:00:00+00:00"  # cutoff passada — aceita tudo


@pytest.mark.asyncio
class TestListPullsSince:
    async def test_returns_pulls_within_window(self, settings, pulls_fixture) -> None:
        with respx.mock:
            respx.get(_PULLS_URL).mock(return_value=httpx.Response(200, json=pulls_fixture, headers=rl_headers()))
            async with GitHubCollectorClient(settings) as client:
                from datetime import datetime

                since = datetime(2020, 1, 1, tzinfo=UTC)
                result = await client.list_pulls_since(_REPO, since)

        assert len(result) == 2
        assert result[0]["number"] == 42

    async def test_pagination_follows_link_header(self, settings) -> None:
        """Segue Link: rel=next até o fim."""
        page1 = [{"number": 1, "updated_at": "2026-05-30T10:00:00Z", "state": "open"}]
        page2 = [{"number": 2, "updated_at": "2026-05-30T09:00:00Z", "state": "closed"}]

        call_count = 0

        def side_effect(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                next_url = f"{_GITHUB_API}/repos/{_REPO}/pulls?page=2"
                return httpx.Response(
                    200,
                    json=page1,
                    headers={**rl_headers(), "Link": f'<{next_url}>; rel="next"'},
                )
            return httpx.Response(200, json=page2, headers=rl_headers())

        with respx.mock:
            respx.get(_PULLS_URL).mock(side_effect=side_effect)
            respx.get(f"{_PULLS_URL}?page=2").mock(side_effect=side_effect)
            async with GitHubCollectorClient(settings) as client:
                from datetime import datetime

                since = datetime(2020, 1, 1, tzinfo=UTC)
                result = await client.list_pulls_since(_REPO, since)

        assert len(result) == 2
        assert call_count == 2

    async def test_early_exit_when_item_before_since(self, settings) -> None:
        """Para de paginar quando encontra item anterior ao cutoff."""
        recent = "2026-05-30T10:00:00Z"
        old = "2026-05-20T10:00:00Z"
        page = [
            {"number": 10, "updated_at": recent},
            {"number": 9, "updated_at": old},
        ]
        with respx.mock:
            respx.get(_PULLS_URL).mock(return_value=httpx.Response(200, json=page, headers=rl_headers()))
            async with GitHubCollectorClient(settings) as client:
                from datetime import datetime

                since = datetime(2026, 5, 25, tzinfo=UTC)
                result = await client.list_pulls_since(_REPO, since)

        assert len(result) == 1
        assert result[0]["number"] == 10

    async def test_rate_limit_raises_not_retried(self, settings) -> None:
        """403 com Remaining=0 levanta GitHubRateLimitError sem retry."""
        with respx.mock:
            respx.get(_PULLS_URL).mock(
                return_value=httpx.Response(
                    403,
                    json={"message": "rate limit exceeded"},
                    headers={
                        "X-RateLimit-Remaining": "0",
                        "X-RateLimit-Reset": "9999999999",
                    },
                )
            )
            async with GitHubCollectorClient(settings) as client:
                from datetime import datetime

                since = datetime(2020, 1, 1, tzinfo=UTC)
                with pytest.raises(GitHubRateLimitError):
                    await client.list_pulls_since(_REPO, since)

    async def test_retry_on_502(self, settings) -> None:
        """502 é retried; segundo request retorna sucesso."""
        call_count = 0

        def side_effect(_req: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(502, text="Bad Gateway")
            return httpx.Response(200, json=[], headers=rl_headers())

        with respx.mock:
            respx.get(_PULLS_URL).mock(side_effect=side_effect)
            async with GitHubCollectorClient(settings) as client:
                from datetime import datetime

                since = datetime(2020, 1, 1, tzinfo=UTC)
                result = await client.list_pulls_since(_REPO, since)

        assert call_count == 2
        assert result == []

    async def test_no_retry_on_401(self, settings) -> None:
        """401 levanta GitHubAuthError sem retry."""
        with respx.mock:
            respx.get(_PULLS_URL).mock(
                return_value=httpx.Response(401, json={"message": "Bad credentials"}, headers=rl_headers())
            )
            async with GitHubCollectorClient(settings) as client:
                from datetime import datetime

                since = datetime(2020, 1, 1, tzinfo=UTC)
                with pytest.raises(GitHubAuthError):
                    await client.list_pulls_since(_REPO, since)

    async def test_no_retry_on_404(self, settings) -> None:
        """404 levanta GitHubNotFoundError sem retry."""
        from app.integrations.github.exceptions import GitHubNotFoundError

        with respx.mock:
            respx.get(_PULLS_URL).mock(
                return_value=httpx.Response(404, json={"message": "Not Found"}, headers=rl_headers())
            )
            async with GitHubCollectorClient(settings) as client:
                from datetime import datetime

                since = datetime(2020, 1, 1, tzinfo=UTC)
                with pytest.raises(GitHubNotFoundError):
                    await client.list_pulls_since(_REPO, since)


@pytest.mark.asyncio
class TestListWorkflowRunsSince:
    async def test_unwraps_workflow_runs_key(self, settings, workflow_runs_fixture) -> None:
        with respx.mock:
            respx.get(_RUNS_URL).mock(
                return_value=httpx.Response(200, json=workflow_runs_fixture, headers=rl_headers())
            )
            async with GitHubCollectorClient(settings) as client:
                from datetime import datetime

                since = datetime(2020, 1, 1, tzinfo=UTC)
                result = await client.list_workflow_runs_since(_REPO, since)

        assert len(result) == 2
        assert result[0]["id"] == 9001

    async def test_no_retry_on_502_exhausted(self, settings) -> None:
        """Esgota retries em 502 e levanta GitHubServerError."""
        with respx.mock:
            respx.get(_RUNS_URL).mock(return_value=httpx.Response(502, text="Bad Gateway"))
            async with GitHubCollectorClient(settings) as client:
                from datetime import datetime

                since = datetime(2020, 1, 1, tzinfo=UTC)
                with pytest.raises(GitHubServerError):
                    await client.list_workflow_runs_since(_REPO, since)
