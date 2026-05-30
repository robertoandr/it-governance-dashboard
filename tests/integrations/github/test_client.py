from __future__ import annotations

import httpx
import pytest
import respx

from app.integrations.github.client import GitHubClient, _parse_next_link
from app.integrations.github.exceptions import (
    GitHubAuthError,
    GitHubNotFoundError,
    GitHubRateLimitError,
    GitHubValidationError,
)
from app.integrations.github.models import (
    CodeQLAlert,
    DependabotAlert,
    PullRequest,
    RateLimitInfo,
    WorkflowRun,
)

# ---------------------------------------------------------------------------
# _parse_next_link
# ---------------------------------------------------------------------------


class TestParseNextLink:
    def test_returns_url_when_next_present(self) -> None:
        header = '<https://api.github.com/repos/o/r/pulls?page=2>; rel="next", <https://api.github.com/repos/o/r/pulls?page=5>; rel="last"'
        assert _parse_next_link(header) == "https://api.github.com/repos/o/r/pulls?page=2"

    def test_returns_none_when_no_next(self) -> None:
        header = '<https://api.github.com/repos/o/r/pulls?page=1>; rel="prev"'
        assert _parse_next_link(header) is None

    def test_returns_none_on_empty_header(self) -> None:
        assert _parse_next_link("") is None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rl_headers(remaining: int = 5000, limit: int = 5000, reset: int = 9999999999) -> dict:
    return {
        "X-RateLimit-Limit": str(limit),
        "X-RateLimit-Remaining": str(remaining),
        "X-RateLimit-Reset": str(reset),
        "X-RateLimit-Used": "0",
    }


