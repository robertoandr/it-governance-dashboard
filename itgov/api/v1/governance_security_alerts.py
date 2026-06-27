"""Namespace Flask-RESTX para o pilar Endpoint — Alertas de Segurança (Defender).

Endpoint:
  GET /governance/security-alerts -> SecurityAlertsSummary

KPI-END-01: alertas abertos ha mais de 24h no Microsoft Defender.
Fonte primaria: InfluxDB gov_security_alerts (escrito pelo security_alerts_collector a cada 1h).
Cache: TTL 300s em memoria, mesmo padrao dos demais modulos governance.
"""

from __future__ import annotations

import threading
import time

import structlog
from flask_restx import Namespace, Resource, fields

log = structlog.get_logger(__name__)

ns = Namespace("governance_security_alerts", description="Alertas de Segurança M365/Defender (KPI-END-01)")

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


security_alerts_model = ns.model(
    "SecurityAlertsSummary",
    {
        "enabled": fields.Boolean(description="True se dados estao disponiveis"),
        "total_open": fields.Integer(allow_null=True, description="Total de alertas abertos"),
        "high": fields.Integer(allow_null=True, description="Alertas de severidade alta"),
        "medium": fields.Integer(allow_null=True, description="Alertas de severidade media"),
        "low": fields.Integer(allow_null=True, description="Alertas de severidade baixa"),
        "older_than_24h": fields.Integer(allow_null=True, description="Alertas abertos ha mais de 24h (KPI-END-01)"),
        "source": fields.String(description="Fonte dos dados"),
        "last_collected": fields.String(allow_null=True, description="ISO 8601 da ultima coleta"),
    },
)


def _buscar_do_influxdb() -> dict:
    """Le resumo de alertas do InfluxDB (measurement gov_security_alerts)."""
    try:
        from app.services.influxdb_provider import InfluxDBMetricsProvider

        provider = InfluxDBMetricsProvider()
        flux = f"""
from(bucket: "{provider._bucket_raw}")
  |> range(start: -24h)
  |> filter(fn: (r) => r._measurement == "gov_security_alerts")
  |> last()
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
"""
        rows = provider._query(flux)
        if not rows:
            return {"enabled": False, "source": "no_data"}
        row = rows[-1]
        return {
            "enabled": True,
            "total_open": int(row.get("total_open", 0) or 0),
            "high": int(row.get("high", 0) or 0),
            "medium": int(row.get("medium", 0) or 0),
            "low": int(row.get("low", 0) or 0),
            "older_than_24h": int(row.get("older_than_24h", 0) or 0),
            "source": "influxdb",
            "last_collected": str(row.get("_time", "")) or None,
        }
    except Exception as exc:
        log.warning("gov_security_alerts.influxdb_failed", error=str(exc))
        return {"enabled": False, "source": "influxdb_error", "error": str(exc)}


def _obter_dados() -> dict:
    cached = _ler_cache()
    if cached is not None:
        log.debug("gov_security_alerts.cache.hit")
        return cached

    log.info("gov_security_alerts.cache.miss")
    dados = _buscar_do_influxdb()
    _gravar_cache(dados)
    return dados


def get_cached_security_alerts_summary() -> dict:
    """Wrapper publico de _obter_dados — usado pela view HTML."""
    return _obter_dados()


@ns.route("/security-alerts")
class SecurityAlertsSummaryResource(Resource):
    """GET /governance/security-alerts — retorna resumo de alertas do Defender."""

    @ns.doc("get_security_alerts_summary")
    @ns.marshal_with(security_alerts_model, code=200)
    def get(self) -> dict:
        """Retorna alertas de segurança abertos no Microsoft Defender (KPI-END-01)."""
        return _obter_dados()
