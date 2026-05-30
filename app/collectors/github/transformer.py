"""Transformação: GitHubPR / GitHubWorkflowRun → GovernancePoint."""

from __future__ import annotations

from app.collectors.github.models import GitHubPR, GitHubWorkflowRun
from app.storage.influxdb.models import GovernancePoint


def pr_to_point(pr: GitHubPR, repo: str) -> GovernancePoint:
    """Converte GitHubPR em GovernancePoint para governance_raw.

    Measurement: ``gov_github_pr``

    Tags:
        repo: Nome curto do repositório (sem owner).
        state: "open" | "closed" | "merged".
        author: Login do autor.
            ⚠️ Cardinality ALTA em orgs grandes — considerar agregar por
            equipe (CODEOWNERS) se |unique_authors| > 200.
        target_branch: Branch alvo do PR (base.ref).

    Fields:
        pr_number, title, additions, deletions, changed_files,
        time_to_merge_seconds (0 se aberto), review_comments, count=1.

    Args:
        pr: PR validado pelo modelo GitHubPR.
        repo: Nome curto do repositório (sem owner/).

    Returns:
        GovernancePoint para escrita em governance_raw.
    """
    ttm = int((pr.merged_at - pr.created_at).total_seconds()) if pr.merged_at else 0
    return GovernancePoint(
        measurement="gov_github_pr",
        tags={
            "repo": repo,
            "state": pr.effective_state,
            "author": pr.user.login,
            "target_branch": pr.base_ref,
        },
        fields={
            "pr_number": pr.number,
            "title": pr.title,
            "additions": pr.additions,
            "deletions": pr.deletions,
            "changed_files": pr.changed_files,
            "time_to_merge_seconds": ttm,
            "review_comments": pr.review_comments,
            "count": 1,
        },
        timestamp_ns=int(pr.created_at.timestamp() * 1_000_000_000),
    )


def workflow_to_point(run: GitHubWorkflowRun, repo: str) -> GovernancePoint:
    """Converte GitHubWorkflowRun em GovernancePoint para governance_raw.

    Measurement: ``gov_github_workflow``

    Tags:
        repo: Nome curto do repositório.
        workflow_name: Nome do workflow.
            ⚠️ Cardinality BAIXA (< 20 por repo) — seguro como tag.
        branch: Branch do run.
            ⚠️ Cardinality MÉDIA — feature branches podem explodir.
            Mitigação futura: normalizar feature/*, hotfix/* → "feature", "hotfix".
        conclusion: "success" | "failure" | "cancelled" | "skipped" | "unknown".
        event: "push" | "pull_request" | "schedule" | "workflow_dispatch".

    Fields:
        run_id, duration_seconds, attempt, count=1.

    Args:
        run: Workflow run validado por GitHubWorkflowRun.
        repo: Nome curto do repositório (sem owner/).

    Returns:
        GovernancePoint para escrita em governance_raw.
    """
    duration = int((run.updated_at - run.run_started_at).total_seconds())
    return GovernancePoint(
        measurement="gov_github_workflow",
        tags={
            "repo": repo,
            "workflow_name": run.name,
            "branch": run.head_branch,
            "conclusion": run.conclusion or "unknown",
            "event": run.event,
        },
        fields={
            "run_id": run.id,
            "duration_seconds": max(0, duration),  # guard against clock skew
            "attempt": run.run_attempt,
            "count": 1,
        },
        timestamp_ns=int(run.run_started_at.timestamp() * 1_000_000_000),
    )
