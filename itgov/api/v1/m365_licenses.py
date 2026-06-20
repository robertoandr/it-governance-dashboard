"""API de Licenças M365 — uso vs disponível + custos manuais.

Fonte primária: InfluxDB measurement m365_licenses (escrito pelo entra_id_collector).
Custos e renovação: app/data/license_costs.json (entrada manual via UI).
Cache: TTL 300s em memória.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)

_CACHE_TTL = 300
_cache_lock = threading.Lock()
_cache_dados: dict | None = None
_cache_ts: float = 0.0

_COSTS_FILE = Path(__file__).parent.parent.parent.parent / "app" / "data" / "license_costs.json"

# SKUs gratuitas/trial que têm total = 10000 ou similares não fazem sentido para custo
_FREE_SKUS = {
    "POWERAPPS_DEV",
    "FLOW_FREE",
    "POWER_BI_STANDARD",
    "WINDOWS_STORE",
    "Dynamics_365_Customer_Service_Enterprise_viral_trial",
    "Microsoft_Teams_Exploratory_Dept",
    "Power_Pages_vTrial_for_Makers",
}


def _load_costs() -> dict[str, Any]:
    try:
        if _COSTS_FILE.exists():
            return json.loads(_COSTS_FILE.read_text())
    except Exception as exc:
        log.warning("license_costs_load_failed", error=str(exc))
    return {}


def save_costs(data: dict) -> None:
    try:
        _COSTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _COSTS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        _invalidar_cache()
    except Exception as exc:
        log.warning("license_costs_save_failed", error=str(exc))
        raise


def _invalidar_cache() -> None:
    global _cache_dados, _cache_ts
    with _cache_lock:
        _cache_dados = None
        _cache_ts = 0.0


def _cache_valido() -> bool:
    return _cache_dados is not None and (time.monotonic() - _cache_ts) < _CACHE_TTL


def _query_influxdb() -> list[dict]:
    """Lê últimos registros de m365_licenses do InfluxDB e agrupa por sku_name."""
    try:
        from app.services.influxdb_provider import InfluxDBMetricsProvider

        provider = InfluxDBMetricsProvider()
        flux = f"""
from(bucket: "{provider._bucket_raw}")
  |> range(start: -24h)
  |> filter(fn: (r) => r._measurement == "m365_licenses")
  |> group(columns: ["sku_id", "sku_name", "_field"])
  |> last()
  |> keep(columns: ["sku_id", "sku_name", "_field", "_value", "_time"])
"""
        rows = provider._query(flux)
        # Aggregate per-field rows into one dict per SKU
        skus: dict[str, dict] = {}
        for row in rows:
            key = str(row.get("sku_name", ""))
            if key not in skus:
                skus[key] = {
                    "sku_id": str(row.get("sku_id", "")),
                    "sku_name": key,
                    "_time": row.get("_time"),
                }
            field = str(row.get("_field", ""))
            if field:
                skus[key][field] = row.get("_value", 0)
        return list(skus.values())
    except Exception as exc:
        log.warning("m365_licenses_influx_failed", error=str(exc))
        return []


def get_licenses_summary() -> dict:
    global _cache_dados, _cache_ts
    with _cache_lock:
        if _cache_valido():
            log.debug("m365_licenses.cache.hit")
            return _cache_dados  # type: ignore[return-value]

    log.info("m365_licenses.cache.miss")
    rows = _query_influxdb()
    costs = _load_costs()

    licenses = []
    total_consumed = 0
    total_seats = 0
    custo_mensal_total = 0.0
    desperdicio_total = 0.0

    for row in rows:
        sku_id = str(row.get("sku_id", ""))
        sku_name = str(row.get("sku_name", ""))
        consumed = int(row.get("consumed", 0) or 0)
        total = int(row.get("total", 0) or 0)
        available = int(row.get("available", 0) or 0)
        warning_units = int(row.get("warning_units", 0) or 0)

        cost_info = costs.get(sku_name, {})
        friendly_name = cost_info.get("friendly_name", sku_name)
        cost_unit = float(cost_info.get("cost_per_unit_brl", 0.0) or 0.0)
        renewal_date = cost_info.get("renewal_date", "")
        billing_cycle = cost_info.get("billing_cycle", "monthly")
        notes = cost_info.get("notes", "")

        custo_mensal = round(consumed * cost_unit, 2)
        desperdicio = round(available * cost_unit, 2)
        uso_pct = round(consumed / total * 100, 1) if total > 0 else 0.0
        over_provisioned = consumed > total

        is_free = sku_name in _FREE_SKUS or total >= 10000

        if not is_free:
            total_consumed += consumed
            total_seats += total
            custo_mensal_total += custo_mensal
            desperdicio_total += desperdicio

        licenses.append(
            {
                "sku_id": sku_id,
                "sku_name": sku_name,
                "friendly_name": friendly_name,
                "consumed": consumed,
                "total": total,
                "available": available,
                "warning_units": warning_units,
                "uso_pct": uso_pct,
                "over_provisioned": over_provisioned,
                "is_free": is_free,
                "cost_per_unit_brl": cost_unit,
                "custo_mensal_brl": custo_mensal,
                "desperdicio_brl": desperdicio,
                "renewal_date": renewal_date,
                "billing_cycle": billing_cycle,
                "notes": notes,
                "last_updated": str(row.get("_time", ""))[:19] or None,
            }
        )

    licenses.sort(key=lambda x: (-x["desperdicio_brl"], -x["consumed"]))

    result: dict = {
        "has_data": len(licenses) > 0,
        "licenses": licenses,
        "summary": {
            "total_skus": len([lic for lic in licenses if not lic["is_free"]]),
            "total_skus_free": len([lic for lic in licenses if lic["is_free"]]),
            "total_consumed": total_consumed,
            "total_seats": total_seats,
            "uso_pct": round(total_consumed / total_seats * 100, 1) if total_seats else 0.0,
            "custo_mensal_total_brl": round(custo_mensal_total, 2),
            "desperdicio_total_brl": round(desperdicio_total, 2),
            "skus_com_custo": len([lic for lic in licenses if lic["cost_per_unit_brl"] > 0]),
        },
        "costs_file": str(_COSTS_FILE),
    }

    with _cache_lock:
        _cache_dados = result
        _cache_ts = time.monotonic()

    return result
