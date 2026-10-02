"""API de Licenças M365 — uso vs disponível + custos manuais.

Fonte primária: InfluxDB measurement m365_licenses (escrito pelo entra_id_collector).
Custos e renovação: LICENSE_COSTS_PATH (default /app/data/license_costs.json, no volume
persistente), com seed inicial a partir de app/data/license_costs.json (entrada manual via UI).
Cache: TTL 300s em memória.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from datetime import date
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)

_CACHE_TTL = 300
_cache_lock = threading.Lock()
_cache_dados: dict | None = None
_cache_ts: float = 0.0

# FIX(LIC-01): path é configurável via LICENSE_COSTS_PATH (default: volume /app/data,
# montado pelo docker-compose). O arquivo versionado em
# <repo_root>/app/data/license_costs.seed.json serve como seed inicial — ver
# ensure_costs_file(). É um arquivo distinto de _COSTS_FILE (que é saída local
# de runtime, gitignored) justamente para poder ser versionado no git.
_COSTS_FILE = Path(os.environ.get("LICENSE_COSTS_PATH", "/app/data/license_costs.json"))
_SEED_FILE = Path(__file__).parent.parent.parent.parent / "app" / "data" / "license_costs.seed.json"


def ensure_costs_file() -> None:
    """Popula _COSTS_FILE a partir do seed versionado no primeiro boot (volume vazio)."""
    try:
        if not _COSTS_FILE.exists() and _SEED_FILE.exists():
            _COSTS_FILE.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(_SEED_FILE, _COSTS_FILE)
            log.info("license_costs_seeded", path=str(_COSTS_FILE))
    except Exception as exc:
        log.warning("license_costs_seed_failed", error=str(exc))


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


# SKUs que não são licença de usuário (unidade = GB, capacidade do tenant).
_CAPACIDADE_SKUS = {"SHAREPOINTSTORAGE"}

# Nome amigável quando o license_costs.json não tem um (o arquivo tem precedência).
_NOMES_PADRAO = {
    "SPB": "Microsoft 365 Business Premium",
    "THREAT_INTELLIGENCE": "Defender for Office 365 Plano 2",
    "Microsoft_Intune_Suite": "Microsoft Intune Suite",
    "PROJECT_PLAN3_DEPT": "Project Plano 3 (Departamentos)",
    "MICROSOFT_365_COPILOT_BUSINESS_DEPT": "Microsoft 365 Copilot Business (Departamentos)",
    "SHAREPOINTSTORAGE": "Armazenamento extra SharePoint (GB)",
}

# Preço de lista Microsoft em R$/usuário/mês, compromisso anual, sem impostos —
# usado como ESTIMATIVA enquanto o custo real não é informado na tela.
# Fontes: tabela Microsoft vigente desde 01/07/2026 (Business, Apps, Copilot);
# Power BI Pro desde 01/04/2025; Visio Plano 1 (página de planos do Visio).
# SKU sem preço de lista em R$ confirmado fica fora — a tela pede o valor.
PRECOS_LISTA_BRL: dict[str, float] = {
    "O365_BUSINESS_ESSENTIALS": 33.40,
    "O365_BUSINESS": 57.30,
    "O365_BUSINESS_PREMIUM": 80.20,
    "SPB": 126.00,
    "MICROSOFT_365_COPILOT_BUSINESS_DEPT": 120.20,
    "POWER_BI_PRO": 80.00,
    "VISIOONLINE_PLAN1": 28.60,
}


def _categoria(sku_name: str, total: int, assinatura: Any) -> str:
    """pago | teste | gratuito | capacidade — só 'pago' entra nos totais."""
    if sku_name in _FREE_SKUS or total >= 10000:
        return "gratuito"
    if sku_name in _CAPACIDADE_SKUS:
        return "capacidade"
    if assinatura is not None and assinatura.teste:
        return "teste"
    return "pago"


def _dias_ate(data_iso: str) -> int | None:
    """Dias de hoje até a data (AAAA-MM-DD...); None se vazia ou inválida."""
    try:
        return (date.fromisoformat(str(data_iso)[:10]) - date.today()).days
    except ValueError:
        return None


def _assinaturas() -> dict:
    """Teste/renovação por SKU via Graph; vazio se indisponível (tela segue com o manual)."""
    try:
        from itgov.services.m365_assinaturas import obter_assinaturas

        return obter_assinaturas()
    except (ImportError, RuntimeError) as exc:
        log.warning("m365_licenses.assinaturas_indisponiveis", error=str(exc))
        return {}


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
    assinaturas = _assinaturas()

    licenses = []
    total_consumed = 0
    total_seats = 0
    custo_mensal_total = 0.0
    custo_estimado_total = 0.0
    desperdicio_total = 0.0

    for row in rows:
        sku_id = str(row.get("sku_id", ""))
        sku_name = str(row.get("sku_name", ""))
        consumed = int(row.get("consumed", 0) or 0)
        total = int(row.get("total", 0) or 0)
        available = int(row.get("available", 0) or 0)
        warning_units = int(row.get("warning_units", 0) or 0)

        cost_info = costs.get(sku_name, {})
        has_friendly_name = "friendly_name" in cost_info or sku_name in _NOMES_PADRAO
        friendly_name = cost_info.get("friendly_name") or _NOMES_PADRAO.get(sku_name, sku_name)
        assinatura = assinaturas.get(sku_name)
        categoria = _categoria(sku_name, total, assinatura)

        # Custo informado na tela vale mais que o preço de lista (estimado).
        cost_informado = float(cost_info.get("cost_per_unit_brl", 0.0) or 0.0)
        if cost_informado > 0:
            cost_unit, preco_origem = cost_informado, "informado"
        elif categoria == "pago" and sku_name in PRECOS_LISTA_BRL:
            cost_unit, preco_origem = PRECOS_LISTA_BRL[sku_name], "estimado"
        elif categoria == "pago":
            cost_unit, preco_origem = 0.0, "sem_preco"
        else:
            cost_unit, preco_origem = 0.0, "nao_se_aplica"

        renewal_date = cost_info.get("renewal_date", "")
        renewal_origem = "informado" if renewal_date else ""
        if not renewal_date and assinatura is not None and assinatura.renovacao:
            renewal_date, renewal_origem = assinatura.renovacao.isoformat(), "microsoft"
        renovacao_dias = _dias_ate(renewal_date)
        billing_cycle = cost_info.get("billing_cycle", "monthly")
        notes = cost_info.get("notes", "")

        custo_mensal = round(consumed * cost_unit, 2)
        uso_pct = round(consumed / total * 100, 1) if total > 0 else 0.0
        over_provisioned = consumed > total
        # Regra de negócio: SKU acima do limite contratado (consumed > total) não entra no
        # cálculo de desperdício — não faz sentido "desperdício" quando já está estourado.
        # Checagem explícita aqui em vez de confiar no clamp available=max(0, total-consumed)
        # feito no coletor (collector/jobs/entra_id_collector.py), que pode mudar sem avisar
        # esta camada de agregação.
        desperdicio = 0.0 if over_provisioned else round(available * cost_unit, 2)

        is_free = categoria != "pago"

        if not is_free:
            total_consumed += consumed
            total_seats += total
            custo_mensal_total += custo_mensal
            if preco_origem == "estimado":
                custo_estimado_total += custo_mensal
            desperdicio_total += desperdicio

        licenses.append(
            {
                "sku_id": sku_id,
                "sku_name": sku_name,
                "friendly_name": friendly_name,
                "has_friendly_name": has_friendly_name,
                "consumed": consumed,
                "total": total,
                "available": available,
                "warning_units": warning_units,
                "uso_pct": uso_pct,
                "over_provisioned": over_provisioned,
                "is_free": is_free,
                "categoria": categoria,
                "teste_vence": renewal_date if categoria == "teste" else "",
                "assinaturas_suspensas": assinatura.suspensas if assinatura is not None else 0,
                "cost_per_unit_brl": cost_unit,
                "cost_informado_brl": cost_informado,
                "preco_origem": preco_origem,
                "renewal_origem": renewal_origem,
                "renovacao_dias": renovacao_dias,
                "custo_mensal_brl": custo_mensal,
                "desperdicio_brl": desperdicio,
                "renewal_date": renewal_date,
                "billing_cycle": billing_cycle,
                "notes": notes,
                "last_updated": str(row.get("_time", ""))[:19] or None,
            }
        )

    licenses.sort(key=lambda x: (-x["desperdicio_brl"], -x["consumed"]))

    non_free_licenses = [lic for lic in licenses if not lic["is_free"]]
    result: dict = {
        "has_data": len(licenses) > 0,
        "licenses": licenses,
        "summary": {
            "total_skus": len(non_free_licenses),
            "total_skus_free": len([lic for lic in licenses if lic["categoria"] == "gratuito"]),
            "total_skus_teste": len([lic for lic in licenses if lic["categoria"] == "teste"]),
            "custo_estimado_brl": round(custo_estimado_total, 2),
            "skus_sem_preco": len([lic for lic in licenses if lic["preco_origem"] == "sem_preco"]),
            "total_consumed": total_consumed,
            "total_seats": total_seats,
            "uso_pct": round(total_consumed / total_seats * 100, 1) if total_seats else 0.0,
            "custo_mensal_total_brl": round(custo_mensal_total, 2),
            "desperdicio_total_brl": round(desperdicio_total, 2),
            "skus_com_custo": len([lic for lic in licenses if lic["cost_per_unit_brl"] > 0]),
            "total_unassigned": sum(lic["available"] for lic in non_free_licenses),
            "skus_ociosas": len([lic for lic in non_free_licenses if lic["available"] > 0]),
        },
        "costs_file": str(_COSTS_FILE),
    }

    with _cache_lock:
        _cache_dados = result
        _cache_ts = time.monotonic()

    return result