# ---------------------------------------------------------------------------
# list_pull_requests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestListPullRequests:
    async def test_happy_path(self, mock_settings, mock_auth, prs_fixture) -> None:
        with respx.mock(base_url="https://api.github.com", assert_all_called=False) as mock:
            mock.get("/repos/owner/repo/pulls").mock(
                return_value=httpx.Response(200, json=prs_fixture, headers=_rl_headers())
            )
            async with GitHubClient(mock_settings, mock_auth) as client:
                prs = await client.list_pull_requests("owner/repo")

        assert len(prs) == 2
        assert all(isinstance(pr, PullRequest) for pr in prs)
        assert prs[0].number == 1
        assert prs[0].user_login == "alice"
        assert prs[0].repo == "owner/repo"
        assert prs[1].state == "open"

    async def test_pagination_follows_link_header(self, mock_settings, mock_auth, prs_fixture) -> None:
        page1 = prs_fixture[:1]
        page2 = prs_fixture[1:]
        with respx.mock(base_url="https://api.github.com", assert_all_called=False) as mock:
            # Register the specific page=2 route FIRST so respx matches it before
            # the catch-all route below (respx uses FIFO ordering).
            mock.get("/repos/owner/repo/pulls", params={"page": "2"}).mock(
                return_value=httpx.Response(200, json=page2, headers=_rl_headers())
            )
            # Catch-all: first page (no page= param) → returns Link: next header
            mock.get("/repos/owner/repo/pulls").mock(
                return_value=httpx.Response(
                    200,
                    json=page1,
                    headers={
                        **_rl_headers(),
                        "Link": '<https://api.github.com/repos/owner/repo/pulls?page=2>; rel="next"',
                    },
                )
            )
            async with GitHubClient(mock_settings, mock_auth) as client:
                prs = await client.list_pull_requests("owner/repo")

        assert len(prs) == 2

    async def test_rate_limit_warning_logged(self, mock_settings, mock_auth, prs_fixture, caplog) -> None:
        import logging

        with respx.mock(base_url="https://api.github.com", assert_all_called=False) as mock:
            mock.get("/repos/owner/repo/pulls").mock(
                return_value=httpx.Response(
                    200,
                    json=prs_fixture,
                    headers=_rl_headers(remaining=5),
                )
            )
            async with GitHubClient(mock_settings, mock_auth) as client:
                with caplog.at_level(logging.WARNING):
                    await client.list_pull_requests("owner/repo")

    async def test_rate_limit_exhausted_raises(self, mock_settings, mock_auth) -> None:
        with respx.mock(base_url="https://api.github.com", assert_all_called=False) as mock:
            mock.get("/repos/owner/repo/pulls").mock(
                return_value=httpx.Response(
                    403,
                    json={"message": "API rate limit exceeded"},
                    headers=_rl_headers(remaining=0),
                )
            )
            async with GitHubClient(mock_settings, mock_auth) as client:
                with pytest.raises(GitHubRateLimitError):
                    await client.list_pull_requests("owner/repo")

    async def test_retries_on_500_then_succeeds(self, mock_settings, mock_auth, prs_fixture) -> None:
        call_count = 0

        def side_effect(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                return httpx.Response(500, json={"message": "Internal Server Error"})
            return httpx.Response(200, json=prs_fixture, headers=_rl_headers())

        with respx.mock(base_url="https://api.github.com", assert_all_called=False) as mock:
            mock.get("/repos/owner/repo/pulls").mock(side_effect=side_effect)
            async with GitHubClient(mock_settings, mock_auth) as client:
                prs = await client.list_pull_requests("owner/repo")

        assert len(prs) == 2
        assert call_count == 2

    async def test_401_raises_auth_error(self, mock_settings, mock_auth) -> None:
        with respx.mock(base_url="https://api.github.com", assert_all_called=False) as mock:
            mock.get("/repos/owner/repo/pulls").mock(
                return_value=httpx.Response(401, json={"message": "Bad credentials"})
            )
            async with GitHubClient(mock_settings, mock_auth) as client:
                with pytest.raises(GitHubAuthError) as exc_info:
                    await client.list_pull_requests("owner/repo")
        assert exc_info.value.status_code == 401

    async def test_403_without_rate_limit_raises_auth_error(self, mock_settings, mock_auth) -> None:
        with respx.mock(base_url="https://api.github.com", assert_all_called=False) as mock:
            mock.get("/repos/owner/repo/pulls").mock(
                return_value=httpx.Response(
                    403,
                    json={"message": "Forbidden"},
                    headers=_rl_headers(remaining=100),
                )
            )
            async with GitHubClient(mock_settings, mock_auth) as client:
                with pytest.raises(GitHubAuthError):
                    await client.list_pull_requests("owner/repo")

    async def test_404_raises_not_found(self, mock_settings, mock_auth) -> None:
        with respx.mock(base_url="https://api.github.com", assert_all_called=False) as mock:
            mock.get("/repos/owner/repo/pulls").mock(return_value=httpx.Response(404, json={"message": "Not Found"}))
            async with GitHubClient(mock_settings, mock_auth) as client:
                with pytest.raises(GitHubNotFoundError):
                    await client.list_pull_requests("owner/repo")

    async def test_422_raises_validation_error(self, mock_settings, mock_auth) -> None:
        with respx.mock(base_url="https://api.github.com", assert_all_called=False) as mock:
            mock.get("/repos/owner/repo/pulls").mock(
                return_value=httpx.Response(422, json={"message": "Validation Failed"})
            )
            async with GitHubClient(mock_settings, mock_auth) as client:
                with pytest.raises(GitHubValidationError):
                    await client.list_pull_requests("owner/repo")


# ---------------------------------------------------------------------------
# list_workflow_runs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestListWorkflowRuns:
    async def test_happy_path(self, mock_settings, mock_auth, workflows_fixture) -> None:
        with respx.mock(base_url="https://api.github.com", assert_all_called=False) as mock:
            mock.get("/repos/owner/repo/actions/runs").mock(
                return_value=httpx.Response(200, json=workflows_fixture, headers=_rl_headers())
            )
            async with GitHubClient(mock_settings, mock_auth) as client:
                runs = await client.list_workflow_runs("owner/repo")

        assert len(runs) == 2
        assert all(isinstance(r, WorkflowRun) for r in runs)
        assert runs[0].conclusion == "success"
        assert runs[0].actor_login == "alice"


# ---------------------------------------------------------------------------
# list_codeql_alerts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestListCodeQLAlerts:
    async def test_happy_path(self, mock_settings, mock_auth, codeql_fixture) -> None:
        with respx.mock(base_url="https://api.github.com", assert_all_called=False) as mock:
            mock.get("/repos/owner/repo/code-scanning/alerts").mock(
                return_value=httpx.Response(200, json=codeql_fixture, headers=_rl_headers())
            )
            async with GitHubClient(mock_settings, mock_auth) as client:
                alerts = await client.list_codeql_alerts("owner/repo")

        assert len(alerts) == 1
        assert isinstance(alerts[0], CodeQLAlert)
        assert alerts[0].rule_id == "py/sql-injection"
        assert alerts[0].rule_security_severity_level == "high"
        assert alerts[0].tool_name == "CodeQL"


# ---------------------------------------------------------------------------
# list_dependabot_alerts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestListDependabotAlerts:
    async def test_happy_path(self, mock_settings, mock_auth, dependabot_fixture) -> None:
        with respx.mock(base_url="https://api.github.com", assert_all_called=False) as mock:
            mock.get("/repos/owner/repo/dependabot/alerts").mock(
                return_value=httpx.Response(200, json=dependabot_fixture, headers=_rl_headers())
            )
            async with GitHubClient(mock_settings, mock_auth) as client:
                alerts = await client.list_dependabot_alerts("owner/repo")

        assert len(alerts) == 1
        assert isinstance(alerts[0], DependabotAlert)
        assert alerts[0].dependency_name == "requests"
        assert alerts[0].severity == "high"
        assert alerts[0].cvss_score == 8.1


# ---------------------------------------------------------------------------
# get_rate_limit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestGetRateLimit:
    async def test_happy_path(self, mock_settings, mock_auth) -> None:
        payload = {
            "rate": {
                "limit": 5000,
                "remaining": 4999,
                "reset": 9999999999,
                "used": 1,
            }
        }
        with respx.mock(base_url="https://api.github.com", assert_all_called=False) as mock:
            mock.get("/rate_limit").mock(return_value=httpx.Response(200, json=payload, headers=_rl_headers()))
            async with GitHubClient(mock_settings, mock_auth) as client:
                info = await client.get_rate_limit()

        assert isinstance(info, RateLimitInfo)
        assert info.limit == 5000
        assert info.remaining == 4999
