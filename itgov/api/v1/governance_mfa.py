"""Namespace Flask-RESTX para o pilar MFA — Governança de TI.

Endpoint:
  GET /governance/mfa → MFASummary (adoção de MFA via Entra ID / InfluxDB)

Fonte primária: InfluxDB gov_entra_summary.mfa_enabled_pct (escrito pelo
entra_id_collector a cada 6h).  Fallback: dados ausentes retornam enabled=false.
Cache: TTL de 300s em memória, mesmo padrão dos demais módulos governance.
"""

from __future__ import annotations

import threading
import time

import structlog
from flask_restx import Namespace, Resource, fields

log = structlog.get_logger(__name__)

ns = Namespace("governance_mfa", description="Governança de MFA (Entra ID / M365)")

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


mfa_summary_model = ns.model(
    "MFASummary",
    {
        "enabled": fields.Boolean(description="True se dados MFA estão disponíveis"),
        "mfa_enabled_pct": fields.Float(allow_null=True, description="% usuários com MFA registrado"),
        "total_users": fields.Integer(allow_null=True),
        "guest_users": fields.Integer(allow_null=True),
        "ca_policies_count": fields.Integer(allow_null=True, description="Políticas de Acesso Condicional"),
        "stale_accounts_90d": fields.Integer(allow_null=True, description="Contas sem login há 90+ dias"),
        "source": fields.String(description="Fonte dos dados"),
        "last_collected": fields.String(allow_null=True, description="ISO 8601 da última coleta"),
    },
)


def _buscar_do_influxdb() -> dict:
    """Lê resumo MFA do InfluxDB (measurement gov_entra_summary)."""
    try:
        from app.services.influxdb_provider import InfluxDBMetricsProvider

        provider = InfluxDBMetricsProvider()
        flux = f"""
from(bucket: "{provider._bucket_raw}")
  |> range(start: -24h)
  |> filter(fn: (r) => r._measurement == "gov_entra_summary")
  |> last()
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
"""
        rows = provider._query(flux)
        if not rows:
            return {"enabled": False, "source": "no_data"}
        row = rows[-1]
        return {
            "enabled": True,
            "mfa_enabled_pct": float(row.get("mfa_enabled_pct", 0.0)),
            "total_users": int(row.get("total_users", 0) or 0),
            "guest_users": int(row.get("guest_users", 0) or 0),
            "ca_policies_count": int(row.get("ca_policies_count", 0) or 0),
            "stale_accounts_90d": int(row.get("stale_accounts_90d", 0) or 0),
            "source": "influxdb",
            "last_collected": str(row.get("_time", "")) or None,
        }
    except Exception as exc:
        log.warning("gov_mfa.influxdb_failed", error=str(exc))
        return {"enabled": False, "source": "influxdb_error", "error": str(exc)}


def _obter_dados() -> dict:
    cached = _ler_cache()
    if cached is not None:
        log.debug("gov_mfa.cache.hit")
        return cached

    log.info("gov_mfa.cache.miss")
    dados = _buscar_do_influxdb()
    _gravar_cache(dados)
    return dados


@ns.route("/mfa")
class MFASummaryResource(Resource):
    """GET /governance/mfa — retorna resumo de adoção de MFA."""

    @ns.doc("get_mfa_summary")
    @ns.marshal_with(mfa_summary_model, code=200)
    def get(self) -> dict:
        """Retorna adoção de MFA e métricas de identidade do Entra ID."""
        return _obter_dados()
