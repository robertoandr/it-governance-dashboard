"""Módulo Cyber — investigação de incidentes e visão NOC de segurança.

Reúne, **somente leitura**, três fontes que o dashboard já coleta:

- Incidentes e KPIs de segurança do Acronis Cyber Protect
  (``itgov.api.v1.acronis_backup.get_cached_acronis_summary``, lido do InfluxDB);
- Problemas ativos do Zabbix (``itgov.api.v1.zabbix_triggers.get_cached_triggers``);
- Estado dos FortiGate (``itgov.services.fortigate_api.get_cached_fortigates``).

O "agente Cyber" (conta de serviço Acronis com papéis *master* + *Cyber*) não
executa nenhuma ação de escrita aqui: ações de resposta (isolar endpoint, mudar
plano, disparar campanha) devem passar pela fila de aprovação. Este módulo só
observa e correlaciona.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import structlog

log = structlog.get_logger(__name__)

# Peso para ordenar/priorizar incidentes por severidade (maior = mais grave).
# Cobre os rótulos usuais da API Acronis; valores desconhecidos caem em 0.
PESO_SEVERIDADE: dict[str, int] = {
    "critical": 5,
    "crítico": 5,
    "critico": 5,
    "high": 4,
    "alto": 4,
    "error": 4,
    "warning": 3,
    "medium": 3,
    "médio": 3,
    "medio": 3,
    "low": 2,
    "baixo": 2,
    "info": 1,
    "informational": 1,
}

_CACHE_TTL = 300

_lock = threading.Lock()
_cache_data: dict[str, Any] | None = None
_cache_ts: float = 0.0


def peso_severidade(severidade: str | None) -> int:
    """Retorna o peso numérico de uma severidade para ordenação.

    Args:
        severidade: Rótulo de severidade vindo do Acronis (ex.: ``critical``).

    Returns:
        Peso de 1 (info) a 5 (crítico); 0 quando o rótulo é desconhecido.
    """
    if not severidade:
        return 0
    return PESO_SEVERIDADE.get(str(severidade).strip().lower(), 0)


def filtrar_incidentes(
    incidentes: list[dict[str, Any]],
    *,
    severidade: str | None = None,
    tipo: str | None = None,
    busca: str | None = None,
) -> list[dict[str, Any]]:
    """Filtra a lista de incidentes para a investigação.

    Os filtros são combinados por ``E`` (todos precisam casar). Comparações de
    texto são case-insensitive; ``severidade`` casa exatamente o rótulo, ``tipo``
    e ``busca`` casam por substring.

    Args:
        incidentes: Incidentes no formato de ``get_cached_acronis_summary``.
        severidade: Rótulo exato de severidade, ou ``None`` para não filtrar.
        tipo: Substring do ``alert_type``, ou ``None``.
        busca: Substring livre buscada em ``resource_name`` e ``alert_type``.

    Returns:
        Nova lista com os incidentes que passam em todos os filtros.
    """
    sev = (severidade or "").strip().lower()
    tp = (tipo or "").strip().lower()
    q = (busca or "").strip().lower()

    saida: list[dict[str, Any]] = []
    for inc in incidentes:
        if sev and str(inc.get("severity", "")).strip().lower() != sev:
            continue
        if tp and tp not in str(inc.get("alert_type", "")).lower():
            continue
        if q:
            alvo = f"{inc.get('resource_name', '')} {inc.get('alert_type', '')}".lower()
            if q not in alvo:
                continue
        saida.append(inc)
    return saida


def montar_noc(
    acronis: dict[str, Any] | None,
    zabbix: dict[str, Any] | None,
    forti: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Correlaciona as três fontes numa visão única de NOC de segurança.

    Args:
        acronis: Resumo de ``get_cached_acronis_summary`` (ou ``None``).
        zabbix: Retorno de ``get_cached_triggers`` (ou ``None``).
        forti: Lista de ``get_cached_fortigates`` (ou ``None``).

    Returns:
        Dicionário com blocos ``acronis``, ``zabbix`` e ``forti`` já resumidos e
        uma ``criticidade`` geral (``critico`` | ``atencao`` | ``ok``).
    """
    ac = acronis or {}
    zbx = zabbix or {}
    fts = forti or []

    nao_mitigados = int(ac.get("incidents_not_mitigated", 0) or 0)
    intrusoes = int(ac.get("intrusion_attempts", 0) or 0)
    patches_crit = int(ac.get("patches_critical", 0) or 0)

    zbx_on = bool(zbx.get("enabled"))
    zbx_total = int(zbx.get("total", 0) or 0) if zbx_on else 0

    fw_total = len(fts)
    fw_up = sum(1 for f in fts if f.get("api_up"))
    wan_down = 0
    for f in fts:
        for wan in f.get("wans", []) or []:
            if wan.get("status") == "down":
                wan_down += 1

    fw_fora = fw_total - fw_up

    if nao_mitigados > 0 or patches_crit > 0 or fw_fora > 0 or wan_down > 0:
        criticidade = "critico"
    elif intrusoes > 0 or zbx_total > 0:
        criticidade = "atencao"
    else:
        criticidade = "ok"

    return {
        "criticidade": criticidade,
        "acronis": {
            "incidents_not_mitigated": nao_mitigados,
            "incidents_mitigated": int(ac.get("incidents_mitigated", 0) or 0),
            "intrusion_attempts": intrusoes,
            "patches_critical": patches_crit,
            "patches_warning": int(ac.get("patches_warning", 0) or 0),
        },
        "zabbix": {
            "enabled": zbx_on,
            "total": zbx_total,
            "counts": zbx.get("counts", {}) if zbx_on else {},
            "updated_at": zbx.get("updated_at"),
        },
        "forti": {
            "total": fw_total,
            "online": fw_up,
            "fora": fw_fora,
            "wan_down": wan_down,
        },
    }


