"""
Serviço de Governança - lê metadados de domínios e owners.

Carrega config/owners.yaml com cache em memória (TTL 60s).
Calcula SLA atual consumindo o _cache global do app.
"""

import logging
import threading
import time
from pathlib import Path

import yaml

log = logging.getLogger("governance_service")

_CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
_OWNERS_FILE = _CONFIG_DIR / "owners.yaml"

_yaml_cache: dict = {"data": None, "loaded_at": 0.0, "mtime": 0.0}
_yaml_lock = threading.Lock()
_TTL = 60  # segundos


def _load_yaml(force: bool = False) -> dict:
    """Carrega owners.yaml com cache TTL + invalidação por mtime."""
    with _yaml_lock:
        now = time.time()
        try:
            current_mtime = _OWNERS_FILE.stat().st_mtime
        except FileNotFoundError:
            log.error("Arquivo não encontrado: %s", _OWNERS_FILE)
            return {"domains": {}, "organization": {}}

        cache_valid = (
            not force
            and _yaml_cache["data"] is not None
            and (now - _yaml_cache["loaded_at"]) < _TTL
            and _yaml_cache["mtime"] == current_mtime
        )

        if cache_valid:
            return _yaml_cache["data"]

        try:
            with _OWNERS_FILE.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            _yaml_cache.update(data=data, loaded_at=now, mtime=current_mtime)
            log.info("owners.yaml recarregado (mtime=%.0f)", current_mtime)
            return data
        except yaml.YAMLError as e:
            log.error("Erro ao parsear owners.yaml: %s", e)
            return _yaml_cache.get("data") or {"domains": {}, "organization": {}}


def get_organization() -> dict:
    """Retorna dados da organização."""
    return _load_yaml().get("organization", {})


def get_domains(include_planned: bool = True) -> list[dict]:
    """
    Retorna lista de domínios ordenada por 'order'.
    Cada domínio vem com 'key' (slug) injetado.
    """
    data = _load_yaml()
    domains_raw = data.get("domains", {}) or {}

    result = []
    for key, info in domains_raw.items():
        if not include_planned and info.get("status") == "planned":
            continue
        domain = dict(info)
        domain["key"] = key
        result.append(domain)

    result.sort(key=lambda d: d.get("order", 999))
    return result


def get_domain(key: str) -> dict | None:
    """Retorna um domínio específico ou None."""
    domains = _load_yaml().get("domains", {}) or {}
    info = domains.get(key)
    if not info:
        return None
    info = dict(info)
    info["key"] = key
    return info


def compute_sla_current(domain_key: str, cache: dict) -> float | None:
    """
    Calcula SLA atual de um domínio consumindo o _cache global do app.
    Retorna None se não houver dado suficiente.
    """
    if domain_key == "cftv":
        cftv = cache.get("cftv") or {}
        return cftv.get("up_pct")

    if domain_key == "m365":
        # SLA M365 = média ponderada de MFA + ServiceHealth OK
        mfa = (cache.get("mfa") or {}).get("pct")
        sh = cache.get("service_health") or []
        # se há service_health, todos OK = 100%, qualquer issue = 95%
        sh_pct = (
            100.0 if not sh else (100.0 if all(s.get("status") in ("serviceOperational", "ok") for s in sh) else 95.0)
        )
        if mfa is None:
            return sh_pct
        return round((float(mfa) * 0.4) + (sh_pct * 0.6), 1)

    return None


def get_domains_with_runtime(cache: dict) -> list[dict]:
    """
    Retorna domínios enriquecidos com dados de runtime (SLA atual, alerts).
    """
    domains = get_domains(include_planned=True)
    for d in domains:
        if d.get("status") == "active":
            current = compute_sla_current(d["key"], cache)
            if current is not None:
                d.setdefault("sla", {})["current"] = current
                target = d["sla"].get("target", 0)
                d["sla"]["status"] = "ok" if current >= target else "warn"
    return domains
