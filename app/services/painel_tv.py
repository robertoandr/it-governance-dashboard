"""Dados do painel de TV do NOC (/gov/v1 … /gov/v6).

Junta, num único dicionário serializável em JSON, o que as telas de TV mostram:
score e pilares, KPIs de operação, ativos e falhas por unidade, alertas
abertos, SLA, Secure Score, licenças, dispositivos, links e a disponibilidade
dos últimos 7 dias. Não cria fonte de dado nova: reaproveita os caches que as
páginas do dashboard já usam (Zabbix, Zendesk, Microsoft Graph, FortiGate,
InfluxDB).

Cada bloco é montado isoladamente: se uma fonte falha, o bloco vem ``None`` e
o nome vai para ``erros`` — a tela mostra "sem dados" naquele bloco e o resto
continua (o painel nunca fica em branco por causa de uma integração).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import requests
import structlog

from itgov.utils.cache_swr import CacheSWR

log = structlog.get_logger(__name__)

TZ_LOCAL = ZoneInfo("America/Sao_Paulo")
_DIAS = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]
_MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]

# Exceções esperadas de uma fonte fora do ar ou com resposta inesperada.
_ERROS_FONTE = (RuntimeError, ValueError, KeyError, TypeError, OSError, requests.RequestException)

_cache: CacheSWR[dict[str, Any]] = CacheSWR("painel_tv", ttl=60, valido=lambda v: bool(v.get("score")))


def faixa(valor: float | None) -> str:
    """Faixa de status de um percentual/score: ok > 85, warn 60–85, crit < 60."""
    if valor is None:
        return "none"
    if valor > 85:
        return "ok"
    if valor >= 60:
        return "warn"
    return "crit"


def faixa_unidade(falhas: int, total: int) -> str:
    """Status de uma unidade pela quantidade de ativos em falha (0 ok, 1–5 atenção, >5 crítico)."""
    if not total:
        return "none"
    if falhas > 5:
        return "crit"
    if falhas > 0:
        return "warn"
    return "ok"


def _bloco(erros: list[str], nome: str, montar: Callable[[], Any]) -> Any:
    try:
        return montar()
    except _ERROS_FONTE as exc:
        log.warning("painel_tv.bloco_falhou", bloco=nome, erro=str(exc))
        erros.append(nome)
        return None


def _governanca() -> dict[str, Any]:
    from app.services.metrics_aggregator import MetricsAggregator

    dados = asyncio.run(MetricsAggregator().calculate_full_score()).model_dump(mode="json")
    pilares = [
        {
            "nome": p["label"],
            "score": round(p["score"], 1) if p.get("data_source") != "coming_soon" else None,
            "faixa": faixa(p["score"]) if p.get("data_source") != "coming_soon" else "none",
        }
        for p in dados["pillars"]
    ]
    score = round(dados["global_score"], 1)
    return {"score": {"valor": score, "faixa": faixa(score), "tendencia": dados.get("trend")}, "pilares": pilares}


def _zabbix() -> dict[str, Any]:
    from itgov.api.v1.zabbix_monitoring import get_cached_zabbix_summary
    from itgov.api.v1.zabbix_triggers import get_cached_triggers

    resumo = get_cached_zabbix_summary() or {}
    trig = get_cached_triggers() or {}
    cont = trig.get("counts") or {}
    problemas = trig.get("problems") or []
    return {
        "disponibilidade": resumo.get("uptime_pct"),
        "hosts_total": resumo.get("total_monitorado"),
        "hosts_fora": resumo.get("hosts_down"),
        "problemas": {
            "total": trig.get("total", len(problemas)),
            "desastre": cont.get("Desastre", 0),
            "alto": cont.get("Alto", 0),
            "medio": cont.get("Médio", 0),
            "aviso": cont.get("Aviso", 0),
            "reconhecidos": sum(1 for p in problemas if p.get("acknowledged")),
        },
        "_problemas": problemas,
    }


def _unidades_e_hosts() -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Ativos e falhas por unidade raiz; também devolve nome do host → unidade."""
    from app.models.unidade import Unidade
    from itgov.api.v1.rede_monitoring import _zbx, unidade_do_ip

    unidades = Unidade.query.all()
    por_id = {u.id: u for u in unidades}

    def raiz(uid: int) -> Any:
        u = por_id[uid]
        while u.parent_id and u.parent_id in por_id:
            u = por_id[u.parent_id]
        return u

    faixas = [(u.id, f) for u in unidades if u.ativo for f in u.faixas]
    hosts = _zbx(
        "host.get",
        {"output": ["hostid", "name"], "selectInterfaces": ["ip"], "filter": {"status": "0"}},
    )
    com_falha = {
        h["hostid"]
        for t in _zbx(
            "trigger.get",
            {
                "output": ["triggerid"],
                "filter": {"value": 1},
                "only_true": True,
                "monitored": True,
                "min_severity": 2,
                "skipDependent": True,
                "selectHosts": ["hostid"],
            },
        )
        for h in t["hosts"]
    }
    contagem: dict[str, list[int]] = {raiz(u.id).nome: [0, 0] for u in unidades if u.ativo and not u.parent_id}
    host_unidade: dict[str, str] = {}
    for h in hosts:
        ip = (h.get("interfaces") or [{}])[0].get("ip", "")
        uid = unidade_do_ip(ip, faixas) if ip else None
        nome = raiz(uid).nome if uid else "Serviços"
        host_unidade[h["name"]] = nome
        c = contagem.setdefault(nome, [0, 0])
        c[0] += 1
        c[1] += 1 if h["hostid"] in com_falha else 0
    lista = [
        {"nome": nome, "ativos": t, "falhas": f, "faixa": faixa_unidade(f, t)} for nome, (t, f) in contagem.items()
    ]
    lista.sort(key=lambda u: (-u["falhas"], -u["ativos"], u["nome"]))
    return lista, host_unidade


