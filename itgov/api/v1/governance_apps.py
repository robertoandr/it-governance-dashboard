"""Namespace Flask-RESTX para o pilar Aplicativos — Governança de TI.

Endpoint:
  GET /governance/apps → AppRegistrationSummary

Cache: TTL de 300s em memória, mesmo padrão de governance_mfa.py.
"""

from __future__ import annotations

import asyncio
import threading
import time

import structlog
from flask_restx import Namespace, Resource, fields

log = structlog.get_logger(__name__)

ns = Namespace("governance_apps", description="Governança de App Registrations M365")

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


credencial_model_fields = {
    "app_display_name": fields.String,
    "app_id": fields.String,
    "tipo": fields.String(description="password | certificate"),
    "end_date_time": fields.String,
    "dias_restantes": fields.Integer(description="Negativo = já expirado"),
}

app_summary_model = ns.model(
    "AppRegistrationSummary",
    {
        "total_apps": fields.Integer,
        "secrets_expirando_30d": fields.Integer,
        "secrets_expirados": fields.Integer,
        "expirando": fields.List(fields.Nested(ns.model("CredencialExpirando", credencial_model_fields))),
    },
)


def _buscar_do_graph() -> dict:
    import os

    from itgov.services.app_registration_graph_client import AppRegistrationGraphClient
    from itgov.services.app_registration_service import calcular_resumo_apps

    tenant_id = (os.environ.get("AZURE_TENANT_ID") or "").strip()
    if not tenant_id:
        raise RuntimeError("AZURE_TENANT_ID não configurado")

    client = AppRegistrationGraphClient()
    apps = asyncio.run(client.get_applications(tenant_id))
    return calcular_resumo_apps(apps).model_dump(mode="json")


def _obter_dados() -> dict:
    cached = _ler_cache()
    if cached is not None:
        log.debug("gov_apps.cache.hit")
        return cached

    log.info("gov_apps.cache.miss")
    dados = _buscar_do_graph()
    _gravar_cache(dados)
    return dados


def get_cached_app_summary() -> dict:
    """Wrapper público de _obter_dados — usado pela view HTML em app/views/dashboards.py."""
    return _obter_dados()


@ns.route("/apps")
class GovernancaApps(Resource):
    @ns.marshal_with(app_summary_model)
    def get(self):
        """Retorna o resumo de governança de App Registrations."""
        try:
            return _obter_dados(), 200
        except RuntimeError:
            ns.abort(503, "Integração Graph não configurada")
        except Exception as exc:
            log.error("gov_apps.get.erro", erro=str(exc))
            ns.abort(500, "Erro ao buscar dados de apps")
