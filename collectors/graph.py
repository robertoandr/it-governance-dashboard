"""
Coletor Microsoft Graph - apenas endpoints que NÃO estão no Influx.
O coletor /opt/zabbix/m365/m365_status.py já alimenta MFA, Service Health,
Incidents e Users no InfluxDB. Aqui só buscamos o que falta:
  - Secure Score
  - Intune compliance %
  - Licenças ativas
"""

import json
import logging
import re
import warnings
from pathlib import Path

import msal
import requests

import config

# Deprecation: migrar para app.services.graph_service no Sprint 10F. Ver docs/MIGRATION.md.
warnings.warn(
    "collectors.graph is deprecated. Use app.services.graph_service (Sprint 10F). See docs/MIGRATION.md.",
    DeprecationWarning,
    stacklevel=2,
)

log = logging.getLogger(__name__)


_NAMES_PATH = Path(__file__).resolve().parent.parent / "data" / "license_names.json"
_SKU_NAMES_CACHE: dict | None = None


def _load_sku_names() -> dict:
    global _SKU_NAMES_CACHE
    if _SKU_NAMES_CACHE is not None:
        return _SKU_NAMES_CACHE
    try:
        with open(_NAMES_PATH) as f:
            _SKU_NAMES_CACHE = json.load(f)
    except Exception as e:
        log.warning("license_names.json não carregado: %s", e)
        _SKU_NAMES_CACHE = {}
    return _SKU_NAMES_CACHE


def _pretty_sku_name(raw: str) -> str:
    """Devolve nome amigável a partir do skuPartNumber. Usa mapa fixo + fallback."""
    if not raw:
        return "?"
    names = _load_sku_names()
    if raw in names:
        return names[raw]
    # Fallback algorítmico: troca _ por espaço, title case, mantém algumas siglas
    s = raw.replace("_", " ").strip()
    s = " ".join(w.capitalize() for w in s.split())
    # Corrige siglas comuns que viraram TitleCase
    for sigla in ["BI", "AI", "API", "VPN", "SSO", "MFA", "AD", "IT", "QA", "VOIP", "VTrial"]:
        s = re.sub(r"\b" + sigla.title() + r"\b", sigla, s)
    s = s.replace("Vtrial", "(Trial)")
    return s


_COSTS_PATH = Path(__file__).resolve().parent.parent / "data" / "license_costs.json"


def _load_sku_costs() -> dict:
    """Lê o JSON de custos a cada chamada (sem cache, pra refletir edições)."""
    try:
        with open(_COSTS_PATH) as f:
            data = json.load(f)
        return {k: v for k, v in data.items() if not k.startswith("_")}
    except Exception as e:
        log.warning("license_costs.json não carregado: %s", e)
        return {}


