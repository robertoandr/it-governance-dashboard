"""Namespace Flask-RESTX para o pilar Dispositivos — Governança de TI.

Endpoint:
  GET /governance/devices → DeviceSummary

Cache: TTL de 300s em memória, mesmo padrão de governance_mfa.py.
"""

from __future__ import annotations

import asyncio
import threading
import time

import structlog
from flask_restx import Namespace, Resource, fields

log = structlog.get_logger(__name__)

ns = Namespace("governance_devices", description="Governança de Dispositivos M365")

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


device_summary_model = ns.model(
    "DeviceSummary",
    {
        "total_devices": fields.Integer,
        "stale_90d": fields.Integer(description="Sem sign-in nos últimos 90 dias"),
        "managed_pct": fields.Float(allow_null=True, description="% gerenciado via Intune, ou null se não aplicável"),
        "os_distribution": fields.Raw,
        "trust_type_distribution": fields.Raw,
    },
)


def _buscar_do_graph() -> dict:
    import os

    from itgov.services.device_graph_client import DeviceGraphClient
    from itgov.services.device_service import calcular_resumo_dispositivos

    tenant_id = (os.environ.get("AZURE_TENANT_ID") or "").strip()
    if not tenant_id:
        raise RuntimeError("AZURE_TENANT_ID não configurado")

    client = DeviceGraphClient()
    devices = asyncio.run(client.get_devices(tenant_id))
    return calcular_resumo_dispositivos(devices).model_dump()


def _obter_dados() -> dict:
    cached = _ler_cache()
    if cached is not None:
        log.debug("gov_devices.cache.hit")
        return cached

    log.info("gov_devices.cache.miss")
    dados = _buscar_do_graph()
    _gravar_cache(dados)
    return dados


def get_cached_device_summary() -> dict:
    """Wrapper público de _obter_dados — usado pela view HTML em app/views/dashboards.py."""
    return _obter_dados()


@ns.route("/devices")
class GovernancaDevices(Resource):
    @ns.marshal_with(device_summary_model)
    def get(self):
        """Retorna o resumo de governança de dispositivos."""
        try:
            return _obter_dados(), 200
        except RuntimeError:
            ns.abort(503, "Integração Graph não configurada")
        except Exception as exc:
            log.error("gov_devices.get.erro", erro=str(exc))
            ns.abort(500, "Erro ao buscar dados de dispositivos")
