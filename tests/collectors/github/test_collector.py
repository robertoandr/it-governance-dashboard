"""Testes de integração do GitHubCollector."""

from __future__ import annotations

import httpx
import pytest
import respx
from pydantic import SecretStr

from app.collectors.github.client import GitHubCollectorClient
from app.collectors.github.collector import GitHubCollector
from tests.collectors.github.conftest import (
    _GITHUB_API,
    _LOOKBACK_HOURS_TEST,
    _REPO,
    rl_headers,
)

_PULLS_URL = f"{_GITHUB_API}/repos/{_REPO}/pulls"
_RUNS_URL = f"{_GITHUB_API}/repos/{_REPO}/actions/runs"


def _make_collector(settings, mock_influx) -> tuple[GitHubCollectorClient, GitHubCollector]:
    client = GitHubCollectorClient(settings)
    collector = GitHubCollector(client=client, influx=mock_influx, settings=settings)
    return client, collector


@pytest.mark.asyncio
class TestGitHubCollectorE2E:
    async def test_e2e_success_writes_both_measurements(
        self, settings, mock_influx, pulls_fixture, workflow_runs_fixture
    ) -> None:
        with respx.mock:
            respx.get(_PULLS_URL).mock(return_value=httpx.Response(200, json=pulls_fixture, headers=rl_headers()))
            respx.get(_RUNS_URL).mock(
                return_value=httpx.Response(200, json=workflow_runs_fixture, headers=rl_headers())
            )
            client, collector = _make_collector(settings, mock_influx)
            async with client:
                count = await collector.collect()

        assert count == 4  # 2 PRs + 2 workflow runs
        mock_influx.write_points.assert_awaited_once()
        points = mock_influx.write_points.call_args[0][0]
        measurements = {p.measurement for p in points}
        assert "gov_github_pr" in measurements
        assert "gov_github_workflow" in measurements

    async def test_empty_repo_no_write(self, settings, mock_influx) -> None:
        with respx.mock:
            respx.get(_PULLS_URL).mock(return_value=httpx.Response(200, json=[], headers=rl_headers()))
            respx.get(_RUNS_URL).mock(
                return_value=httpx.Response(200, json={"workflow_runs": []}, headers=rl_headers())
            )
            client, collector = _make_collector(settings, mock_influx)
            async with client:
                count = await collector.collect()

        assert count == 0
        mock_influx.write_points.assert_not_awaited()

    async def test_multi_repo_tags_correct(self, mock_influx, workflow_runs_fixture) -> None:
        from pydantic import SecretStr

        from app.collectors.github.config import GitHubCollectorSettings

        settings_multi = GitHubCollectorSettings(
            token=SecretStr("ghp_test"),
            repos=["owner/repo-a", "owner/repo-b"],
            lookback_hours=24,
        )

        pulls_a = [
            {
                "number": 1,
                "title": "PR in A",
                "state": "open",
                "user": {"login": "alice", "id": 1},
                "base": {"ref": "main"},
                "created_at": "2026-05-30T08:00:00Z",
                "updated_at": "2026-05-30T08:00:00Z",
            }
        ]
        pulls_b = [
            {
                "number": 2,
                "title": "PR in B",
                "state": "open",
                "user": {"login": "bob", "id": 2},
                "base": {"ref": "develop"},
                "created_at": "2026-05-30T09:00:00Z",
                "updated_at": "2026-05-30T09:00:00Z",
            }
        ]

        with respx.mock:
            respx.get(f"{_GITHUB_API}/repos/owner/repo-a/pulls").mock(
                return_value=httpx.Response(200, json=pulls_a, headers=rl_headers())
            )
            respx.get(f"{_GITHUB_API}/repos/owner/repo-a/actions/runs").mock(
                return_value=httpx.Response(200, json={"workflow_runs": []}, headers=rl_headers())
            )
            respx.get(f"{_GITHUB_API}/repos/owner/repo-b/pulls").mock(
                return_value=httpx.Response(200, json=pulls_b, headers=rl_headers())
            )
            respx.get(f"{_GITHUB_API}/repos/owner/repo-b/actions/runs").mock(
                return_value=httpx.Response(200, json={"workflow_runs": []}, headers=rl_headers())
            )

            client = GitHubCollectorClient(settings_multi)
            collector = GitHubCollector(client=client, influx=mock_influx, settings=settings_multi)
            async with client:
                count = await collector.collect()

        assert count == 2
        points = mock_influx.write_points.call_args[0][0]
        repos = {p.tags["repo"] for p in points}
        assert repos == {"repo-a", "repo-b"}

    async def test_partial_failure_continues_other_repos(self, mock_influx, pulls_fixture) -> None:
        """Se um repo falhar com 500, os outros repos continuam sendo coletados."""
        from app.collectors.github.config import GitHubCollectorSettings

        settings_multi = GitHubCollectorSettings(
            token=SecretStr("ghp_test"),
            repos=["owner/repo-fail", "owner/repo-ok"],
            lookback_hours=24,
        )
        pulls_ok = [
            {
                "number": 10,
                "title": "OK PR",
                "state": "open",
                "user": {"login": "dev", "id": 1},
                "base": {"ref": "main"},
                "created_at": "2026-05-30T08:00:00Z",
                "updated_at": "2026-05-30T08:00:00Z",
            }
        ]

        with respx.mock:
            # repo-fail retorna 500
            respx.get(f"{_GITHUB_API}/repos/owner/repo-fail/pulls").mock(
                return_value=httpx.Response(500, text="Internal Server Error")
            )
            # repo-ok retorna dados válidos
            respx.get(f"{_GITHUB_API}/repos/owner/repo-ok/pulls").mock(
                return_value=httpx.Response(200, json=pulls_ok, headers=rl_headers())
            )
            respx.get(f"{_GITHUB_API}/repos/owner/repo-ok/actions/runs").mock(
                return_value=httpx.Response(200, json={"workflow_runs": []}, headers=rl_headers())
            )

            client = GitHubCollectorClient(settings_multi)
            collector = GitHubCollector(client=client, influx=mock_influx, settings=settings_multi)
            async with client:
                count = await collector.collect()

        # repo-ok foi coletado apesar de repo-fail ter falhado
        assert count == 1
        points = mock_influx.write_points.call_args[0][0]
        assert points[0].tags["repo"] == "repo-ok"

    async def test_transform_invalid_item_skipped(self, settings, mock_influx) -> None:
        """Item inválido no transform é descartado sem interromper os demais."""
        envelopes = [
            {"type": "pr", "data": {"number": "not-an-int"}, "repo": "test-repo"},  # inválido
            {
                "type": "workflow",
                "data": {
                    "id": 9001,
                    "name": "CI",
                    "head_branch": "main",
                    "event": "push",
                    "status": "completed",
                    "conclusion": "success",
                    "run_started_at": "2026-05-30T08:00:00Z",
                    "updated_at": "2026-05-30T08:10:00Z",
                    "run_attempt": 1,
                },
                "repo": "test-repo",
            },
        ]
        _, collector = _make_collector(settings, mock_influx)
        points = collector.transform(envelopes)
        assert len(points) == 1
        assert points[0].measurement == "gov_github_workflow"

    async def test_pr_points_measurement_and_fields(self, settings, mock_influx, pulls_fixture) -> None:
        with respx.mock:
            respx.get(_PULLS_URL).mock(return_value=httpx.Response(200, json=pulls_fixture, headers=rl_headers()))
            respx.get(_RUNS_URL).mock(
                return_value=httpx.Response(200, json={"workflow_runs": []}, headers=rl_headers())
            )
            client, collector = _make_collector(settings, mock_influx)
            async with client:
                await collector.collect()

        points = mock_influx.write_points.call_args[0][0]
        pr_points = [p for p in points if p.measurement == "gov_github_pr"]
        assert len(pr_points) == 2
        open_pr = next(p for p in pr_points if p.tags["state"] == "open")
        assert open_pr.tags["author"] == "dev-alice"
        assert open_pr.tags["target_branch"] == "main"

    async def test_merged_pr_correct_state(self, settings, mock_influx, pulls_fixture) -> None:
        with respx.mock:
            respx.get(_PULLS_URL).mock(return_value=httpx.Response(200, json=pulls_fixture, headers=rl_headers()))
            respx.get(_RUNS_URL).mock(
                return_value=httpx.Response(200, json={"workflow_runs": []}, headers=rl_headers())
            )
            client, collector = _make_collector(settings, mock_influx)
            async with client:
                await collector.collect()

        points = mock_influx.write_points.call_args[0][0]
        merged = next((p for p in points if p.tags.get("state") == "merged"), None)
        assert merged is not None
        assert merged.fields["time_to_merge_seconds"] > 0

    async def test_partial_failure_does_not_raise(self, mock_influx) -> None:
        """Partial failure (1 de N repos falha) → não levanta exceção."""
        from app.collectors.github.config import GitHubCollectorSettings

        settings_multi = GitHubCollectorSettings(
            token=SecretStr("ghp_test"),
            repos=["owner/repo-fail", "owner/repo-ok"],
            lookback_hours=_LOOKBACK_HOURS_TEST,
        )
        pulls_ok = [
            {
                "number": 5,
                "title": "Surviving PR",
                "state": "open",
                "user": {"login": "dev", "id": 1},
                "base": {"ref": "main"},
                "created_at": "2026-05-30T08:00:00Z",
                "updated_at": "2026-05-30T08:00:00Z",
            }
        ]
        with respx.mock:
            respx.get(f"{_GITHUB_API}/repos/owner/repo-fail/pulls").mock(
                return_value=httpx.Response(500, text="Server Error")
            )
            respx.get(f"{_GITHUB_API}/repos/owner/repo-ok/pulls").mock(
                return_value=httpx.Response(200, json=pulls_ok, headers=rl_headers())
            )
            respx.get(f"{_GITHUB_API}/repos/owner/repo-ok/actions/runs").mock(
                return_value=httpx.Response(200, json={"workflow_runs": []}, headers=rl_headers())
            )
            client = GitHubCollectorClient(settings_multi)
            collector = GitHubCollector(client=client, influx=mock_influx, settings=settings_multi)
            async with client:
                count = await collector.collect()  # não deve levantar

        assert count == 1

    async def test_total_failure_raises(self, mock_influx) -> None:
        """Total failure (todos os repos falham) → GitHubCollectorError."""
        from app.collectors.github.config import GitHubCollectorSettings
        from app.collectors.github.exceptions import GitHubCollectorError

        settings_all_fail = GitHubCollectorSettings(
            token=SecretStr("ghp_test"),
            repos=["owner/repo-a", "owner/repo-b"],
            lookback_hours=_LOOKBACK_HOURS_TEST,
        )
        with respx.mock:
            respx.get(f"{_GITHUB_API}/repos/owner/repo-a/pulls").mock(
                return_value=httpx.Response(401, json={"message": "Bad credentials"}, headers=rl_headers())
            )
            respx.get(f"{_GITHUB_API}/repos/owner/repo-b/pulls").mock(
                return_value=httpx.Response(401, json={"message": "Bad credentials"}, headers=rl_headers())
            )
            client = GitHubCollectorClient(settings_all_fail)
            collector = GitHubCollector(client=client, influx=mock_influx, settings=settings_all_fail)
            async with client:
                with pytest.raises(GitHubCollectorError) as exc_info:
                    await collector.collect()

        assert len(exc_info.value.repos) == 2
        assert "owner/repo-a" in exc_info.value.errors
        assert "owner/repo-b" in exc_info.value.errors
        mock_influx.write_points.assert_not_awaited()
