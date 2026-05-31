"""Endpoints Flask-RESTX para integração Zabbix.

Todos os endpoints são read-only, exceto POST /ack que requer
rate limit estrito e audit log (write op no Zabbix).
"""

from __future__ import annotations

import os

import structlog
from flask import request
from flask_restx import Namespace, Resource, fields

from itgov.models.zabbix import AcknowledgeRequest
from itgov.services.zabbix_service import ZabbixService

log = structlog.get_logger(__name__)

ns = Namespace("zabbix", description="Zabbix monitoring integration")

# ── Swagger models ────────────────────────────────────────────────────────────

host_model = ns.model(
    "ZabbixHost",
    {
        "hostid": fields.String(description="ID do host"),
        "host": fields.String(description="Hostname técnico"),
        "name": fields.String(description="Nome amigável"),
        "status": fields.Integer(description="0=habilitado"),
        "available": fields.Integer(description="1=up, 2=down, 0=unknown"),
        "is_up": fields.Boolean(description="True se disponível"),
    },
)

host_summary_model = ns.model(
    "ZabbixHostSummary",
    {
        "total": fields.Integer,
        "up": fields.Integer,
        "down": fields.Integer,
        "unknown": fields.Integer,
        "up_pct": fields.Float(description="Percentual de hosts disponíveis"),
    },
)

problem_model = ns.model(
    "ZabbixProblem",
    {
        "eventid": fields.String,
        "name": fields.String(description="Descrição do problema"),
        "severity": fields.Integer(description="0=not_classified … 5=disaster"),
        "acknowledged": fields.Boolean,
        "clock": fields.String(description="Timestamp de início (ISO 8601)"),
        "hosts": fields.List(fields.String, description="Hosts afetados"),
        "is_resolved": fields.Boolean,
        "duration_seconds": fields.Integer,
    },
)

severity_dist_model = ns.model(
    "ZabbixSeverityDistribution",
    {
        "not classified": fields.Integer,
        "information": fields.Integer,
        "warning": fields.Integer,
        "average": fields.Integer,
        "high": fields.Integer,
        "disaster": fields.Integer,
    },
)

ack_request_model = ns.model(
    "AcknowledgeRequest",
    {
        "eventid": fields.String(required=True, description="ID do evento"),
        "message": fields.String(description="Mensagem de reconhecimento", default="Resolvido via dashboard"),
    },
)

ack_response_model = ns.model(
    "AcknowledgeResponse",
    {
        "ok": fields.Boolean,
        "eventid": fields.String,
    },
)


# ── Helper ────────────────────────────────────────────────────────────────────


def _svc() -> ZabbixService:
    """Instancia ZabbixService com credenciais do env."""
    return ZabbixService(
        url=os.environ["ZABBIX_URL"],
        user=os.environ["ZABBIX_USER"],
        password=os.environ["ZABBIX_PASSWORD"],
    )


# ── Resources ─────────────────────────────────────────────────────────────────


@ns.route("/hosts")
class HostListResource(Resource):
    @ns.marshal_list_with(host_model)
    @ns.doc(description="Lista hosts monitorados com status de disponibilidade")
    def get(self) -> list[dict]:
        """Retorna todos os hosts monitorados."""
        with _svc() as svc:
            hosts = svc.get_hosts()
        return [h.model_dump() for h in hosts]


@ns.route("/hosts/summary")
class HostSummaryResource(Resource):
    @ns.marshal_with(host_summary_model)
    @ns.doc(description="Resumo de disponibilidade dos hosts (up/down/unknown)")
    def get(self) -> dict:
        """Resumo de hosts por disponibilidade."""
        with _svc() as svc:
            summary = svc.get_host_summary()
        return summary.model_dump()


@ns.route("/problems")
class ProblemListResource(Resource):
    @ns.marshal_list_with(problem_model)
    @ns.doc(
        description="Lista problemas ativos ordenados por severidade decrescente",
        params={"limit": "Máximo de resultados (default: 50, max: 200)"},
    )
    def get(self) -> list[dict]:
        """Retorna problemas ativos recentes."""
        limit = min(int(request.args.get("limit", 50)), 200)
        with _svc() as svc:
            problems = svc.get_problems(limit=limit)
        return [
            {
                **p.model_dump(),
                "clock": p.clock.isoformat() if p.clock else None,
                "r_clock": p.r_clock.isoformat() if p.r_clock else None,
            }
            for p in problems
        ]


@ns.route("/problems/severity")
class SeverityDistributionResource(Resource):
    @ns.marshal_with(severity_dist_model)
    @ns.doc(description="Distribuição de problemas por nível de severidade")
    def get(self) -> dict:
        """Contagem de problemas ativos por severidade."""
        with _svc() as svc:
            return svc.get_severity_distribution()


@ns.route("/problems/<string:eventid>/ack")
class AcknowledgeResource(Resource):
    @ns.expect(ack_request_model)
    @ns.marshal_with(ack_response_model)
    @ns.doc(
        description="Reconhece um evento no Zabbix. " "Rate limit: 10/min por IP. Requer autenticação SSO.",
        security="bearer",
    )
    def post(self, eventid: str) -> dict:
        """Reconhece (acknowledge) um evento Zabbix."""
        payload = ns.payload or {}
        req = AcknowledgeRequest(
            eventid=eventid,
            message=payload.get("message", "Resolvido via dashboard"),
        )
        # Audit log obrigatório para write ops
        log.info(
            "zabbix_ack_request",
            eventid=req.eventid,
            message=req.message,
            remote_addr=request.remote_addr,
            user_agent=request.headers.get("User-Agent", ""),
        )
        with _svc() as svc:
            ok = svc.acknowledge_event(req)
        return {"ok": ok, "eventid": eventid}
