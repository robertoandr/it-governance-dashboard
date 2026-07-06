"""Namespace Flask-RESTX para o pilar Compliance — Governança de TI.

Endpoint:
  GET /governance/compliance → ComplianceSummary (Microsoft Secure Score)

Cache: TTL de 300s em memória, mesmo padrão de governance_mfa.py.
"""

from __future__ import annotations

import asyncio
import threading
import time

import structlog
from flask_restx import Namespace, Resource, fields

log = structlog.get_logger(__name__)

ns = Namespace("governance_compliance", description="Governança de Compliance M365 (Secure Score)")

_CACHE_TTL = 300

_cache_lock = threading.Lock()
_cache_dados: dict | None = None
_cache_ts: float = 0.0


def _cache_valido() -> bool:
    return _cache_dados is not None and (time.monotonic() - _cache_ts) < _CACHE_TTL


def _ler_cache() -> dict | None:
    with _cache_lock:
        return _cache_dados if _cache_valido() else None


def _gravar_cache(dados: dict) -> None:
    global _cache_dados, _cache_ts
    with _cache_lock:
        _cache_dados = dados
        _cache_ts = time.monotonic()


recomendacao_fields = {
    "control_name": fields.String,
    "categoria": fields.String,
    "descricao": fields.String,
    "score_pct": fields.Float,
}

controle_fields = {
    "control_name": fields.String,
    "title": fields.String,
    "categoria": fields.String,
    "max_score": fields.Float,
    "score": fields.Float,
    "status": fields.String,
    "acao": fields.String,
    "action_url": fields.String(allow_null=True),
}

historico_ponto_fields = {
    "time": fields.String,
    "pct": fields.Float,
}

compliance_summary_model = ns.model(
    "ComplianceSummary",
    {
        "current_score": fields.Float(allow_null=True),
        "max_score": fields.Float(allow_null=True),
        "pct": fields.Float(allow_null=True, description="% do Secure Score, ou null se sem dados"),
        "category_breakdown": fields.Raw,
        "recomendacoes": fields.List(fields.Nested(ns.model("RecomendacaoControle", recomendacao_fields))),
        "security_controls": fields.Raw(description="Status dos controles Safe Links, Safe Attachments e Audit Log"),
        "comparative_pct": fields.Float(
            allow_null=True, description="averageComparativeScores mais relevante (TotalSeats > AllTenants)"
        ),
        "comparative_basis": fields.String(allow_null=True, description="Basis usado em comparative_pct"),
        "controles": fields.List(
            fields.Nested(ns.model("ControlePendente", controle_fields)),
            description="Tabela completa de controles, ordenada por max_score DESC",
        ),
        "historico_90d": fields.List(
            fields.Nested(ns.model("HistoricoPonto", historico_ponto_fields)),
            description="Série do Secure Score (%) nos últimos 90 dias, via InfluxDB",
        ),
        "variacao_30d": fields.Float(allow_null=True, description="Variação em pontos % vs ~30 dias atrás"),
    },
)


def _buscar_historico_influx() -> list[dict]:
    """Lê a série histórica de pct do Secure Score (gov_m365_secure_score, 90d)."""
    try:
        from app.services.influxdb_provider import InfluxDBMetricsProvider

        provider = InfluxDBMetricsProvider()
        flux = f"""
from(bucket: "{provider._bucket_raw}")
  |> range(start: -90d)
  |> filter(fn: (r) => r._measurement == "gov_m365_secure_score")
  |> filter(fn: (r) => r._field == "pct")
  |> sort(columns: ["_time"])
"""
        rows = provider._query(flux)
        return [
            {
                "time": row["_time"].isoformat() if hasattr(row["_time"], "isoformat") else str(row["_time"]),
                "pct": float(row["_value"]),
            }
            for row in rows
            if row.get("_value") is not None
        ]
    except Exception as exc:
        log.warning("gov_compliance.historico_influx_failed", error=str(exc))
        return []


def _buscar_do_graph() -> dict:
    from itgov.services.compliance_service import calcular_resumo_compliance
    from itgov.services.secure_score_graph_client import SecureScoreGraphClient

    client = SecureScoreGraphClient()

    async def _fetch_all():
        secure_score = await client.get_latest_secure_score()
        try:
            control_profiles = await client.get_security_controls()
        except Exception as exc:
            log.warning("gov_compliance.security_controls_failed", error=str(exc))
            control_profiles = []
        return secure_score, control_profiles

    secure_score, control_profiles = asyncio.run(_fetch_all())
    historico_90d = _buscar_historico_influx()
    return calcular_resumo_compliance(
        secure_score,
        control_profiles=control_profiles,
        historico_90d=historico_90d,
    ).model_dump()


def _obter_dados() -> dict:
    cached = _ler_cache()
    if cached is not None:
        log.debug("gov_compliance.cache.hit")
        return cached

    log.info("gov_compliance.cache.miss")
    dados = _buscar_do_graph()
    _gravar_cache(dados)
    return dados


def get_cached_compliance_summary() -> dict:
    """Wrapper público de _obter_dados — usado pela view HTML em app/views/dashboards.py."""
    return _obter_dados()


@ns.route("/compliance")
class GovernancaCompliance(Resource):
    @ns.marshal_with(compliance_summary_model)
    def get(self):
        """Retorna o resumo de governança de Compliance (Secure Score)."""
        try:
            return _obter_dados(), 200
        except Exception as exc:
            log.error("gov_compliance.get.erro", erro=str(exc))
            ns.abort(500, "Erro ao buscar dados de compliance")
