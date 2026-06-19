"""Namespace Flask-RESTX para o pilar Dados (DLP/Sensitivity Labels) — Governança de TI.

Endpoint:
  GET /governance/data → DataGovernanceSummary

Cache: TTL de 300s em memória, mesmo padrão de governance_mfa.py.
"""

from __future__ import annotations

import asyncio
import threading
import time

import structlog
from flask_restx import Namespace, Resource, fields

log = structlog.get_logger(__name__)

ns = Namespace("governance_data", description="Governança de Dados M365 (Sensitivity Labels)")

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


label_fields = {
    "label_id": fields.String,
    "name": fields.String,
    "description": fields.String(allow_null=True),
    "is_active": fields.Boolean,
}

data_summary_model = ns.model(
    "DataGovernanceSummary",
    {
        "total_labels": fields.Integer,
        "labels": fields.List(fields.Nested(ns.model("SensitivityLabelInfo", label_fields))),
    },
)


def _buscar_do_graph() -> dict:
    from itgov.services.data_governance_service import calcular_resumo_dados
    from itgov.services.sensitivity_label_graph_client import SensitivityLabelGraphClient

    client = SensitivityLabelGraphClient()
    labels = asyncio.run(client.get_labels())
    return calcular_resumo_dados(labels).model_dump()


def _obter_dados() -> dict:
    cached = _ler_cache()
    if cached is not None:
        log.debug("gov_data.cache.hit")
        return cached

    log.info("gov_data.cache.miss")
    dados = _buscar_do_graph()
    _gravar_cache(dados)
    return dados


def get_cached_data_summary() -> dict:
    """Wrapper público de _obter_dados — usado pela view HTML em app/views/dashboards.py."""
    return _obter_dados()


@ns.route("/data")
class GovernancaDados(Resource):
    @ns.marshal_with(data_summary_model)
    def get(self):
        """Retorna o resumo de governança de dados (sensitivity labels)."""
        try:
            return _obter_dados(), 200
        except Exception as exc:
            log.error("gov_data.get.erro", erro=str(exc))
            ns.abort(500, "Erro ao buscar dados de governança de dados")
