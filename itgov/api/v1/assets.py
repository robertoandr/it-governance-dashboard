"""Flask-RESTX namespace para status de ativos monitorados via Zabbix.

Endpoints leem EXCLUSIVAMENTE a tabela local (asset_status / asset_status_history).
Nenhum endpoint chama o Zabbix ao vivo — isso é responsabilidade do job de polling.

  GET /api/v1/assets                  → lista de ativos com filtros opcionais
  GET /api/v1/assets/summary          → contadores por tipo e status
  GET /api/v1/assets/history?days=7   → transições recentes (até 730 dias)
"""

from __future__ import annotations

from contextlib import contextmanager

import structlog
from flask_restx import Namespace, Resource, fields

from itgov.services.asset_status_service import AssetStatusService

log = structlog.get_logger(__name__)

ns = Namespace("assets", description="Status de ativos monitorados via Zabbix")

# ── Swagger models ─────────────────────────────────────────────────────────────

_asset_model = ns.model(
    "Asset",
    {
        "hostid": fields.String(description="ID do host no Zabbix"),
        "host": fields.String(description="Technical name do host"),
        "name": fields.String(description="Visible name do host"),
        "asset_type": fields.String(description="camera | server | other"),
        "status": fields.String(description="up | down | unknown"),
        "last_change": fields.String(allow_null=True, description="ISO-8601 da última mudança de status"),
        "updated_at": fields.String(description="ISO-8601 do último poll"),
    },
)

_asset_list_model = ns.model(
    "AssetList",
    {
        "items": fields.List(fields.Nested(_asset_model)),
        "total": fields.Integer(description="Total de ativos retornados"),
    },
)

_summary_tipo_model = ns.model(
    "AssetSummaryTipo",
    {
        "up": fields.Integer(description="Ativos up neste tipo"),
        "down": fields.Integer(description="Ativos down neste tipo"),
        "unknown": fields.Integer(description="Ativos unknown neste tipo"),
        "total": fields.Integer(description="Total neste tipo"),
    },
)

_summary_model = ns.model(
    "AssetSummary",
    {
        "total": fields.Integer(description="Total geral de ativos"),
        "por_status": fields.Raw(description="Contagem global por status"),
        "por_tipo": fields.Raw(description="Contagem global por tipo"),
        "por_tipo_status": fields.Raw(description="Breakdown: tipo → {status: qtd}"),
    },
)

_history_item = ns.model(
    "AssetHistoryItem",
    {
        "id": fields.String(description="UUID da transição"),
        "hostid": fields.String(description="ID do host no Zabbix"),
        "asset_type": fields.String(description="Tipo do ativo no momento da transição"),
        "from_status": fields.String(description="Status anterior"),
        "to_status": fields.String(description="Novo status"),
        "changed_at": fields.String(description="ISO-8601 UTC da mudança"),
        "duration_seconds": fields.Integer(allow_null=True, description="Segundos no status anterior"),
    },
)

_history_model = ns.model(
    "AssetHistory",
    {
        "items": fields.List(fields.Nested(_history_item)),
        "total": fields.Integer(description="Número de transições retornadas"),
        "days": fields.Integer(description="Janela de consulta em dias"),
    },
)

_error_model = ns.model("AssetError", {"error": fields.String()})

# ── Query parsers ──────────────────────────────────────────────────────────────

_list_parser = ns.parser()
_list_parser.add_argument("asset_type", type=str, location="args", help="Filtrar por tipo")
_list_parser.add_argument("status", type=str, location="args", help="Filtrar por status (up|down|unknown)")

_history_parser = ns.parser()
_history_parser.add_argument("days", type=int, default=7, location="args", help="Janela em dias (1–730)")
_history_parser.add_argument("hostid", type=str, location="args", help="Filtrar por hostid específico")
_history_parser.add_argument("asset_type", type=str, location="args", help="Filtrar por tipo")

_DAYS_MIN = 1
_DAYS_MAX = 730

# ── Session factory ────────────────────────────────────────────────────────────


@contextmanager
def _svc():
    """Cede um AssetStatusService com sessão SQLAlchemy ativa."""
    from itgov.db.session import get_session

    with get_session() as session:
        yield AssetStatusService(session)


# ── Helpers de serialização ────────────────────────────────────────────────────


def _ts(dt) -> str | None:
    return dt.isoformat() if dt else None


def _ser_asset(a) -> dict:
    return {
        "hostid": a.hostid,
        "host": a.host,
        "name": a.name,
        "asset_type": a.asset_type,
        "status": a.status,
        "last_change": _ts(a.last_change),
        "updated_at": _ts(a.updated_at),
    }


def _ser_history(h) -> dict:
    return {
        "id": str(h.id),
        "hostid": h.hostid,
        "asset_type": h.asset_type,
        "from_status": h.from_status,
        "to_status": h.to_status,
        "changed_at": _ts(h.changed_at),
        "duration_seconds": h.duration_seconds,
    }


# ── Resources ──────────────────────────────────────────────────────────────────


@ns.route("")
class AssetCollection(Resource):
    """Lista de ativos com status atual — lê a tabela local, nunca o Zabbix."""

    @ns.doc("list_assets")
    @ns.expect(_list_parser)
    @ns.marshal_with(_asset_list_model)
    def get(self):
        """Retorna todos os ativos monitorados com filtros opcionais por tipo e status."""
        args = _list_parser.parse_args()
        with _svc() as svc:
            items = svc.list_assets(
                asset_type=args.get("asset_type"),
                status=args.get("status"),
            )
        return {"items": [_ser_asset(a) for a in items], "total": len(items)}


@ns.route("/summary")
class AssetSummaryResource(Resource):
    """Resumo de contadores — útil para widgets de dashboard."""

    @ns.doc("asset_summary")
    @ns.marshal_with(_summary_model)
    def get(self):
        """Retorna contagens de ativos agrupadas por tipo e status."""
        with _svc() as svc:
            s = svc.summary()
        return {
            "total": s.total,
            "por_status": s.por_status,
            "por_tipo": s.por_tipo,
            "por_tipo_status": s.por_tipo_status,
        }


@ns.route("/history")
class AssetHistoryResource(Resource):
    """Histórico de transições de status — registra quedas e retornos."""

    @ns.doc(
        "asset_history",
        params={
            "days": f"Janela de tempo em dias ({_DAYS_MIN}–{_DAYS_MAX}, padrão 7)",
            "hostid": "Filtrar por host específico",
            "asset_type": "Filtrar por tipo de ativo",
        },
    )
    @ns.expect(_history_parser)
    @ns.response(400, "Parâmetro days inválido", _error_model)
    @ns.marshal_with(_history_model)
    def get(self):
        """Retorna transições de status recentes, ordenadas pela mais recente primeiro."""
        args = _history_parser.parse_args()
        days = args.get("days", 7)
        if days is None or not (_DAYS_MIN <= days <= _DAYS_MAX):
            ns.abort(400, f"days deve estar entre {_DAYS_MIN} e {_DAYS_MAX}")

        with _svc() as svc:
            items = svc.history(
                days=days,
                hostid=args.get("hostid"),
                asset_type=args.get("asset_type"),
            )

        log.info("asset_history_consultado", days=days, transicoes=len(items))
        return {"items": [_ser_history(h) for h in items], "total": len(items), "days": days}
