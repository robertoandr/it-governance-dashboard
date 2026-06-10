"""Endpoint GET /v1/alerts/active — alertas ativos para dashboard e telas CFTV.

Lê exclusivamente a tabela active_alerts (sem chamadas ao Zabbix ao vivo).
Ordenação: critical primeiro, depois por created_at DESC.
"""

from __future__ import annotations

import structlog
from flask_restx import Namespace, Resource, fields

from itgov.db.session import get_session
from itgov.models.db.alert import ActiveAlert

log = structlog.get_logger(__name__)

ns = Namespace("alerts", description="Alertas ativos com causa-raiz")

_alert_model = ns.model(
    "ActiveAlert",
    {
        "host_id": fields.String(description="ID do host no Zabbix"),
        "host_name": fields.String(description="Nome visível do host"),
        "hostgroup": fields.String(description="Ramo/unidade (ex: Centro)"),
        "tipo": fields.String(description="DVR_DOWN | CAMERA_DOWN | ATIVO_DOWN"),
        "severidade": fields.String(description="critical | warning"),
        "msg": fields.String(description="Mensagem formatada do alerta"),
        "filhos_afetados": fields.Integer(allow_null=True, description="Câmeras afetadas (só DVR_DOWN)"),
        "created_at": fields.String(description="ISO-8601 do primeiro disparo"),
        "updated_at": fields.String(description="ISO-8601 da última atualização"),
    },
)

_summary_model = ns.model(
    "AlertsSummary",
    {
        "total": fields.Integer(description="Total de alertas ativos"),
        "critical": fields.Integer(description="Alertas críticos (DVR DOWN)"),
        "warning": fields.Integer(description="Alertas de aviso"),
        "alertas": fields.List(fields.Nested(_alert_model)),
    },
)


@ns.route("/active")
class ActiveAlertsResource(Resource):
    @ns.marshal_with(_summary_model)
    @ns.doc(
        description=(
            "Lista todos os alertas ativos ordenados por severidade e data. "
            "Consumido pelo dashboard e pelas telas de TV CFTV."
        ),
        params={
            "tipo": "Filtrar por tipo: DVR_DOWN | CAMERA_DOWN | ATIVO_DOWN",
            "ramo": "Filtrar por ramo (ex: Centro)",
        },
    )
    def get(self) -> dict:
        """Retorna alertas ativos com resumo por severidade."""
        from flask import request

        tipo_filtro: str | None = request.args.get("tipo")
        ramo_filtro: str | None = request.args.get("ramo")

        with get_session() as session:
            query = session.query(ActiveAlert)
            if tipo_filtro:
                query = query.filter(ActiveAlert.tipo == tipo_filtro.upper())
            if ramo_filtro:
                query = query.filter(ActiveAlert.hostgroup == ramo_filtro)

            # critical primeiro, depois por data mais recente
            alertas = query.order_by(
                ActiveAlert.severidade.desc(),
                ActiveAlert.created_at.desc(),
            ).all()

        total = len(alertas)
        n_critical = sum(1 for a in alertas if a.severidade == "critical")
        n_warning = total - n_critical

        def _fmt(a: ActiveAlert) -> dict:
            return {
                "host_id": a.host_id,
                "host_name": a.host_name,
                "hostgroup": a.hostgroup,
                "tipo": a.tipo,
                "severidade": a.severidade,
                "msg": a.msg,
                "filhos_afetados": a.filhos_afetados,
                "created_at": a.created_at.isoformat() if a.created_at else None,
                "updated_at": a.updated_at.isoformat() if a.updated_at else None,
            }

        log.info("alertas_ativos_consultados", total=total, critical=n_critical)

        return {
            "total": total,
            "critical": n_critical,
            "warning": n_warning,
            "alertas": [_fmt(a) for a in alertas],
        }