def _alertas(problemas: list[dict[str, Any]], host_unidade: dict[str, str]) -> list[dict[str, Any]]:
    rotulo = {5: "DESASTRE", 4: "ALTO", 3: "MÉDIO", 2: "AVISO", 1: "INFO", 0: "N/C"}
    itens = [
        {
            "sev": int(p.get("severity") or 0),
            "sev_rotulo": rotulo.get(int(p.get("severity") or 0), "?"),
            "host": p.get("host", ""),
            "problema": _problema_curto(p.get("name", "")),
            "unidade": host_unidade.get(p.get("host", ""), "—"),
            "desde": p.get("since_iso", ""),
            "reconhecido": bool(p.get("acknowledged")),
        }
        for p in problemas
    ]
    itens.sort(key=lambda a: a["desde"], reverse=True)
    return itens


def _problema_curto(nome: str) -> str:
    """Nome do trigger em português curto para leitura de longe."""
    trocas = {
        "ICMP Ping: Unavailable by ICMP ping": "sem ping",
        "ICMP Ping: High ICMP ping response time": "ping lento",
        "No SNMP data collection": "sem coleta SNMP",
        "Linux: Zabbix agent is not available (for 3m)": "agente Zabbix fora",
    }
    return trocas.get(nome, nome)


def _sla() -> dict[str, Any]:
    from itgov.api.v1.zendesk import get_cached_sla_detail

    s = get_cached_sla_detail() or {}
    per = s.get("period") or {}
    if not per:
        raise ValueError("SLA do Zendesk sem dados")
    return {
        "cumprido": per.get("compliance_pct"),
        "primeira_resposta": per.get("first_reply_compliance_pct"),
        "resolucao": per.get("resolution_compliance_pct"),
        "abertos": s.get("total_open"),
        "vencidos": s.get("backlog_breached"),
        "dias": per.get("window_days", 30),
    }


def _secure_score() -> dict[str, Any]:
    from itgov.api.v1.governance_compliance import get_cached_compliance_summary

    sc = get_cached_compliance_summary() or {}
    if sc.get("pct") is None:
        raise ValueError("Secure Score sem dados")
    return {"pct": sc.get("pct"), "variacao_30d": sc.get("variacao_30d")}


def _licencas() -> dict[str, Any]:
    from itgov.api.v1.m365_licenses import get_licenses_summary

    d = get_licenses_summary() or {}
    r = d.get("summary") or {}
    if not r:
        raise ValueError("licenças sem dados")
    anomalias = []
    for lic in d.get("licenses") or []:
        if lic.get("is_free"):
            continue
        if lic.get("desperdicio_brl"):
            anomalias.append(
                {
                    "tipo": "ociosa",
                    "texto": f"{lic['friendly_name']}: {lic.get('available', 0)} sem uso",
                    "valor": lic["desperdicio_brl"],
                }
            )
        dias = lic.get("renovacao_dias")
        if isinstance(dias, int | float) and dias < 0:
            anomalias.append(
                {"tipo": "vencida", "texto": f"{lic['friendly_name']}: renovação vencida há {-int(dias)} dias"}
            )
    return {
        "uso_pct": r.get("uso_pct"),
        "usadas": r.get("total_consumed"),
        "total": r.get("total_seats"),
        "custo_mensal": r.get("custo_mensal_total_brl"),
        "desperdicio": r.get("desperdicio_total_brl"),
        "anomalias": anomalias,
    }


