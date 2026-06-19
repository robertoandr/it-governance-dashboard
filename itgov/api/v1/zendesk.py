"""Endpoints Flask-RESTX para integração Zendesk (read-only)."""

from __future__ import annotations

import os
import threading
import time

import structlog
from flask import request
from flask_restx import Namespace, Resource, fields

from itgov.services.zendesk_service import ZendeskService

log = structlog.get_logger(__name__)

# ── Cache em memória (TTL 5min) ───────────────────────────────────────────────

_CACHE_TTL = 300

_lock_mttr = threading.Lock()
_cache_mttr: dict | None = None
_cache_mttr_ts: float = 0.0

_lock_vol = threading.Lock()
_cache_vol: dict | None = None
_cache_vol_ts: float = 0.0


def _cache_valido(ts: float) -> bool:
    return (time.monotonic() - ts) < _CACHE_TTL


def get_cached_mttr_summary() -> dict:
    """Retorna resumo MTTR/SLA + CSAT do Zendesk com cache de 5min."""
    global _cache_mttr, _cache_mttr_ts
    with _lock_mttr:
        if _cache_mttr is not None and _cache_valido(_cache_mttr_ts):
            log.debug("zendesk.mttr.cache.hit")
            return _cache_mttr

    log.info("zendesk.mttr.cache.miss")
    with _svc() as svc:
        sla = svc.get_sla_metrics()
        csat = svc.get_csat_summary()
        open_tickets = svc.get_open_tickets()

    total_open = len(open_tickets)
    avg_age = round(sum(t.age_hours for t in open_tickets) / total_open, 1) if total_open else 0.0

    dados = {
        "total_open": total_open,
        "breached": sla.breached,
        "compliance_pct": sla.compliance_pct,
        "avg_age_hours": avg_age,
        "csat_pct": csat.csat_pct,
        "csat_sample": csat.sample_size,
        "csat_good": csat.good,
        "csat_bad": csat.bad,
    }
    with _lock_mttr:
        _cache_mttr = dados
        _cache_mttr_ts = time.monotonic()
    return dados


def get_cached_volume_by_status() -> dict:
    """Retorna volume de tickets por status com cache de 5min."""
    global _cache_vol, _cache_vol_ts
    with _lock_vol:
        if _cache_vol is not None and _cache_valido(_cache_vol_ts):
            log.debug("zendesk.volume.cache.hit")
            return _cache_vol

    log.info("zendesk.volume.cache.miss")
    with _svc() as svc:
        dados = svc.get_ticket_volume_by_status()
    with _lock_vol:
        _cache_vol = dados
        _cache_vol_ts = time.monotonic()
    return dados


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

volume_model = ns.model(
    "ZendeskVolumeByStatus",
    dict.fromkeys(("new", "open", "pending", "hold", "solved", "closed"), fields.Integer),
)


# ── Helper ────────────────────────────────────────────────────────────────────


def _svc() -> ZendeskService:
    """Instancia ZendeskService com credenciais do ambiente."""
    return ZendeskService(
        subdomain=os.getenv("ZENDESK_SUBDOMAIN", ""),
        email=os.getenv("ZENDESK_EMAIL", ""),
        api_token=os.getenv("ZENDESK_API_TOKEN", ""),
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
        """Volume de tickets agrupado por status."""
        with _svc() as svc:
            return svc.get_ticket_volume_by_status()


@ns.route("/sla")
class SLAResource(Resource):
    @ns.marshal_with(sla_model)
    @ns.doc(description="Métricas de SLA — compliance e tickets em breach")
    def get(self) -> dict:
        """Métricas de SLA dos tickets ativos."""
        with _svc() as svc:
            metric = svc.get_sla_metrics()
        return metric.model_dump()


@ns.route("/csat")
class CSATResource(Resource):
    @ns.marshal_with(csat_model)
    @ns.doc(description="Customer Satisfaction Score — resumo de avaliações")
    def get(self) -> dict:
        """Resumo de CSAT (satisfação do cliente)."""
        with _svc() as svc:
            summary = svc.get_csat_summary()
        return summary.model_dump()
