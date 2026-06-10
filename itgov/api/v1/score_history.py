"""Flask-RESTX namespace para histórico de score de governança.

Expõe tendência temporal do score global e por pilar.
Cada ponto SEMPRE carrega coverage e data_source para que o frontend
possa distinguir score estimado de score real no gráfico histórico
(requisito anti-mentira temporal do Pacote II).

Endpoints:
  GET /api/v1/score/history?days=<1–730>          → tendência global
  GET /api/v1/score/history/<pillar_id>?days=<N>  → tendência do pilar
"""

from __future__ import annotations

from contextlib import contextmanager

import structlog
from flask import request
from flask_restx import Namespace, Resource, fields

from app.models.governance import PillarID
from itgov.services.trend_service import TrendService

log = structlog.get_logger(__name__)

ns = Namespace("score", description="Histórico de score de governança")

# ── Swagger models ────────────────────────────────────────────────────────────

_global_point = ns.model(
    "GlobalScorePoint",
    {
        "ts": fields.String(description="Timestamp ISO-8601 do snapshot"),
        "global_score": fields.Float(description="Score global ponderado (0–100)"),
        "status": fields.String(description="OPERACIONAL | DEGRADADO | CRÍTICO"),
        "trend": fields.String(description="up | down | stable"),
        "coverage_real": fields.Integer(description="Pilares com dados reais"),
        "coverage_total": fields.Integer(description="Total de pilares avaliados"),
    },
)

_global_history = ns.model(
    "GlobalScoreHistory",
    {
        "points": fields.List(fields.Nested(_global_point)),
        "count": fields.Integer(description="Número de pontos retornados"),
        "days": fields.Integer(description="Janela de consulta em dias"),
    },
)

_pillar_point = ns.model(
    "PillarScorePoint",
    {
        "ts": fields.String(description="Timestamp ISO-8601 do snapshot"),
        "score": fields.Float(description="Score do pilar (0–100)"),
        "weight": fields.Float(description="Peso do pilar no score global"),
        "data_source": fields.String(description="live | partial | manual | coming_soon"),
        "is_real": fields.Boolean(description="True se dado não é COMING_SOON"),
        "status": fields.String(description="OPERACIONAL | DEGRADADO | CRÍTICO"),
        "trend": fields.String(description="up | down | stable"),
    },
)

_pillar_history = ns.model(
    "PillarScoreHistory",
    {
        "pillar_id": fields.String(description="Identificador do pilar"),
        "points": fields.List(fields.Nested(_pillar_point)),
        "count": fields.Integer(description="Número de pontos retornados"),
        "days": fields.Integer(description="Janela de consulta em dias"),
    },
)

_error = ns.model(
    "ErrorMessage",
    {
        "error": fields.String(),
    },
)

# ── Valores válidos de pillar_id ──────────────────────────────────────────────

_VALID_PILLAR_IDS = {p.value for p in PillarID}
_DAYS_MIN = 1
_DAYS_MAX = 730
_DAYS_DEFAULT = 90

# ── Session / service factory ─────────────────────────────────────────────────


@contextmanager
def _svc():
    """Context manager que cede um TrendService com sessão ativa."""
    from itgov.db.session import get_session

    with get_session() as session:
        yield TrendService(session)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _parse_days() -> int:
    """Lê e valida o query param `days`. Aborta com 400 se inválido."""
    raw = request.args.get("days", _DAYS_DEFAULT, type=int)
    if raw is None or not (_DAYS_MIN <= raw <= _DAYS_MAX):
        ns.abort(400, f"days deve estar entre {_DAYS_MIN} e {_DAYS_MAX}")
    return raw


def _ts(dt) -> str:
    return dt.isoformat() if dt else ""


# ── Resources ─────────────────────────────────────────────────────────────────


@ns.route("/history")
class GlobalScoreHistory(Resource):
    @ns.doc(
        "global_score_history",
        params={"days": f"Janela de tempo em dias ({_DAYS_MIN}–{_DAYS_MAX}, padrão {_DAYS_DEFAULT})"},
    )
    @ns.marshal_with(_global_history)
    def get(self):
        """Retorna a tendência do score global de governança.

        Cada ponto inclui coverage_real/total para identificar se o score
        foi calculado com dados reais ou estimados.
        """
        days = _parse_days()

        with _svc() as svc:
            points = svc.global_history(days)

        log.info("score_history_global", days=days, pontos=len(points))

        return {
            "points": [
                {
                    "ts": _ts(p.ts),
                    "global_score": p.global_score,
                    "status": p.status,
                    "trend": p.trend,
                    "coverage_real": p.coverage_real,
                    "coverage_total": p.coverage_total,
                }
                for p in points
            ],
            "count": len(points),
            "days": days,
        }


@ns.route("/history/<string:pillar_id>")
@ns.param("pillar_id", "Identificador do pilar (ex. risk_management)")
class PillarScoreHistory(Resource):
    @ns.doc(
        "pillar_score_history",
        params={"days": f"Janela de tempo em dias ({_DAYS_MIN}–{_DAYS_MAX}, padrão {_DAYS_DEFAULT})"},
    )
    @ns.response(400, "pillar_id inválido ou days fora do intervalo", _error)
    @ns.response(404, "Nenhum histórico encontrado para o pilar", _error)
    @ns.marshal_with(_pillar_history)
    def get(self, pillar_id: str):
        """Retorna a tendência de score de um pilar específico.

        Cada ponto inclui data_source e is_real para que o frontend
        pinte pontos estimados diferente de pontos com dado real.

        404 se o pilar não possui nenhum snapshot no período.
        """
        if pillar_id not in _VALID_PILLAR_IDS:
            ns.abort(400, f"pillar_id inválido: '{pillar_id}'. Valores aceitos: {sorted(_VALID_PILLAR_IDS)}")

        days = _parse_days()

        with _svc() as svc:
            points = svc.pillar_history(pillar_id, days)

        if not points:
            ns.abort(404, f"Nenhum histórico encontrado para o pilar '{pillar_id}' nos últimos {days} dias")

        log.info("score_history_pilar", pillar_id=pillar_id, days=days, pontos=len(points))

        return {
            "pillar_id": pillar_id,
            "points": [
                {
                    "ts": _ts(p.ts),
                    "score": p.score,
                    "weight": p.weight,
                    "data_source": p.data_source,
                    "is_real": p.is_real,
                    "status": p.status,
                    "trend": p.trend,
                }
                for p in points
            ],
            "count": len(points),
            "days": days,
        }
