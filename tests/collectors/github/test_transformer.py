"""Testes do transformer GitHub → GovernancePoint."""

from __future__ import annotations

from datetime import UTC, datetime

from app.collectors.github.models import GitHubPR, GitHubWorkflowRun
from app.collectors.github.transformer import pr_to_point, workflow_to_point

_REPO = "test-repo"
_UTC = UTC


def _make_pr(**overrides) -> GitHubPR:
    defaults = {
        "number": 1,
        "title": "feat: test",
        "state": "open",
        "merged": False,
        "user": {"login": "dev-alice", "id": 1},
        "base_ref": "main",
        "created_at": datetime(2026, 5, 28, 10, 0, 0, tzinfo=_UTC),
        "merged_at": None,
        "additions": 10,
        "deletions": 2,
        "changed_files": 1,
        "review_comments": 0,
    }
    return GitHubPR(**{**defaults, **overrides})


def _make_run(**overrides) -> GitHubWorkflowRun:
    defaults = {
        "id": 9001,
        "name": "CI",
        "head_branch": "main",
        "event": "push",
        "status": "completed",
        "conclusion": "success",
        "run_started_at": datetime(2026, 5, 30, 8, 0, 0, tzinfo=_UTC),
        "updated_at": datetime(2026, 5, 30, 8, 10, 0, tzinfo=_UTC),
        "run_attempt": 1,
    }
    return GitHubWorkflowRun(**{**defaults, **overrides})


class TestPrToPoint:
    def test_open_pr_state_and_ttm_zero(self) -> None:
        point = pr_to_point(_make_pr(), _REPO)
        assert point.tags["state"] == "open"
        assert point.fields["time_to_merge_seconds"] == 0

    def test_merged_pr_state_and_ttm(self) -> None:
        pr = _make_pr(
            state="closed",
            merged=True,
            created_at=datetime(2026, 5, 28, 10, 0, 0, tzinfo=_UTC),
            merged_at=datetime(2026, 5, 29, 10, 0, 0, tzinfo=_UTC),
        )
        point = pr_to_point(pr, _REPO)
        assert point.tags["state"] == "merged"
        assert point.fields["time_to_merge_seconds"] == 86400  # 24h

    def test_closed_not_merged(self) -> None:
        pr = _make_pr(state="closed", merged=False)
        point = pr_to_point(pr, _REPO)
        assert point.tags["state"] == "closed"
        assert point.fields["time_to_merge_seconds"] == 0

    def test_measurement_prefix(self) -> None:
        point = pr_to_point(_make_pr(), _REPO)
        assert point.measurement == "gov_github_pr"

    def test_base_ref_extraction_from_dict(self) -> None:
        pr = GitHubPR.model_validate(
            {
                "number": 1,
                "title": "t",
                "state": "open",
                "user": {"login": "x", "id": 1},
                "base": {"ref": "develop", "sha": "abc"},
                "created_at": "2026-05-28T10:00:00Z",
            }
        )
        point = pr_to_point(pr, _REPO)
        assert point.tags["target_branch"] == "develop"

    def test_author_tag(self) -> None:
        point = pr_to_point(_make_pr(), _REPO)
        assert point.tags["author"] == "dev-alice"

    def test_repo_tag(self) -> None:
        point = pr_to_point(_make_pr(), _REPO)
        assert point.tags["repo"] == _REPO

    def test_timestamp_ns_from_created_at(self) -> None:
        created = datetime(2026, 5, 28, 10, 0, 0, tzinfo=_UTC)
        point = pr_to_point(_make_pr(created_at=created), _REPO)
        assert point.timestamp_ns == int(created.timestamp() * 1_000_000_000)

    def test_count_field_is_one(self) -> None:
        assert pr_to_point(_make_pr(), _REPO).fields["count"] == 1

    def test_merged_inferred_from_api_list(self) -> None:
        """Verifica que merged é inferido de merged_at quando não vem da API de lista."""
        pr = GitHubPR.model_validate(
            {
                "number": 99,
                "title": "fix: something",
                "state": "closed",
                # sem campo 'merged' — simulando resposta da API de lista
                "user": {"login": "dev", "id": 2},
                "base": {"ref": "main", "sha": "abc"},
                "created_at": "2026-05-27T08:00:00Z",
                "merged_at": "2026-05-27T12:00:00Z",
            }
        )
        assert pr.merged is True
        assert pr.effective_state == "merged"


class TestWorkflowToPoint:
    def test_success_conclusion(self) -> None:
        point = workflow_to_point(_make_run(), _REPO)
        assert point.tags["conclusion"] == "success"

    def test_failure_conclusion(self) -> None:
        point = workflow_to_point(_make_run(conclusion="failure"), _REPO)
        assert point.tags["conclusion"] == "failure"

    def test_none_conclusion_becomes_unknown(self) -> None:
        run = _make_run(conclusion=None)
        point = workflow_to_point(run, _REPO)
        assert point.tags["conclusion"] == "unknown"

    def test_measurement_prefix(self) -> None:
        assert workflow_to_point(_make_run(), _REPO).measurement == "gov_github_workflow"

    def test_duration_calculated(self) -> None:
        run = _make_run(
            run_started_at=datetime(2026, 5, 30, 8, 0, 0, tzinfo=_UTC),
            updated_at=datetime(2026, 5, 30, 8, 10, 0, tzinfo=_UTC),
        )
        point = workflow_to_point(run, _REPO)
        assert point.fields["duration_seconds"] == 600

    def test_duration_never_negative(self) -> None:
        """Clock skew não gera duração negativa."""
        run = _make_run(
            run_started_at=datetime(2026, 5, 30, 8, 10, 0, tzinfo=_UTC),
            updated_at=datetime(2026, 5, 30, 8, 0, 0, tzinfo=_UTC),
        )
        point = workflow_to_point(run, _REPO)
        assert point.fields["duration_seconds"] == 0

    def test_run_attempt_field(self) -> None:
        point = workflow_to_point(_make_run(run_attempt=3), _REPO)
        assert point.fields["attempt"] == 3

    def test_count_field_is_one(self) -> None:
        assert workflow_to_point(_make_run(), _REPO).fields["count"] == 1
