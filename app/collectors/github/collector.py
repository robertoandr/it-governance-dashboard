"""GitHubCollector — coleta PRs e workflow runs de N repos em uma passada."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import structlog
from pydantic import ValidationError

from app.collectors.base import BaseCollector
from app.collectors.github.client import GitHubCollectorClient
from app.collectors.github.config import GitHubCollectorSettings
from app.collectors.github.exceptions import GitHubAPIError, GitHubCollectorError
from app.collectors.github.models import GitHubPR, GitHubWorkflowRun
from app.collectors.github.transformer import pr_to_point, workflow_to_point
from app.storage.influxdb.client import GovernanceInfluxClient
from app.storage.influxdb.models import GovernancePoint

log = structlog.get_logger(__name__)

_TYPE_PR = "pr"
_TYPE_WORKFLOW = "workflow"


class GitHubCollector(BaseCollector[dict]):
    """Coleta PRs e Workflows de N repos em uma única passada.

    Diferente do ZabbixCollector (single measurement), fetch() retorna
    envelopes tipados {"type": "pr"|"workflow", "data": dict, "repo": str}
    para que transform() possa despachar para o transformer correto.

    Partial failure: se um repo falhar, o erro é logado e os outros
    continuam sendo coletados.

    Args:
        client: Cliente GitHub com métodos filtrados por since.
        influx: Cliente InfluxDB escopado em governance_raw.
        settings: Configuração do coletor.
    """

    def __init__(
        self,
        client: GitHubCollectorClient,
        influx: GovernanceInfluxClient,
        settings: GitHubCollectorSettings,
    ) -> None:
        super().__init__(influx=influx, name="github")
        self.client = client
        self.settings = settings

    async def fetch(self) -> list[dict]:
        """Itera sobre todos os repos e coleta PRs + workflow runs.

        Captura erros por repo (partial failure) para não derrubar a coleta
        dos outros repos quando um falha.

        Returns:
            Lista de envelopes {"type", "data", "repo"} prontos para transform.
        """
        cutoff = datetime.now(UTC) - timedelta(hours=self.settings.lookback_hours)
        envelopes: list[dict] = []
        errors: dict[str, str] = {}

        for repo in self.settings.repos:
            repo_name = repo.split("/")[-1]
            try:
                prs = await self.client.list_pulls_since(repo, cutoff)
                log.info("github.fetch_prs", repo=repo, count=len(prs))
                envelopes.extend({"type": _TYPE_PR, "data": pr, "repo": repo_name} for pr in prs)

                runs = await self.client.list_workflow_runs_since(repo, cutoff)
                log.info("github.fetch_workflows", repo=repo, count=len(runs))
                envelopes.extend({"type": _TYPE_WORKFLOW, "data": run, "repo": repo_name} for run in runs)

            except (GitHubAPIError, httpx.HTTPError) as exc:
                log.error("github.repo_fetch_failed", repo=repo, error=str(exc))
                errors[repo] = str(exc)

        if errors and len(errors) == len(self.settings.repos):
            raise GitHubCollectorError(repos=list(self.settings.repos), errors=errors)

        return envelopes

    def transform(self, raw: list[dict]) -> list[GovernancePoint]:
        """Converte envelopes em GovernancePoints.

        Erros de validação por item são logados e o item é descartado,
        sem interromper a transformação dos demais.

        Args:
            raw: Lista de envelopes de fetch().

        Returns:
            Lista de GovernancePoint prontos para escrita.
        """
        points: list[GovernancePoint] = []
        for item in raw:
            try:
                if item["type"] == _TYPE_PR:
                    pr = GitHubPR.model_validate(item["data"])
                    points.append(pr_to_point(pr, item["repo"]))
                elif item["type"] == _TYPE_WORKFLOW:
                    run = GitHubWorkflowRun.model_validate(item["data"])
                    points.append(workflow_to_point(run, item["repo"]))
            except ValidationError as exc:
                log.warning(
                    "github.transform_skipped",
                    type=item.get("type"),
                    repo=item.get("repo"),
                    error=str(exc)[:120],
                )
        return points