def _dispositivos() -> dict[str, Any]:
    from itgov.api.v1.governance_devices import get_cached_device_summary

    d = get_cached_device_summary() or {}
    if not d.get("total_devices"):
        raise ValueError("dispositivos sem dados")
    return {"total": d["total_devices"], "inativos": d.get("stale_45d")}


def _links() -> dict[str, Any]:
    from itgov.services.fortigate_api import get_cached_fortigates

    itens = []
    for fw in get_cached_fortigates() or []:
        for w in fw.get("wans", []):
            if not w.get("link"):
                itens.append(
                    {
                        "unidade": fw.get("unidade", ""),
                        "texto": f"{w.get('operadora', '')} ({w.get('iface', '')}) sem link",
                    }
                )
        for sla in fw.get("sdwan", []):
            for m in sla.get("members", []):
                if m.get("status") == "down":
                    itens.append(
                        {"unidade": fw.get("unidade", ""), "texto": f"{sla.get('sla', '')} {m.get('label', '')} fora"}
                    )
    return {"fora": itens}


def _seguranca() -> dict[str, Any]:
    """Tentativas de invasão e incidentes (Acronis) + alertas do Defender."""
    from itgov.api.v1.acronis_backup import get_cached_acronis_summary
    from itgov.api.v1.governance_security_alerts import get_cached_security_alerts_summary

    a = get_cached_acronis_summary() or {}
    if not a.get("total_agents"):
        raise ValueError("Acronis sem dados")
    try:
        defender = get_cached_security_alerts_summary() or {}
    except _ERROS_FONTE as exc:
        log.warning("painel_tv.defender_falhou", erro=str(exc))
        defender = {}
    incidentes = [
        {
            "maquina": i.get("resource_name", "—"),
            "tipo": i.get("alert_type", "—"),
            "severidade": i.get("severity", "—"),
            "mitigado": bool(i.get("mitigation")),
        }
        for i in (a.get("incidentes") or [])[:8]
    ]
    return {
        "invasoes": a.get("intrusion_attempts", 0),
        "invasoes_edr": a.get("intrusion_edr", 0),
        "invasoes_url": a.get("intrusion_url", 0),
        "invasoes_login": a.get("intrusion_login", 0),
        "incidentes": a.get("incidents_total", 0),
        "nao_mitigados": a.get("incidents_not_mitigated", 0),
        "patches_criticos": a.get("patches_critical", 0),
        "defender_abertos": defender.get("total_open") if defender.get("enabled") else None,
        "defender_altos": defender.get("high") if defender.get("enabled") else None,
        "defender_24h": defender.get("older_than_24h") if defender.get("enabled") else None,
        "ultimos_incidentes": incidentes,
    }


def _controlados() -> dict[str, Any]:
    """Endpoints sob controle (agente Acronis com plano de proteção)."""
    from itgov.api.v1.acronis_backup import get_cached_acronis_summary

    a = get_cached_acronis_summary() or {}
    if not a.get("total_agents"):
        raise ValueError("Acronis sem dados")
    return {
        "total": a["total_agents"],
        "online": a.get("online", 0),
        "offline": a.get("offline", 0),
        "protegidos": a.get("protected", 0),
        "protegidos_pct": a.get("protected_pct"),
        "sem_plano": a.get("sem_plano_count", 0),
        "offline_30d": a.get("offline_gt_30d_count", 0),
        "desatualizados": a.get("outdated", 0),
    }


