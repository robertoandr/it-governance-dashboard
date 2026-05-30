from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import structlog

from app.integrations.github.client import GitHubClient
from app.integrations.github.config import GitHubSettings
from app.integrations.github.models import PullRequest, WorkflowRun

logger = structlog.get_logger(__name__)


class GitHubService:
    """Orchestrates metric collection across all configured repositories.

    Collects PRs, workflow runs, CodeQL alerts, and Dependabot alerts
    in parallel. A failure in one repository does not prevent collection
    from others.

    Args:
        client: Authenticated :class:`GitHubClient` instance.
        settings: :class:`GitHubSettings` with the repo list.
    """

    def __init__(self, client: GitHubClient, settings: GitHubSettings) -> None:
        self._client = client
        self._settings = settings

    async def collect_all_metrics(self) -> dict[str, Any]:
        """Collect metrics for all configured repositories in parallel.

        Returns:
            Mapping of ``repo`` → metric dict. Repos that fail are
            recorded under the ``errors`` key.
        """
        tasks = [self.collect_repo_metrics(repo) for repo in self._settings.repos]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        output: dict[str, Any] = {"repos": {}, "errors": {}}
        for repo, result in zip(self._settings.repos, results, strict=True):
            if isinstance(result, Exception):
                logger.error(
                    "github_repo_collection_failed",
                    repo=repo,
                    error=str(result),
                    exc_info=result,
                )
                output["errors"][repo] = str(result)
            else:
                output["repos"][repo] = result

        logger.info(
            "github_collection_complete",
            repos_ok=len(output["repos"]),
            repos_failed=len(output["errors"]),
        )
        return output

    async def collect_repo_metrics(self, repo: str) -> dict[str, Any]:
        """Collect all metric types for a single repository in parallel.

        Args:
            repo: Repository in ``owner/repo`` format.

        Returns:
            Dict with keys ``pull_requests``, ``workflow_runs``,
            ``codeql_alerts``, ``dependabot_alerts``, and computed
            aggregations.
        """
        logger.info("github_collecting_repo", repo=repo)
        prs, runs, codeql, dependabot = await asyncio.gather(
            self._client.list_pull_requests(repo),
            self._client.list_workflow_runs(repo),
            self._client.list_codeql_alerts(repo),
            self._client.list_dependabot_alerts(repo),
        )
        return {
            "pull_requests": [pr.model_dump() for pr in prs],
            "workflow_runs": [r.model_dump() for r in runs],
            "codeql_alerts": [a.model_dump() for a in codeql],
            "dependabot_alerts": [a.model_dump() for a in dependabot],
            "pr_metrics": self._compute_pr_metrics(prs),
            "workflow_metrics": self._compute_workflow_metrics(runs),
            "collected_at": datetime.now(tz=UTC).isoformat(),
        }

    def _compute_pr_metrics(self, prs: list[PullRequest]) -> dict[str, Any]:
        """Compute aggregate PR statistics.

        Args:
            prs: List of pull requests from the repository.

        Returns:
            Dict with ``open_count``, ``avg_review_time_hours``, and
            ``merge_rate``.
        """
        if not prs:
            return {"open_count": 0, "avg_review_time_hours": 0.0, "merge_rate": 0.0}

        open_count = sum(1 for pr in prs if pr.state == "open")
        merged = [pr for pr in prs if pr.merged_at is not None]
        merge_rate = len(merged) / len(prs)

        review_times: list[float] = []
        for pr in merged:
            if pr.merged_at and pr.created_at:
                delta = pr.merged_at - pr.created_at
                review_times.append(delta.total_seconds() / 3600)

        avg_review_time = sum(review_times) / len(review_times) if review_times else 0.0

        return {
            "open_count": open_count,
            "avg_review_time_hours": round(avg_review_time, 2),
            "merge_rate": round(merge_rate, 4),
        }

    def _compute_workflow_metrics(self, runs: list[WorkflowRun]) -> dict[str, Any]:
        """Compute aggregate CI/CD workflow statistics.

        Args:
            runs: List of workflow runs from the repository.

        Returns:
            Dict with ``total_runs``, ``success_rate``, and
            ``avg_duration_minutes`` (where available).
        """
        if not runs:
            return {"total_runs": 0, "success_rate": 0.0, "avg_duration_minutes": 0.0}

        completed = [r for r in runs if r.conclusion is not None]
        successful = [r for r in completed if r.conclusion == "success"]
        success_rate = len(successful) / len(completed) if completed else 0.0

        durations: list[float] = []
        for run in completed:
            if run.run_started_at and run.updated_at:
                delta = run.updated_at - run.run_started_at
                durations.append(delta.total_seconds() / 60)

        avg_duration = sum(durations) / len(durations) if durations else 0.0

        return {
            "total_runs": len(runs),
            "success_rate": round(success_rate, 4),
            "avg_duration_minutes": round(avg_duration, 2),
        }
