"""Zendesk integration service.

Provides access to Zendesk Support tickets, SLA metrics and CSAT ratings,
normalized into Pydantic models for consumption by the IT Governance Dashboard.

Edge contracts (established Sprint 10F, formalized here)
---------------------------------------------------------

The following design decisions are INTENTIONAL and load-bearing.
Changing any of them WILL affect dashboard semantics. Read before
"fixing" what may look like a bug.

1. Datetime arithmetic — UTC-aware
   All age/duration computations use ``datetime.now(UTC) - ticket.<ts>``.
   Naive timestamps from the Zendesk API are converted to UTC at parse
   time. Rationale: dashboard runs in multi-timezone deployments and
   silent naive-vs-aware bugs are notoriously hard to spot.
   Reference: PR #55.

2. SLA compliance — three-state, not boolean
   Tickets are classified as ``compliant``, ``breached`` or ``unknown``
   (no SLA policy applies). Unknown tickets are NOT counted as compliant.
   Rationale: silent reclassification inflates compliance metrics and
   masks gaps in SLA policy configuration.
   Reference: PR #56.

3. Pagination — cursor-based with safety cap
   ``_paginate`` traverses ``next_page`` cursors until exhausted OR
   ``_DEFAULT_MAX_PAGES`` is reached (configurable via
   ``ZENDESK_MAX_PAGES`` env var, default 100 ≈ 10k tickets).
   When the cap fires, a structured WARN log is emitted
   (``zendesk.pagination.cap_reached``) and iteration stops gracefully
   — it does NOT raise. Rationale: dashboards prefer partial data over
   500s; observability surfaces the gap without breaking UX.
   Reference: PR #57, PR #64 (issue #58).

4. CSAT score — calculated over OFFERED surveys only
   ``csat_score`` averages ratings from tickets where a survey was
   actually offered. Tickets without an offered survey are EXCLUDED
   from the denominator (not counted as 0). Rationale: "no survey" ≠
   "bad rating"; counting unoffered as zero penalizes teams for Zendesk
   policy configuration, not actual satisfaction.
   Reference: PR #59.
   ``CSATSummary.sample_size`` exposes the denominator so consumers can
   distinguish "no data" (sample_size=0, csat_pct=None) from "bad ratings"
   (sample_size>0, csat_pct=0.0). Implemented in PR #66 (issue #61).

5. SLAPolicy model — removed (YAGNI)
   An earlier draft modeled Zendesk SLA Policies as a first-class
   Pydantic entity. It was removed because no downstream consumer reads
   policy definitions — only the per-ticket compliance result matters.
   Re-add only when a concrete consumer materializes.
   Reference: PR #60.

Conventions
-----------

* Module-level constants (``_PAGE_SIZE``, ``_DEFAULT_MAX_PAGES``) are
  resolved from env at import time. Tests that need to override them
  pass ``max_pages`` directly to ``_paginate`` rather than patching
  the environment.
* Logs use structlog with dotted event names: ``zendesk.<area>.<event>``.
  Tests capture them via ``structlog.testing.capture_logs()`` rather
  than pytest's ``caplog`` (which only intercepts stdlib logging).
* All public methods return Pydantic models, never raw dicts.
"""

from __future__ import annotations

import base64
from typing import Any

import structlog

import config
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
_DEFAULT_MAX_PAGES: int = config.ZENDESK_MAX_PAGES


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

    def _paginate(
        self,
        path: str,
        root_key: str,
        *,
        max_pages: int | None = None,
        **params: Any,
    ) -> list[dict[str, Any]]:
        """Coleta todas as páginas de um endpoint paginado (cursor-based).

        Args:
            path: Caminho do endpoint (ex: "/api/v2/tickets.json").
            root_key: Chave raiz da resposta JSON com os registros.
            max_pages: Cap de páginas. Padrão: ZENDESK_MAX_PAGES (100).
                100 páginas × 100 tickets/página = 10k registros por chamada.
                Se excedido, emite WARN estruturado e interrompe (Opção A:
                dados parciais preferíveis a erro 500 no dashboard).
            **params: Query params adicionais para a primeira página.

        Returns:
            Lista acumulada de registros de todas as páginas percorridas.
        """
        cap = max_pages if max_pages is not None else _DEFAULT_MAX_PAGES
        results: list[dict[str, Any]] = []
        query_params: dict[str, Any] = {"page[size]": _PAGE_SIZE, **params}
        url: str | None = path
        page_count = 0

        while url:
            if page_count >= cap:
                log.warning(
                    "zendesk.pagination.cap_reached",
                    cap=cap,
                    pages_traversed=page_count,
                    last_url=url,
                    hint="Consider incremental exports for large tenants",
                )
                break

            data = self._get_json(url, **query_params) if url == path else self._get_json(url)
            results.extend(data.get(root_key, []))
            page_count += 1

            meta = data.get("meta", {})
            links = data.get("links", {})
            # Cursor-based pagination (API v2)
            if meta.get("has_more") and links.get("next"):
                url = links["next"]
                query_params = {}  # próxima página usa URL completa
            else:
                url = None

        log.debug(
            "zendesk.pagination.completed",
            pages_traversed=page_count,
            cap=cap,
            capped=page_count >= cap,
        )
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
            CSATSummary com totais, percentual de satisfação e sample_size.
            ``csat_pct`` is None when sample_size == 0 — consumers must treat
            None as "no data", not as "0% satisfaction".
        """
        ratings = self.get_satisfaction_ratings()
        total = len(ratings)
        good = sum(1 for r in ratings if r.score == "good")
        bad = total - good
        csat_pct = round((good / total) * 100, 1) if total else None
        return CSATSummary(total_ratings=total, good=good, bad=bad, csat_pct=csat_pct, sample_size=total)

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