def _cftv(host_unidade: dict[str, str]) -> dict[str, Any]:
    """Câmeras e gravadores do Zabbix: funcionando, fora e quais estão fora."""
    from itgov.api.v1.cftv_monitoring import get_cached_cftv_summary

    devs = (get_cached_cftv_summary() or {}).get("devices") or []
    if not devs:
        raise ValueError("CFTV sem dados")
    cameras = [d for d in devs if not d.get("is_gravador")]
    gravadores = [d for d in devs if d.get("is_gravador")]

    def conta(lista: list[dict[str, Any]], status: str) -> int:
        return sum(1 for d in lista if d.get("status") == status)

    ativas = [d for d in cameras if d.get("status") != "maint"]
    fora = sorted(
        (d for d in devs if d.get("status") == "down"),
        key=lambda d: (not d.get("is_gravador"), -(d.get("offline_desde") or 0)),
    )
    return {
        "cameras": len(cameras),
        "funcionando": conta(cameras, "up"),
        "fora": conta(cameras, "down"),
        "sem_dados": conta(cameras, "nodata"),
        "manutencao": conta(cameras, "maint"),
        "funcionando_pct": round(conta(cameras, "up") / len(ativas) * 100, 1) if ativas else None,
        "gravadores": len(gravadores),
        "gravadores_fora": conta(gravadores, "down"),
        "lista_fora": [
            {
                "nome": d.get("name", ""),
                "unidade": host_unidade.get(d.get("name", ""), "—"),
                "gravador": bool(d.get("is_gravador")),
                "desde": datetime.fromtimestamp(d["offline_desde"], TZ_LOCAL).isoformat(timespec="seconds")
                if d.get("offline_desde")
                else "",
            }
            for d in fora[:40]
        ],
    }


def _dispo_7d() -> list[dict[str, Any]]:
    from itgov.api.v1.rede_monitoring import _query_influx

    linhas = _query_influx(
        'from(bucket:"governance_raw") |> range(start:-7d) '
        '|> filter(fn:(r)=> r._measurement=="gov_zabbix_disponibilidade" and r._field=="uptime_pct") '
        '|> aggregateWindow(every:1d, fn:mean, createEmpty:false, timeSrc:"_start", location:{zone:"America/Sao_Paulo", offset:0s})'
    )
    serie = []
    for linha in linhas:
        t = linha.get("_time")
        if isinstance(t, datetime) and linha.get("_value") is not None:
            serie.append({"dia": t.astimezone(TZ_LOCAL).strftime("%d/%m"), "valor": round(float(linha["_value"]), 2)})
    return serie[-7:]


def _fontes() -> list[dict[str, str]]:
    from app.services import fontes_status

    return [
        {"nome": f.nome, "estado": f.estado} for f in fontes_status.status_fontes() if f.estado != "nao_configurada"
    ]


def montar_painel() -> dict[str, Any]:
    """Monta todos os blocos do painel (sem cache)."""
    erros: list[str] = []
    agora = datetime.now(TZ_LOCAL)
    gov = _bloco(erros, "governança", _governanca) or {}
    zbx = _bloco(erros, "zabbix", _zabbix) or {}
    un = _bloco(erros, "unidades", _unidades_e_hosts)
    unidades, host_unidade = un if un else ([], {})
    problemas = zbx.pop("_problemas", [])
    return {
        "gerado_em": agora.isoformat(timespec="seconds"),
        "hora": agora.strftime("%H:%M"),
        "data": f"{_DIAS[agora.weekday()]}, {agora.day:02d} {_MESES[agora.month - 1]}",
        "score": gov.get("score"),
        "pilares": gov.get("pilares", []),
        "zabbix": zbx or None,
        "unidades": unidades,
        "alertas": _alertas(problemas, host_unidade),
        "sla": _bloco(erros, "zendesk", _sla),
        "secure_score": _bloco(erros, "secure score", _secure_score),
        "licencas": _bloco(erros, "licenças", _licencas),
        "dispositivos": _bloco(erros, "dispositivos", _dispositivos),
        "links": _bloco(erros, "fortigate", _links),
        "seguranca": _bloco(erros, "segurança", _seguranca),
        "controlados": _bloco(erros, "acronis", _controlados),
        "cftv": _bloco(erros, "cftv", lambda: _cftv(host_unidade)),
        "dispo_7d": _bloco(erros, "histórico", _dispo_7d) or [],
        "fontes": _bloco(erros, "fontes", _fontes) or [],
        "erros": erros,
    }


def get_painel() -> dict[str, Any]:
    """Painel com cache de 60 s (atualiza em segundo plano, nunca bloqueia a TV)."""
    from flask import current_app

    app = current_app._get_current_object()  # type: ignore[attr-defined]

    def carregar() -> dict[str, Any]:
        # A atualização roda numa thread do CacheSWR, fora do request: precisa
        # do contexto da app para consultar as unidades no banco.
        with app.app_context():
            return montar_painel()

    return _cache.get(carregar)