def _cache_valido() -> bool:
    return _cache_data is not None and (time.monotonic() - _cache_ts) < _CACHE_TTL


def _coletar_acronis() -> dict[str, Any]:
    """Resumo Acronis, degradando para ``{}`` se a fonte falhar."""
    try:
        from itgov.api.v1.acronis_backup import get_cached_acronis_summary

        return get_cached_acronis_summary()
    except (ImportError, RuntimeError, ValueError, OSError) as exc:
        log.warning("cyber.acronis_indisponivel", error=str(exc))
        return {}


def _coletar_zabbix() -> dict[str, Any]:
    """Problemas ativos do Zabbix, degradando para desabilitado se falhar."""
    try:
        from itgov.api.v1.zabbix_triggers import get_cached_triggers

        return get_cached_triggers()
    except (ImportError, RuntimeError, ValueError, OSError) as exc:
        log.warning("cyber.zabbix_indisponivel", error=str(exc))
        return {"enabled": False, "problems": [], "counts": {}}


def _coletar_forti() -> list[dict[str, Any]]:
    """Estado dos FortiGate, degradando para lista vazia se falhar."""
    try:
        from itgov.services.fortigate_api import get_cached_fortigates

        return get_cached_fortigates()
    except (ImportError, RuntimeError, ValueError, OSError) as exc:
        log.warning("cyber.forti_indisponivel", error=str(exc))
        return []


def montar_dashboard() -> dict[str, Any]:
    """Monta o payload completo do módulo Cyber a partir das três fontes.

    Returns:
        Dicionário com ``incidentes`` (lista para investigação), ``kpis``
        (KPIs de segurança do Acronis) e ``noc`` (visão correlacionada).
    """
    acronis = _coletar_acronis()
    zabbix = _coletar_zabbix()
    forti = _coletar_forti()

    incidentes = list(acronis.get("incidentes", []))
    return {
        "incidentes": incidentes,
        "kpis": {
            "total_agents": acronis.get("total_agents", 0),
            "incidents_total": acronis.get("incidents_total", 0),
            "incidents_not_mitigated": acronis.get("incidents_not_mitigated", 0),
            "intrusion_attempts": acronis.get("intrusion_attempts", 0),
            "edr_total": acronis.get("edr_total", 0),
        },
        "noc": montar_noc(acronis, zabbix, forti),
    }


def get_cached_cyber() -> dict[str, Any]:
    """Retorna o payload do módulo Cyber com cache TTL de 5 min."""
    global _cache_data, _cache_ts
    with _lock:
        if _cache_valido():
            log.debug("cyber.cache.hit")
            return _cache_data  # type: ignore[return-value]

    dados = montar_dashboard()

    with _lock:
        _cache_data = dados
        _cache_ts = time.monotonic()
    return dados


# ── Namespace Flask-RESTX (JSON para auto-refresh do painel NOC) ────────────
from flask_restx import Namespace, Resource  # noqa: E402

from app.auth.rbac import require_role  # noqa: E402

ns = Namespace("cyber", description="Módulo Cyber — incidentes e NOC de segurança")


@ns.route("/noc")
class CyberNocResource(Resource):
    """Visão NOC de segurança correlacionada (somente leitura)."""

    @ns.doc("get_cyber_noc")
    @require_role("admin", "gestor", pagina="cyber")
    def get(self) -> dict[str, Any]:
        """Retorna o bloco NOC correlacionado (Acronis + Zabbix + FortiGate)."""
        return get_cached_cyber()["noc"]


@ns.route("/incidentes/live")
class CyberIncidentesLiveResource(Resource):
    """Investigação ao vivo: incidentes consultados direto na API Acronis."""

    @ns.doc("get_cyber_incidentes_live")
    @require_role("admin", "gestor", pagina="cyber")
    def get(self) -> dict[str, Any]:
        """Lista incidentes ativos ao vivo.

        Returns:
            ``{"configurado": False}`` quando a conta de serviço Acronis
            (master + Cyber) ainda não está configurada; caso contrário, os
            incidentes agregados do mais grave ao menos grave.
        """
        from itgov.services import acronis_cyber

        if not acronis_cyber.configurado():
            return {"configurado": False, "incidentes": []}
        incidentes = acronis_cyber.listar_incidentes()
        return {
            "configurado": True,
            "total": len(incidentes),
            "incidentes": [i.model_dump() for i in incidentes],
        }


@ns.route("/incidentes/live/<string:incident_id>")
class CyberIncidenteLiveResource(Resource):
    """Detalhe e linha do tempo de um incidente, ao vivo."""

    @ns.doc("get_cyber_incidente_live")
    @require_role("admin", "gestor", pagina="cyber")
    def get(self, incident_id: str) -> dict[str, Any]:
        """Retorna um incidente específico com sua linha do tempo.

        Args:
            incident_id: Identificador do incidente.
        """
        from itgov.services import acronis_cyber

        if not acronis_cyber.configurado():
            return {"configurado": False, "incidente": None}
        inc = acronis_cyber.detalhe_incidente(incident_id)
        if inc is None:
            return {"configurado": True, "incidente": None}
        return {"configurado": True, "incidente": inc.model_dump()}
