"""Serviço Zendesk: REST API via SyncAPIClient + auth por token."""

from __future__ import annotations

import base64
from typing import Any

import structlog

from itgov.models.zendesk import (
    CSATSummary,
    SatisfactionRating,
    SLAMetric,
    Ticket,
    TicketStatus,
)
from itgov.utils.http_client import SyncAPIClient

log = structlog.get_logger(__name__)

_PAGE_SIZE = 100  # max permitido pela API Zendesk v2


class ZendeskService(SyncAPIClient):
    """Cliente Zendesk REST API.

    Autentica via API token (email/token Basic Auth).
    Read-only — sem operações de escrita no escopo inicial.

    Args:
        subdomain: Subdomínio do Zendesk (ex: "empresa" para empresa.zendesk.com).
        email: Email do agente para autenticação.
        api_token: Token de API gerado no painel Zendesk.
        timeout: Timeout HTTP em segundos (default: 10).
        max_retries: Tentativas em falhas 5xx (default: 3).
    """

    def __init__(
        self,
        subdomain: str,
        email: str,
        api_token: str,
        timeout: float = 10.0,
        max_retries: int = 3,
    ) -> None:
        credentials = base64.b64encode(f"{email}/token:{api_token}".encode()).decode()
        super().__init__(
            base_url=f"https://{subdomain}.zendesk.com",
            timeout=timeout,
            max_retries=max_retries,
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/json",
            },
        )
        self._subdomain = subdomain

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _get_json(self, path: str, **params: Any) -> dict[str, Any]:
        """GET com parâmetros de query, retorna JSON parseado."""
        resp = self.get(path, params=params if params else None)
        return resp.json()

    def _paginate(self, path: str, root_key: str, **params: Any) -> list[dict[str, Any]]:
        """Coleta todas as páginas de um endpoint paginado (cursor-based)."""
        results: list[dict[str, Any]] = []
        params = {"page[size]": _PAGE_SIZE, **params}
        url: str | None = path

        while url:
            data = self._get_json(url, **params) if url == path else self._get_json(url)
            results.extend(data.get(root_key, []))
            meta = data.get("meta", {})
            links = data.get("links", {})
            # Cursor-based pagination (API v2)
            if meta.get("has_more") and links.get("next"):
                url = links["next"]
                params = {}  # próxima página usa URL completa
            else:
                url = None

        return results

    # ── Public Methods ────────────────────────────────────────────────────────

    def get_tickets(self, status: str | None = None) -> list[Ticket]:
        """Retorna tickets com filtro opcional por status.

        Args:
            status: "open", "pending", "solved", etc. None retorna todos.

        Returns:
            Lista de Ticket ordenada por data de criação decrescente.
        """
        params: dict[str, Any] = {"sort_by": "created_at", "sort_order": "desc"}
        if status:
            params["status"] = status

        raw = self._paginate("/api/v2/tickets.json", "tickets", **params)
        tickets = [Ticket.model_validate(t) for t in raw]
        log.info("zendesk_tickets_fetched", count=len(tickets), status_filter=status)
        return tickets

    def get_open_tickets(self) -> list[Ticket]:
        """Atalho: retorna apenas tickets abertos (new + open + pending)."""
        all_tickets = self.get_tickets()
        return [t for t in all_tickets if t.is_open]

    def get_sla_metrics(self) -> SLAMetric:
        """Calcula métricas de SLA a partir dos tickets ativos.

        Nota: Zendesk SLA real requer plano Professional+. Esta implementação
        calcula uma aproximação baseada em tickets abertos e idade.

        Returns:
            SLAMetric com compliance estimado.
        """
        open_tickets = self.get_open_tickets()
        total = len(open_tickets)

        # SLA breach heurística: tickets abertos há mais de 8h sem resposta
        _sla_threshold_hours = 8.0
        breached = sum(1 for t in open_tickets if t.age_hours > _sla_threshold_hours)
        compliance = round((1 - breached / total) * 100, 1) if total else 100.0

        log.info("zendesk_sla_calculated", total=total, breached=breached, compliance=compliance)
        return SLAMetric(
            total_tickets=total,
            breached=breached,
            compliance_pct=compliance,
        )

    def get_satisfaction_ratings(self) -> list[SatisfactionRating]:
        """Retorna avaliações de satisfação (CSAT) recentes.

        Returns:
            Lista de SatisfactionRating (últimas 100 avaliações).
        """
        raw = self._paginate("/api/v2/satisfaction_ratings.json", "satisfaction_ratings", sort_order="desc")
        ratings = [SatisfactionRating.model_validate(r) for r in raw if r.get("score") in ("good", "bad")]
        log.info("zendesk_csat_fetched", count=len(ratings))
        return ratings

    def get_csat_summary(self) -> CSATSummary:
        """Calcula resumo de CSAT (Customer Satisfaction Score).

        Returns:
            CSATSummary com totais e percentual de satisfação.
        """
        ratings = self.get_satisfaction_ratings()
        total = len(ratings)
        good = sum(1 for r in ratings if r.score == "good")
        bad = total - good
        csat_pct = round((good / total) * 100, 1) if total else 0.0
        return CSATSummary(total_ratings=total, good=good, bad=bad, csat_pct=csat_pct)

    def get_ticket_volume_by_status(self) -> dict[str, int]:
        """Retorna contagem de tickets por status.

        Returns:
            Dict com TicketStatus como chave e contagem como valor.
        """
        tickets = self.get_tickets()
        volume: dict[str, int] = {s.value: 0 for s in TicketStatus}
        for t in tickets:
            volume[t.status.value] = volume.get(t.status.value, 0) + 1
        return volume