class GraphCollector:
    def __init__(self):
        self._token: str | None = None
        if not config.GRAPH_ENABLED:
            self._app = None
            return
        self._app = msal.ConfidentialClientApplication(
            config.GRAPH_CLIENT_ID,
            authority=f"https://login.microsoftonline.com/{config.GRAPH_TENANT_ID}",
            client_credential=config.GRAPH_CLIENT_SECRET,
        )

    def _get_token(self) -> str:
        if not self._app:
            raise RuntimeError("Graph não configurado (AZURE_* vazios)")
        if self._token:
            return self._token
        result = self._app.acquire_token_for_client(scopes=config.GRAPH_SCOPES)
        if "access_token" not in result:
            raise RuntimeError(f"Falha no token Graph: {result.get('error_description', result)}")
        self._token = result["access_token"]
        return self._token

    def _get(self, endpoint: str) -> dict:
        headers = {"Authorization": f"Bearer {self._get_token()}"}
        r = requests.get(
            f"https://graph.microsoft.com/v1.0{endpoint}",
            headers=headers,
            timeout=20,
        )
        if r.status_code == 401:
            # Token expirou
            self._token = None
            headers["Authorization"] = f"Bearer {self._get_token()}"
            r = requests.get(
                f"https://graph.microsoft.com/v1.0{endpoint}",
                headers=headers,
                timeout=20,
            )
        r.raise_for_status()
        return r.json()

    def get_secure_score(self) -> dict:
        if not config.GRAPH_ENABLED:
            return {"current": 0, "max": 100, "pct": 0, "enabled": False}
        try:
            data = self._get("/security/secureScores?$top=1")
            scores = data.get("value", [])
            if not scores:
                return {"current": 0, "max": 100, "pct": 0, "enabled": True}
            s = scores[0]
            current = float(s.get("currentScore", 0))
            maximum = float(s.get("maxScore", 100)) or 100
            return {
                "current": round(current, 1),
                "max": round(maximum, 1),
                "pct": round((current / maximum) * 100, 1),
                "enabled": True,
            }
        except Exception as e:
            log.warning("Secure Score falhou: %s", e)
            return {"current": 0, "max": 100, "pct": 0, "enabled": True, "error": str(e)}

    def get_intune_compliance(self) -> dict:
        if not config.GRAPH_ENABLED:
            return {"pct": 0, "compliant": 0, "total": 0, "enabled": False}
        try:
            data = self._get("/deviceManagement/managedDevices?$select=complianceState&$top=999")
            devices = data.get("value", [])
            if not devices:
                return {"pct": 0, "compliant": 0, "total": 0, "enabled": True}
            compliant = sum(1 for d in devices if d.get("complianceState") == "compliant")
            total = len(devices)
            return {
                "pct": round((compliant / total) * 100, 1),
                "compliant": compliant,
                "total": total,
                "enabled": True,
            }
        except Exception as e:
            log.warning("Intune falhou: %s", e)
            return {"pct": 0, "compliant": 0, "total": 0, "enabled": True, "error": str(e)}

    def get_licenses(self) -> list[dict]:
        if not config.GRAPH_ENABLED:
            return []
        try:
            costs = _load_sku_costs()
            data = self._get("/subscribedSkus")
            out = []
            for sku in data.get("value", []):
                enabled = sku.get("prepaidUnits", {}).get("enabled", 0)
                consumed = sku.get("consumedUnits", 0)
                if enabled == 0:
                    continue
                sku_id = sku.get("skuPartNumber", "?")
                meta = costs.get(sku_id, {})
                cost = float(meta.get("cost_brl", 0) or 0)
                # Compatibilidade: aceita is_free antigo OU category novo
                if "category" in meta:
                    category = meta["category"]
                elif meta.get("is_free"):
                    category = "free"
                else:
                    category = "paid"
                history = meta.get("history", []) or []
                # delta: compara cost atual com o mais recente do histórico (penúltimo se cost == último)
                prev_cost = None
                if history:
                    sorted_h = sorted(history, key=lambda h: h.get("date", ""))
                    # último histórico com valor diferente do atual
                    for h in reversed(sorted_h):
                        if abs(float(h.get("cost_brl", 0)) - cost) > 0.001:
                            prev_cost = float(h["cost_brl"])
                            break
                delta_abs = round(cost - prev_cost, 2) if prev_cost is not None else None
                delta_pct = round(((cost - prev_cost) / prev_cost) * 100, 1) if prev_cost else None
                free = max(0, enabled - consumed)
                # Só "paid" conta em $$
                pays = category == "paid"
                out.append(
                    {
                        "name": _pretty_sku_name(sku_id),
                        "sku": sku_id,
                        "enabled": enabled,
                        "consumed": consumed,
                        "free": free,
                        "pct_used": round((consumed / enabled) * 100, 1) if enabled else 0,
                        "cost_brl": cost,
                        "category": category,
                        "is_free": (category == "free"),
                        "history": history,
                        "previous_cost_brl": prev_cost,
                        "delta_abs": delta_abs,
                        "delta_pct": delta_pct,
                        "monthly_total_brl": round(cost * consumed, 2) if pays else 0,
                        "waste_brl": round(cost * free, 2) if pays else 0,
                    }
                )
            return sorted(out, key=lambda x: -x["enabled"])
        except Exception as e:
            log.warning("Licenças falharam: %s", e)
            return []
