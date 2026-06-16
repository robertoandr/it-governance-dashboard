"""Endpoints Flask-RESTX para integração Zendesk (read-only)."""

from __future__ import annotations

import time
from typing import Any

import structlog
from flask import request
from flask_restx import Namespace, Resource, fields

import config
from itgov.services.zendesk_service import ZendeskService

log = structlog.get_logger(__name__)

# Cache em memória (mesmo padrão de app/api/dashboards.py): get_mttr_summary() e
# get_ticket_volume_by_status() paginam o histórico completo de tickets — sem
# cache, cada carregamento da página/endpoint leva ~20s (centenas de páginas
# na API Zendesk). TTL curto evita refetch a cada request sem deixar os
# números muito desatualizados.
_TTL_SECONDS = 120
_mttr_cache: dict[str, Any] = {}
_volume_cache: dict[str, Any] = {}


def get_cached_mttr_summary() -> dict[str, Any]:
    """Retorna o resumo de MTTR, recalculando no máximo a cada _TTL_SECONDS."""
    now = time.monotonic()
    if _mttr_cache.get("expires_at", 0) > now and _mttr_cache.get("data") is not None:
        log.debug("zendesk_mttr_cache_hit")
        return _mttr_cache["data"]  # type: ignore[return-value]

    with _svc() as svc:
        summary = svc.get_mttr_summary()
    data = summary.model_dump()
    _mttr_cache["data"] = data
    _mttr_cache["expires_at"] = now + _TTL_SECONDS
    log.info("zendesk_mttr_cache_refreshed", ttl=_TTL_SECONDS)
    return data


def get_cached_volume_by_status() -> dict[str, int]:
    """Retorna o volume de tickets por status, recalculando no máximo a cada _TTL_SECONDS."""
    now = time.monotonic()
    if _volume_cache.get("expires_at", 0) > now and _volume_cache.get("data") is not None:
        log.debug("zendesk_volume_cache_hit")
        return _volume_cache["data"]  # type: ignore[return-value]

    with _svc() as svc:
        data = svc.get_ticket_volume_by_status()
    _volume_cache["data"] = data
    _volume_cache["expires_at"] = now + _TTL_SECONDS
    log.info("zendesk_volume_cache_refreshed", ttl=_TTL_SECONDS)
    return data


ns = Namespace("zendesk", description="Zendesk support integration")

# ── Swagger models ────────────────────────────────────────────────────────────

ticket_model = ns.model(
    "ZendeskTicket",
    {
        "id": fields.Integer,
        "subject": fields.String,
        "status": fields.String(description="new|open|pending|hold|solved|closed"),
        "priority": fields.String(description="low|normal|high|urgent"),
        "created_at": fields.String(description="ISO 8601"),
        "updated_at": fields.String(description="ISO 8601"),
        "is_open": fields.Boolean,
        "age_hours": fields.Float(description="Horas desde abertura"),
        "tags": fields.List(fields.String),
    },
)

sla_model = ns.model(
    "ZendeskSLAMetric",
    {
        "total_tickets": fields.Integer,
        "breached": fields.Integer,
        "compliance_pct": fields.Float(description="Percentual de tickets dentro do SLA"),
        "avg_first_reply_minutes": fields.Float(allow_null=True),
    },
)

csat_model = ns.model(
    "ZendeskCSATSummary",
    {
        "total_ratings": fields.Integer,
        "good": fields.Integer,
        "bad": fields.Integer,
        "csat_pct": fields.Float(
            allow_null=True, description="Percentual de avaliações positivas, ou null se sem dados"
        ),
        "sample_size": fields.Integer(description="Surveys respondidas (good + bad) na janela"),
    },
)

mttr_model = ns.model(
    "ZendeskMTTRSummary",
    {
        "sample_size": fields.Integer(description="Tickets resolvidos considerados no cálculo"),
        "avg_business_minutes": fields.Float(allow_null=True, description="MTTR médio em horário comercial"),
        "avg_calendar_minutes": fields.Float(allow_null=True, description="MTTR médio em tempo corrido"),
        "avg_first_reply_business_minutes": fields.Float(allow_null=True, description="1ª resposta média (business)"),
    },
)

volume_model = ns.model(
    "ZendeskVolumeByStatus",
    dict.fromkeys(("new", "open", "pending", "hold", "solved", "closed"), fields.Integer),
)


# ── Helper ────────────────────────────────────────────────────────────────────


def _svc() -> ZendeskService:
    """Instancia ZendeskService com credenciais validadas em startup via config."""
    return ZendeskService(
        subdomain=config.ZENDESK_SUBDOMAIN,
        email=config.ZENDESK_EMAIL,
        api_token=config.ZENDESK_API_TOKEN,
    )


# ── Resources ─────────────────────────────────────────────────────────────────


@ns.route("/tickets")
class TicketListResource(Resource):
    @ns.marshal_list_with(ticket_model)
    @ns.doc(
        description="Lista tickets com filtro opcional por status",
        params={"status": "Filtro de status: new|open|pending|hold|solved|closed"},
    )
    def get(self) -> list[dict]:
        """Retorna tickets do Zendesk."""
        status_filter = request.args.get("status")
        with _svc() as svc:
            tickets = svc.get_tickets(status=status_filter)
        return [
            {
                **t.model_dump(),
                "created_at": t.created_at.isoformat(),
                "updated_at": t.updated_at.isoformat(),
            }
            for t in tickets
        ]


@ns.route("/tickets/open")
class OpenTicketResource(Resource):
    @ns.marshal_list_with(ticket_model)
    @ns.doc(description="Lista apenas tickets abertos (new + open + pending)")
    def get(self) -> list[dict]:
        """Tickets abertos (requerem atenção)."""
        with _svc() as svc:
            tickets = svc.get_open_tickets()
        return [
            {
                **t.model_dump(),
                "created_at": t.created_at.isoformat(),
                "updated_at": t.updated_at.isoformat(),
            }
            for t in tickets
        ]


@ns.route("/tickets/volume")
class VolumeResource(Resource):
    @ns.marshal_with(volume_model)
    @ns.doc(description="Contagem de tickets por status")
    def get(self) -> dict:
        """Volume de tickets agrupado por status (cacheado, ver _TTL_SECONDS)."""
        return get_cached_volume_by_status()


@ns.route("/sla")
class SLAResource(Resource):
    @ns.marshal_with(sla_model)
    @ns.doc(description="Métricas de SLA — compliance e tickets em breach")
    def get(self) -> dict:
        """Métricas de SLA dos tickets ativos."""
        with _svc() as svc:
            metric = svc.get_sla_metrics()
        return metric.model_dump()


@ns.route("/mttr")
class MTTRResource(Resource):
    @ns.marshal_with(mttr_model)
    @ns.doc(description="MTTR — tempo médio de resolução (business e calendar)")
    def get(self) -> dict:
        """Resumo de MTTR a partir de ticket_metrics (cacheado, ver _TTL_SECONDS)."""
        return get_cached_mttr_summary()


@ns.route("/csat")
class CSATResource(Resource):
    @ns.marshal_with(csat_model)
    @ns.doc(description="Customer Satisfaction Score — resumo de avaliações")
    def get(self) -> dict:
        """Resumo de CSAT (satisfação do cliente)."""
        with _svc() as svc:
            summary = svc.get_csat_summary()
        return summary.model_dump()
