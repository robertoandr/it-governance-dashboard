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
   Reference: PR #56. Evaluation rules live in ``sla_evaluator``: targets
   come from the tenant's Zendesk SLA policy (ITIL defaults otherwise) and
   are measured in business hours, never ticket age.

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
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import structlog

import config
from itgov.models.zendesk import (
    CSATSummary,
    SatisfactionRating,
    SLAMetric,
    SLATargets,
    Ticket,
    TicketMetricSet,
    TicketStatus,
)
from itgov.services import sla_evaluator
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
        group_id: int | None = None,
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
        self._group_id = group_id
        self._sla_targets: SLATargets | None = None

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _get_json(self, path: str, **params: Any) -> dict[str, Any]:
        """GET com parâmetros de query, retorna JSON parseado."""
        resp = self.get(path, params=params if params else None)
        return resp.json()

    def _iter_pages(
        self,
        path: str,
        *,
        max_pages: int | None = None,
        cursor: bool = True,
        **params: Any,
    ) -> Iterator[dict[str, Any]]:
        """Itera as páginas (JSON bruto) de um endpoint paginado.

        Suporta dois estilos de paginação do Zendesk:

        * ``cursor=True`` (padrão) — usa ``page[size]`` + ``links.next``.
          Funciona em /api/v2/tickets.json, /api/v2/groups/<id>/tickets.json.
        * ``cursor=False`` — usa ``per_page`` + ``next_page`` (offset).
          Obrigatório para /api/v2/search.json, que retorna 400 com ``page[size]``.

        Args:
            path: Caminho do endpoint.
            max_pages: Cap de páginas. Padrão: ZENDESK_MAX_PAGES (100).
            cursor: True para cursor-based, False para offset-based.
            **params: Query params adicionais para a primeira página.

        Yields:
            O JSON de cada página percorrida.
        """
        cap = max_pages if max_pages is not None else _DEFAULT_MAX_PAGES

        if cursor:
            query_params: dict[str, Any] = {"page[size]": _PAGE_SIZE, **params}
        else:
            query_params = {"per_page": _PAGE_SIZE, **params}

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
            page_count += 1
            yield data

            # Cursor-based: meta.has_more + links.next
            # Offset-based: next_page como URL no topo da resposta
            meta = data.get("meta", {})
            links = data.get("links", {})
            if meta.get("has_more") and links.get("next"):
                next_url: str | None = links["next"]
            else:
                next_url = data.get("next_page") or None

            url = next_url
            query_params = {}  # próximas páginas usam URL completa

        log.debug(
            "zendesk.pagination.completed",
            pages_traversed=page_count,
            cap=cap,
            capped=page_count >= cap,
            mode="cursor" if cursor else "offset",
        )

    def _paginate(
        self,
        path: str,
        root_key: str,
        *,
        max_pages: int | None = None,
        cursor: bool = True,
        **params: Any,
    ) -> list[dict[str, Any]]:
        """Coleta os registros de ``root_key`` de todas as páginas (ver ``_iter_pages``).

        Returns:
            Lista acumulada de registros de todas as páginas percorridas.
        """
        return [
            record
            for page in self._iter_pages(path, max_pages=max_pages, cursor=cursor, **params)
            for record in page.get(root_key, [])
        ]

    def _search_tickets(self, query: str, sort_by: str) -> list[Ticket]:
        """Busca tickets via Search API com os sideloads de SLA.

        Pede ``include=tickets(slas,metric_sets)`` para que cada ticket traga o
        estado de SLA calculado pelo Zendesk e os tempos em horário comercial,
        sem uma chamada extra por ticket.
        """
        raw: list[dict[str, Any]] = []
        metric_sets: dict[int, dict[str, Any]] = {}
        for page in self._iter_pages(
            "/api/v2/search.json",
            cursor=False,  # search API usa offset pagination (per_page), não cursor (page[size])
            query=query,
            sort_by=sort_by,
            sort_order="desc",
            include="tickets(slas,metric_sets)",
        ):
            raw.extend(page.get("results", []))
            for ms in page.get("metric_sets", []):
                metric_sets[ms["ticket_id"]] = ms

        tickets = []
        for t in raw:
            if t.get("result_type") != "ticket" and "subject" not in t:
                continue
            ticket = Ticket.model_validate(t)
            if ticket.id in metric_sets:
                ticket.metric_set = TicketMetricSet.from_api(metric_sets[ticket.id])
            tickets.append(ticket)
        return tickets

    # ── Public Methods ────────────────────────────────────────────────────────

    def get_groups(self) -> list[dict[str, Any]]:
        """Lista todos os grupos do Zendesk. Útil para descobrir o group_id."""
        data = self._get_json("/api/v2/groups.json")
        groups = data.get("groups", [])
        log.info("zendesk_groups_fetched", count=len(groups))
        return [{"id": g["id"], "name": g["name"]} for g in groups]

    def get_tickets(self, status: str | None = None) -> list[Ticket]:
        """Retorna tickets com filtro opcional por status.

        Quando ``group_id`` foi passado no construtor, usa a Search API com
        ``group_id:<id>`` — única forma de filtrar por grupo server-side no
        Zendesk (o param ``group_id`` em /tickets.json é ignorado pela API).

        Args:
            status: "open", "pending", "solved", etc. None retorna todos.

        Returns:
            Lista de Ticket ordenada por data de criação decrescente.
        """
        if self._group_id:
            query = f"type:ticket group_id:{self._group_id}"
            if status:
                query += f" status:{status}"
            raw = self._paginate(
                "/api/v2/search.json",
                "results",
                cursor=False,
                query=query,
                sort_by="created_at",
                sort_order="desc",
            )
            raw = [t for t in raw if t.get("result_type") == "ticket" or "subject" in t]
        else:
            params: dict[str, Any] = {"sort_by": "created_at", "sort_order": "desc"}
            if status:
                params["status"] = status
            raw = self._paginate("/api/v2/tickets.json", "tickets", **params)

        tickets = [Ticket.model_validate(t) for t in raw]
        log.info(
            "zendesk_tickets_fetched",
            count=len(tickets),
            status_filter=status,
            group_id=self._group_id,
        )
        return tickets

    def get_open_tickets(self) -> list[Ticket]:
        """Retorna tickets abertos (new + open + pending) via search server-side.

        Quando ``group_id`` configurado, acrescenta ``group_id:<id>`` à query
        para que a API retorne apenas tickets do grupo — reduz drasticamente
        o payload transferido em tenants grandes.
        """
        # Repetir a mesma keyword faz OR implícito na search API do Zendesk.
        # "status:new OR status:open ..." NÃO funciona: combinado com group_id
        # retornava 2 tickets em vez de 65.
        query = "type:ticket status:new status:open status:pending"
        if self._group_id:
            query += f" group_id:{self._group_id}"

        tickets = self._search_tickets(query, sort_by="created_at")
        log.info("zendesk_open_tickets_fetched", count=len(tickets), group_id=self._group_id)
        return tickets

    def get_sla_targets(self) -> SLATargets:
        """Retorna as metas de SLA do tenant (política do Zendesk ou padrão ITIL).

        Planos sem SLA (abaixo de Professional) respondem 403 em
        ``/api/v2/slas/policies.json``; nesse caso usamos as metas ITIL.
        O resultado é memorizado na instância.
        """
        if self._sla_targets is None:
            try:
                policies = self._get_json("/api/v2/slas/policies.json").get("sla_policies", [])
            except httpx.HTTPStatusError as exc:
                log.warning("zendesk.sla.policies_unavailable", status=exc.response.status_code)
                policies = []
            targets = sla_evaluator.targets_from_policies(policies) or sla_evaluator.default_targets()
            log.info("zendesk.sla.targets_loaded", source=targets.source, policy=targets.policy_name)
            self._sla_targets = targets
        return self._sla_targets

    def get_sla_metrics(self, days: int = 30) -> SLAMetric:
        """Calcula o SLA dos tickets resolvidos nos últimos ``days`` dias.

        É o KPI de mercado: percentual de tickets atendidos dentro da meta de
        1ª resposta e de resolução, em horário comercial. Tickets ainda
        abertos não entram — o SLA deles ainda não terminou.

        Args:
            days: Janela em dias (padrão 30).

        Returns:
            SLAMetric com compliance geral e por métrica.
        """
        solved = self.get_solved_tickets(days=days)
        metric = sla_evaluator.summarize(solved, self.get_sla_targets(), window_days=days)
        log.info(
            "zendesk_sla_calculated",
            total=metric.total_tickets,
            breached=metric.breached,
            unknown=metric.unknown,
            compliance=metric.compliance_pct,
            days=days,
        )
        return metric

    def get_solved_tickets(self, days: int = 30) -> list[Ticket]:
        """Retorna tickets resolvidos nos últimos ``days`` dias.

        Usa Search API com filtro pela data de resolução — muito mais rápido
        que buscar todos os tickets e filtrar em Python. Inclui ``closed``:
        o Zendesk fecha tickets resolvidos automaticamente após alguns dias,
        e eles continuam contando como resolvidos na janela.
        """
        cutoff_date = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d")
        # Repetir a keyword faz OR implícito (ver get_open_tickets).
        query = f"type:ticket status:solved status:closed solved>{cutoff_date}"
        if self._group_id:
            query += f" group_id:{self._group_id}"

        tickets = self._search_tickets(query, sort_by="updated_at")
        log.info("zendesk_solved_tickets_fetched", count=len(tickets), days=days, group_id=self._group_id)
        return tickets

    def get_satisfaction_ratings(self) -> list[SatisfactionRating]:
        """Retorna avaliações de satisfação (CSAT) dos últimos 90 dias.

        Usa ``start_time`` para evitar varrer todos os ratings do tenant
        (pode ser dezenas de milhares) — limitado a 3 páginas (300 ratings).
        """
        start_time = int((datetime.now(UTC) - timedelta(days=90)).timestamp())
        raw = self._paginate(
            "/api/v2/satisfaction_ratings.json",
            "satisfaction_ratings",
            max_pages=3,
            sort_order="desc",
            start_time=start_time,
        )
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
