from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.integrations.github.models import PullRequest, WorkflowRun
from app.integrations.github.service import GitHubService


def _make_pr(**kwargs) -> PullRequest:
    defaults = {
        "number": 1,
        "title": "test",
        "state": "closed",
        "draft": False,
        "additions": 10,
        "deletions": 2,
        "changed_files": 1,
        "review_comments": 0,
        "created_at": "2026-01-01T10:00:00Z",
        "merged_at": "2026-01-01T12:00:00Z",
        "closed_at": "2026-01-01T12:00:00Z",
        "user": "alice",
    }
    defaults.update(kwargs)
    return PullRequest.model_validate(defaults)


def _make_run(**kwargs) -> WorkflowRun:
    defaults = {
        "id": 1,
        "name": "CI",
        "status": "completed",
        "conclusion": "success",
        "created_at": "2026-01-01T10:00:00Z",
        "updated_at": "2026-01-01T10:07:00Z",
        "run_started_at": "2026-01-01T10:01:00Z",
        "head_branch": "main",
        "event": "push",
        "actor": "alice",
    }
    defaults.update(kwargs)
    return WorkflowRun.model_validate(defaults)


@pytest.fixture
def mock_client() -> AsyncMock:
    client = AsyncMock()
    client.list_pull_requests = AsyncMock(return_value=[])
    client.list_workflow_runs = AsyncMock(return_value=[])
    client.list_codeql_alerts = AsyncMock(return_value=[])
    client.list_dependabot_alerts = AsyncMock(return_value=[])
    return client


# ---------------------------------------------------------------------------
# collect_repo_metrics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestCollectRepoMetrics:
    async def test_returns_expected_keys(self, mock_settings, mock_client) -> None:
        service = GitHubService(mock_client, mock_settings)
        result = await service.collect_repo_metrics("owner/repo")

        assert set(result.keys()) == {
            "pull_requests",
            "workflow_runs",
            "codeql_alerts",
            "dependabot_alerts",
            "pr_metrics",
            "workflow_metrics",
            "collected_at",
        }

    async def test_pr_metrics_aggregation(self, mock_settings, mock_client) -> None:
        prs = [
            _make_pr(number=1, state="closed", merged_at="2026-01-01T14:00:00Z"),
            _make_pr(number=2, state="open", merged_at=None, closed_at=None),
        ]
        mock_client.list_pull_requests = AsyncMock(return_value=prs)
        service = GitHubService(mock_client, mock_settings)
        result = await service.collect_repo_metrics("owner/repo")

        metrics = result["pr_metrics"]
        assert metrics["open_count"] == 1
        assert metrics["merge_rate"] == 0.5
        assert metrics["avg_review_time_hours"] == 4.0

    async def test_workflow_metrics_aggregation(self, mock_settings, mock_client) -> None:
        runs = [
            _make_run(conclusion="success"),
            _make_run(conclusion="failure"),
        ]
        mock_client.list_workflow_runs = AsyncMock(return_value=runs)
        service = GitHubService(mock_client, mock_settings)
        result = await service.collect_repo_metrics("owner/repo")

        metrics = result["workflow_metrics"]
        assert metrics["total_runs"] == 2
        assert metrics["success_rate"] == 0.5


# ---------------------------------------------------------------------------
# collect_all_metrics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestCollectAllMetrics:
    async def test_collects_all_repos(self, mock_client) -> None:
        from pydantic import SecretStr

        from app.integrations.github.config import GitHubSettings

        settings = GitHubSettings(
            token=SecretStr("ghp_test"),
            repos=["owner/repo-a", "owner/repo-b"],
        )
        service = GitHubService(mock_client, settings)
        result = await service.collect_all_metrics()

        assert "owner/repo-a" in result["repos"]
        assert "owner/repo-b" in result["repos"]
        assert result["errors"] == {}

    async def test_one_repo_failure_does_not_block_others(self, mock_client) -> None:
        from pydantic import SecretStr

        from app.integrations.github.config import GitHubSettings

        settings = GitHubSettings(
            token=SecretStr("ghp_test"),
            repos=["owner/ok-repo", "owner/bad-repo"],
        )

        async def fake_collect(repo: str):
            if repo == "owner/bad-repo":
                raise RuntimeError("connection refused")
            return {
                "pr_metrics": {},
                "workflow_metrics": {},
                "collected_at": "x",
                "pull_requests": [],
                "workflow_runs": [],
                "codeql_alerts": [],
                "dependabot_alerts": [],
            }

        service = GitHubService(mock_client, settings)
        service.collect_repo_metrics = fake_collect  # type: ignore[assignment]
        result = await service.collect_all_metrics()

        assert "owner/ok-repo" in result["repos"]
        assert "owner/bad-repo" in result["errors"]
        assert "connection refused" in result["errors"]["owner/bad-repo"]


# ---------------------------------------------------------------------------
# _compute_pr_metrics
# ---------------------------------------------------------------------------


class TestComputePRMetrics:
    def _service(self, mock_settings, mock_client):
        return GitHubService(mock_client, mock_settings)

    def test_empty_list(self, mock_settings, mock_client) -> None:
        s = self._service(mock_settings, mock_client)
        result = s._compute_pr_metrics([])
        assert result == {"open_count": 0, "avg_review_time_hours": 0.0, "merge_rate": 0.0}

    def test_all_open(self, mock_settings, mock_client) -> None:
        prs = [_make_pr(state="open", merged_at=None, closed_at=None) for _ in range(3)]
        s = self._service(mock_settings, mock_client)
        result = s._compute_pr_metrics(prs)
        assert result["open_count"] == 3
        assert result["merge_rate"] == 0.0

    def test_all_merged(self, mock_settings, mock_client) -> None:
        prs = [_make_pr(state="closed", merged_at="2026-01-01T12:00:00Z") for _ in range(3)]
        s = self._service(mock_settings, mock_client)
        result = s._compute_pr_metrics(prs)
        assert result["open_count"] == 0
        assert result["merge_rate"] == 1.0


# ---------------------------------------------------------------------------
# _compute_workflow_metrics
# ---------------------------------------------------------------------------


class TestComputeWorkflowMetrics:
    def _service(self, mock_settings, mock_client):
        return GitHubService(mock_client, mock_settings)

    def test_empty_list(self, mock_settings, mock_client) -> None:
        s = self._service(mock_settings, mock_client)
        result = s._compute_workflow_metrics([])
        assert result == {"total_runs": 0, "success_rate": 0.0, "avg_duration_minutes": 0.0}

    def test_all_successful(self, mock_settings, mock_client) -> None:
        runs = [_make_run(conclusion="success") for _ in range(4)]
        s = self._service(mock_settings, mock_client)
        result = s._compute_workflow_metrics(runs)
        assert result["success_rate"] == 1.0
        assert result["total_runs"] == 4
