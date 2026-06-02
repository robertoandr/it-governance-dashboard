"""Endpoints Flask-RESTX para integração Zendesk (read-only)."""

from __future__ import annotations

import structlog
from flask import request
from flask_restx import Namespace, Resource, fields

import config
from itgov.services.zendesk_service import ZendeskService

log = structlog.get_logger(__name__)

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
