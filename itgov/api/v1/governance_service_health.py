"""Namespace Flask-RESTX para Service Health — Governança M365.

Endpoint: GET /governance/service-health
Cache TTL: 300s (mesmo padrão dos outros pilares de governança)
Permissão Graph: ServiceHealth.Read.All (Application)
"""

from __future__ import annotations

import asyncio
import threading
import time

import structlog
from flask_restx import Namespace, Resource, fields

log = structlog.get_logger(__name__)

ns = Namespace("governance_service_health", description="Service Health M365 (status dos serviços)")

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


service_status_model = ns.model(
    "ServiceStatus",
    {
        "service_id": fields.String,
        "display_name": fields.String,
        "status_raw": fields.String,
        "status_label": fields.String,
        "status_code": fields.Integer,
    },
)

summary_model = ns.model(
    "ServiceHealthSummary",
    {
        "total": fields.Integer,
        "ok": fields.Integer,
        "warning": fields.Integer,
        "critical": fields.Integer,
        "all_operational": fields.Boolean,
        "services": fields.List(fields.Nested(service_status_model)),
    },
)


def _buscar_do_graph() -> dict:
    from itgov.services.service_health_graph_client import ServiceHealthGraphClient
    from itgov.services.service_health_service import calcular_resumo_service_health

    client = ServiceHealthGraphClient()
    raw = asyncio.run(client.get_health_overviews())
    return calcular_resumo_service_health(raw).model_dump()


def _obter_dados() -> dict:
    cached = _ler_cache()
    if cached is not None:
        log.debug("gov_service_health.cache.hit")
        return cached

    log.info("gov_service_health.cache.miss")
    dados = _buscar_do_graph()
    _gravar_cache(dados)
    return dados


def get_cached_service_health_summary() -> dict:
    """Wrapper público para uso pelas views HTML."""
    return _obter_dados()


@ns.route("/service-health")
class GovernancaServiceHealth(Resource):
    @ns.marshal_with(summary_model)
    def get(self):
        """Retorna o status atual dos serviços Microsoft 365."""
        try:
            return _obter_dados(), 200
        except Exception as exc:
            log.error("gov_service_health.get.erro", erro=str(exc))
            ns.abort(500, "Erro ao buscar Service Health")
