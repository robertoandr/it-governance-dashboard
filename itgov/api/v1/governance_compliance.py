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

compliance_summary_model = ns.model(
    "ComplianceSummary",
    {
        "current_score": fields.Float(allow_null=True),
        "max_score": fields.Float(allow_null=True),
        "pct": fields.Float(allow_null=True, description="% do Secure Score, ou null se sem dados"),
        "category_breakdown": fields.Raw,
        "recomendacoes": fields.List(fields.Nested(ns.model("RecomendacaoControle", recomendacao_fields))),
    },
)


def _buscar_do_graph() -> dict:
    from itgov.services.compliance_service import calcular_resumo_compliance
    from itgov.services.secure_score_graph_client import SecureScoreGraphClient

    client = SecureScoreGraphClient()
    secure_score = asyncio.run(client.get_latest_secure_score())
    return calcular_resumo_compliance(secure_score).model_dump()


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
