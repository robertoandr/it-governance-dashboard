"""Dados CFTV (câmeras e NVRs) via Zabbix API para o dashboard.

Consulta os host groups que começam com "CFTV" no Zabbix usando Bearer token
(sem user.login / sem ZABBIX_PASSWORD). Cache TTL 2min (dados real-time).

A busca devolve só a lista de dispositivos com status; ``montar_visao`` agrupa
por gravador (DVR/NVR), aplica o vínculo gravador → unidade e o filtro de
unidade, e calcula os KPIs do recorte.
"""

from __future__ import annotations

import os
import threading
import time
from collections import Counter
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import requests
import structlog

from itgov.utils.cache_swr import CacheSWR

log = structlog.get_logger(__name__)

_CACHE_TTL = 120  # 2 minutos — dados operacionais de câmeras mudam rápido

_lock = threading.Lock()
_cache_data: dict | None = None
_cache_ts: float = 0.0


def _cache_valido() -> bool:
    return _cache_data is not None and (time.monotonic() - _cache_ts) < _CACHE_TTL


def _zbx(method: str, params: dict[str, Any]) -> Any:
    url = os.getenv("ZABBIX_URL", "")
    if not url:
        raise RuntimeError("ZABBIX_URL não configurado")
    if not url.endswith("/api_jsonrpc.php"):
        url = url.rstrip("/") + "/api_jsonrpc.php"

    token = os.getenv("ZABBIX_TOKEN", "")
    resp = requests.post(
        url,
        json={"jsonrpc": "2.0", "method": method, "params": params, "id": 1},
        headers={
            "Content-Type": "application/json-rpc",
            "Authorization": f"Bearer {token}",
        },
        timeout=15,
        verify=False,  # nosec B501 -- Zabbix interno sem cert válido # noqa: S501
    )
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"Zabbix API: {data['error']}")
    return data["result"]


def _buscar_cftv() -> dict:
    # ── 1. Grupos CFTV ───────────────────────────────────────────────────────
    groups = _zbx(
        "hostgroup.get",
        {"output": ["groupid", "name"], "search": {"name": "CFTV"}},
    )
    gids = [g["groupid"] for g in groups if g["name"].startswith("CFTV")]

    if not gids:
        return {"enabled": False, "devices": []}

    # ── 2. Hosts com tags e interfaces (sem itens inline — evita truncamento) ──
    hosts = _zbx(
        "host.get",
        {
            "output": ["hostid", "host", "name", "maintenance_status"],
            "groupids": gids,
            "selectInterfaces": ["type", "main", "ip", "port", "available", "error", "details"],
            "selectTags": ["tag", "value"],
            # Só para o SNMP dos gravadores (cf7); o Zabbix não devolve valor de macro secreta
            "selectMacros": ["macro", "value", "type"],
        },
    )

    host_ids = [h["hostid"] for h in hosts]

    # ── 2b. Busca separada do ICMP ping (filter exato, sem truncamento) ───────
    ping_items = _zbx(
        "item.get",
        {
            "output": ["hostid", "key_", "lastvalue", "lastclock"],
            "hostids": host_ids,
            "filter": {"key_": "icmpping"},
        },
    )
    ping_map: dict[str, dict] = {i["hostid"]: i for i in ping_items}

    # ── 3. Problemas ativos por host ──────────────────────────────────────────
    problems_raw = (
        _zbx(
            "problem.get",
            {
                "output": ["eventid", "name", "severity", "clock", "objectid"],
                "hostids": host_ids,
                "recent": False,
                "suppressed": False,
            },
        )
        if host_ids
        else []
    )

    # Agrupar severidade máxima por host (via trigger → host)
    # Para simplificar, conta apenas total de problemas por host via tags
    problems_by_host: dict[str, int] = {}
    # Início do problema "sem ping" em aberto, por host (para "offline há")
    offline_desde: dict[str, int] = {}
    if problems_raw:
        trigger_ids = list({p["objectid"] for p in problems_raw})
        triggers = _zbx(
            "trigger.get",
            {
                "output": ["triggerid"],
                "selectHosts": ["hostid"],
                "triggerids": trigger_ids,
            },
        )
        hosts_do_trigger: dict[str, list[str]] = {}
        for t in triggers or []:
            hosts_do_trigger[t["triggerid"]] = [h["hostid"] for h in t.get("hosts", [])]
            for hid in hosts_do_trigger[t["triggerid"]]:
                problems_by_host[hid] = problems_by_host.get(hid, 0) + 1
        for p in problems_raw:
            if TRIGGER_SEM_PING not in p.get("name", ""):
                continue
            for hid in hosts_do_trigger.get(p["objectid"], []):
                inicio = int(p["clock"])
                offline_desde[hid] = min(inicio, offline_desde.get(hid, inicio))

    # ── 4. Um registro por dispositivo, com status de ping ────────────────────
    from itgov.services.cftv_snmp import status_snmp

    devices: list[dict] = []
    for h in hosts:
        tags = {tg["tag"]: tg["value"] for tg in h.get("tags", [])}
        subcat = tags.get("subcategory", "outros")
        hid = h.get("hostid", "")

        if h.get("maintenance_status") == "1":
            status = "maint"
        else:
            ping = ping_map.get(hid)
            if not ping or not ping.get("lastclock") or ping["lastclock"] == "0":
                status = "nodata"
            elif ping["lastvalue"] == "1":
                status = "up"
            else:
                status = "down"

        devices.append(
            {
                "host": h["host"],
                "name": h["name"],
                "ip": _ip_principal(h.get("interfaces") or []),
                "subcat": subcat,
                "andar": tags.get("andar", "?"),
                "gravador": gravador_do_dispositivo(h["host"], subcat, tags),
                "is_gravador": subcat in SUBCATS_GRAVADOR,
                "snmp": status_snmp(h.get("interfaces") or [], h.get("macros") or [])
                if subcat in SUBCATS_GRAVADOR
                else None,
                "canal": tags.get("canal") or tags.get("canal_nvr") or "",
                "loja": tags.get("loja", ""),
                "vendor": tags.get("vendor", ""),
                "model": tags.get("model", ""),
                "status": status,
                "problems": problems_by_host.get(hid, 0),
                "offline_desde": offline_desde.get(hid),
                # Zabbix pinga a cada 1 min, inclusive os offline: é o "ping persistente"
                "ultimo_ping": int(ping_map[hid]["lastclock"])
                if hid in ping_map and ping_map[hid].get("lastclock")
                else None,
            }
        )

    return {"enabled": True, "devices": devices}


SUBCATS_GRAVADOR = frozenset({"dvr", "nvr"})
# Nome do trigger do template "ICMP Ping" que marca o dispositivo como offline
TRIGGER_SEM_PING = "Unavailable by ICMP ping"
DIAS_HISTORICO = 30
_TZ = ZoneInfo("America/Sao_Paulo")
SEM_UNIDADE = "sem"
STAND_ALONE = "Stand Alone"


def _ip_principal(interfaces: list[dict]) -> str:
    """IP da interface principal (prefere a de agente, a mais antiga nos hosts do CFTV)."""
    principais = sorted((i for i in interfaces if i.get("main", "1") == "1"), key=lambda i: i.get("type", "1"))
    return (principais or interfaces or [{}])[0].get("ip", "?")


def gravador_do_dispositivo(host: str, subcat: str, tags: dict[str, str]) -> str:
    """Nome do gravador (DVR/NVR) ao qual o dispositivo pertence.

    O próprio gravador usa o nome do host (ex.: ``DVR-1``), que é o mesmo valor
    da tag ``dvr`` das suas câmeras; o criado pelo dashboard (cf7) leva a tag
    ``dvr`` porque o nome técnico não aceita acentos. Câmeras Hikvision apontam para o NVR via
    ``parent_nvr``. Dispositivos sem gravador (faciais, antenas) retornam "".

    Args:
        host: Nome técnico do host no Zabbix.
        subcat: Tag ``subcategory`` do host.
        tags: Todas as tags do host.

    Returns:
        Chave do gravador, ou string vazia.
    """
    if subcat in SUBCATS_GRAVADOR:
        return tags.get("dvr") or host
    return tags.get("dvr") or tags.get("parent_nvr") or ""


def _contagem(devs: list[dict]) -> dict[str, Any]:
    total = len(devs)
    c = {st: sum(1 for d in devs if d["status"] == st) for st in ("up", "down", "nodata", "maint")}
    return {"total": total, **c, "up_pct": round(c["up"] / total * 100, 1) if total else 0.0}


def _canal_key(d: dict) -> tuple:
    canal = d.get("canal", "")
    return (0, int(canal), d["name"]) if canal.isdigit() else (1, 0, d["name"])


def montar_visao(
    dados: dict,
    unidade_por_gravador: dict[str, int | None],
    unidades: dict[int, str],
    unidade_por_loja: dict[str, int],
    filtro: set[int] | str | None = None,
    apelidos: dict[str, str] | None = None,
    duplicado_de: dict[str, str] | None = None,
    sede_por_unidade: dict[int, int] | None = None,
    quedas: dict[str, dict] | None = None,
) -> dict:
    """Agrupa dispositivos em cards por gravador e aplica o filtro de unidade.

    A unidade de um dispositivo vem do vínculo do seu gravador; sem vínculo,
    cai para a tag ``loja`` quando ela bate com o nome de uma unidade.
    Gravadores marcados como duplicados entram no card do gravador principal.

    Args:
        dados: Resultado de ``get_cached_cftv_summary``.
        unidade_por_gravador: Gravador → id da unidade (ou None).
        unidades: Id → nome completo da unidade, para exibição.
        unidade_por_loja: Nome de unidade (minúsculo) → id, para a tag ``loja``.
        filtro: Ids de unidade aceitos, ``SEM_UNIDADE`` para os sem unidade,
            ou None para todos.
        apelidos: Gravador → nome dado pelo admin (substitui o do Zabbix).
        duplicado_de: Gravador duplicado → gravador principal.
        sede_por_unidade: Id de unidade → id da sede (unidade raiz), para
            os totais por sede.
        quedas: Resultado de ``historico_quedas`` (host → quedas no período),
            para a coluna de quedas e o ranking do recorte.

    Returns:
        Dicionário com KPIs do recorte, ``cards`` por gravador, ``sedes`` com
        os totais de cada sede e listas de offline/manutenção.
    """
    apelidos = apelidos or {}
    principal = {g: resolver_principal(g, duplicado_de or {}) for g in (duplicado_de or {})}
    quedas = quedas or {}
    devices = [
        {
            **d,
            "gravador": principal.get(d["gravador"], d["gravador"]),
            "gravador_proprio": d["gravador"],
            "historico": quedas.get(d["host"]),
        }
        for d in dados.get("devices", [])
    ]
    unidos: dict[str, list[str]] = {}
    for dup, alvo in sorted(principal.items()):
        if dup != alvo:
            unidos.setdefault(alvo, []).append(dup)

    def _uid_loja(d: dict) -> int | None:
        return unidade_por_loja.get(d["loja"].strip().lower())

    # Unidade decidida uma vez por gravador, para não partir o card quando as
    # câmeras de um gravador sem vínculo têm tags ``loja`` diferentes.
    votos: dict[str, Counter[int]] = {}
    for d in devices:
        if d["gravador"] and (uid_loja := _uid_loja(d)) is not None:
            votos.setdefault(d["gravador"], Counter())[uid_loja] += 1
    uid_gravador: dict[str, int | None] = {}
    for g in {d["gravador"] for d in devices if d["gravador"]}:
        uid_gravador[g] = unidade_por_gravador.get(g)
        if uid_gravador[g] is None and g in votos:
            uid_gravador[g] = votos[g].most_common(1)[0][0]

    grupos: dict[tuple[str, int | None], list[dict]] = {}
    for d in devices:
        uid = uid_gravador[d["gravador"]] if d["gravador"] else _uid_loja(d)
        if filtro == SEM_UNIDADE and uid is not None:
            continue
        if isinstance(filtro, set) and uid not in filtro:
            continue
        grupos.setdefault((d["gravador"], uid), []).append(d)

    cards: list[dict] = []
    for (gravador, uid), devs in grupos.items():
        # O host do próprio gravador vai no cabeçalho; o de um duplicado unido
        # aparece como mais um dispositivo do card.
        # (pelo nome do host, ou pela tag ``dvr`` quando o nome técnico é outro — ex.: ``dvr-adm_hauer``)
        host_gravador = next((d for d in devs if d["is_gravador"] and d["host"] == gravador), None) or next(
            (d for d in devs if d["is_gravador"] and d["gravador_proprio"] == gravador), None
        )
        itens = sorted((d for d in devs if d is not host_gravador), key=_canal_key)
        vendors = sorted({d["vendor"] for d in devs if d["vendor"]})
        nome_zabbix = host_gravador["name"] if host_gravador else (gravador or STAND_ALONE)
        cards.append(
            {
                "gravador": gravador,
                # Câmera sem gravador grava sozinha (cartão SD/nuvem) — "Stand Alone" (pedido do usuário)
                "titulo": apelidos.get(gravador) or nome_zabbix,
                "nome_zabbix": nome_zabbix,
                "apelido": apelidos.get(gravador, ""),
                "unidos": unidos.get(gravador, []),
                "gravador_host": host_gravador,
                "unidade_id": uid,
                "unidade": unidades.get(uid, "") if uid is not None else "",
                "vendors": vendors,
                "dispositivos": itens,
                # Inclui o próprio gravador: bate com os KPIs e destaca gravador offline
                **_contagem(devs),
            }
        )
    # Problemas primeiro, depois por unidade e nome
    cards.sort(key=lambda c: (-c["down"], c["unidade"] or "~", c["titulo"]))

    todos = [d for devs in grupos.values() for d in devs]
    resumo = _contagem(todos)
    return {
        **resumo,
        "cards": cards,
        "sedes": _totais_por_sede(grupos, unidades, sede_por_unidade or {}),
        # Quem mais caiu no período, só entre os dispositivos do recorte
        "ranking_quedas": sorted(
            (d for d in todos if d["historico"]),
            key=lambda d: (-d["historico"]["quedas"], -d["historico"]["segundos"], d["name"]),
        ),
        "down_list": sorted((d for d in todos if d["status"] == "down"), key=lambda d: (d["gravador"], d["name"])),
        "maint_list": sorted((d for d in todos if d["status"] == "maint"), key=lambda d: d["name"]),
    }


def resolver_principal(gravador: str, duplicado_de: dict[str, str]) -> str:
    """Segue a cadeia "duplicado de" até o gravador principal.

    Um ciclo (A→B→A) não deveria existir — a rota de gravação recusa —, mas
    se aparecer a cadeia para no último gravador antes de repetir.

    Args:
        gravador: Nome do gravador.
        duplicado_de: Gravador duplicado → gravador principal.

    Returns:
        Nome do gravador principal (o próprio, se não for duplicado).
    """
    vistos = {gravador}
    atual = gravador
    while (proximo := duplicado_de.get(atual)) and proximo not in vistos:
        vistos.add(proximo)
        atual = proximo
    return atual


def _totais_por_sede(
    grupos: dict[tuple[str, int | None], list[dict]],
    unidades: dict[int, str],
    sede_por_unidade: dict[int, int],
) -> list[dict]:
    """Totais por sede (unidade raiz) para o primeiro nível do drill-down."""
    por_sede: dict[int | None, list[dict]] = {}
    gravadores: dict[int | None, set[str]] = {}
    for (gravador, uid), devs in grupos.items():
        sede = sede_por_unidade.get(uid, uid) if uid is not None else None
        por_sede.setdefault(sede, []).extend(devs)
        gravadores.setdefault(sede, set()).add(gravador)
    sedes = [
        {
            "id": sede,
            "nome": unidades.get(sede, "") if sede is not None else "Sem unidade",
            "gravadores": len(gravadores[sede]),
            **_contagem(devs),
        }
        for sede, devs in por_sede.items()
    ]
    # Sedes com câmera offline primeiro; "Sem unidade" por último
    sedes.sort(key=lambda s: (s["id"] is None, -s["down"], s["nome"]))
    return sedes


def formatar_duracao(segundos: float) -> str:
    """Duração curta em português, ex.: ``"3 d 4 h"``, ``"2 h 15 min"``, ``"40 min"``.

    Args:
        segundos: Duração em segundos.

    Returns:
        Texto com no máximo duas unidades; abaixo de 1 min, ``"< 1 min"``.
    """
    minutos = int(segundos // 60)
    if minutos < 1:
        return "< 1 min"
    dias, resto = divmod(minutos, 1440)
    horas, mins = divmod(resto, 60)
    if dias:
        return f"{dias} d {horas} h" if horas else f"{dias} d"
    if horas:
        return f"{horas} h {mins} min" if mins else f"{horas} h"
    return f"{mins} min"


def _quando(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=_TZ).strftime("%d/%m %H:%M")


def historico_quedas(eventos: list[dict], fim_por_evento: dict[str, int], agora: float) -> dict[str, dict]:
    """Agrega as quedas de ping por host.

    Args:
        eventos: Eventos de problema do Zabbix (``event.get`` com ``selectHosts``),
            com ``eventid``, ``clock``, ``r_eventid`` e ``hosts``.
        fim_por_evento: Id do evento de recuperação → horário da recuperação.
        agora: Horário atual (epoch), usado como fim das quedas em aberto.

    Returns:
        Host técnico → ``quedas`` (quantas vezes), ``segundos`` (tempo total
        offline), ``tempo`` (formatado), ``ultima`` (início da última queda,
        formatado) e ``em_aberto``.
    """
    por_host: dict[str, dict] = {}
    for e in eventos:
        inicio = int(e["clock"])
        aberto = e.get("r_eventid", "0") in ("0", "")
        fim = agora if aberto else fim_por_evento.get(e["r_eventid"], agora)
        for h in e.get("hosts", []):
            reg = por_host.setdefault(h["host"], {"quedas": 0, "segundos": 0.0, "ultima_ts": 0, "em_aberto": False})
            reg["quedas"] += 1
            reg["segundos"] += max(0.0, fim - inicio)
            reg["ultima_ts"] = max(reg["ultima_ts"], inicio)
            reg["em_aberto"] = reg["em_aberto"] or aberto
    for reg in por_host.values():
        reg["tempo"] = formatar_duracao(reg["segundos"])
        reg["ultima"] = _quando(reg["ultima_ts"])
    return por_host


def _buscar_quedas() -> dict[str, dict]:
    """Quedas de ping dos hosts CFTV nos últimos ``DIAS_HISTORICO`` dias."""
    groups = _zbx("hostgroup.get", {"output": ["groupid", "name"], "search": {"name": "CFTV"}})
    gids = [g["groupid"] for g in groups if g["name"].startswith("CFTV")]
    if not gids:
        return {}
    agora = time.time()
    eventos = _zbx(
        "event.get",
        {
            "output": ["eventid", "clock", "r_eventid"],
            "groupids": gids,
            "source": 0,
            "object": 0,
            "value": 1,
            "time_from": int(agora) - DIAS_HISTORICO * 86400,
            "search": {"name": TRIGGER_SEM_PING},
            "selectHosts": ["host"],
        },
    )
    r_ids = [e["r_eventid"] for e in eventos if e.get("r_eventid") not in (None, "0", "")]
    recuperacoes = _zbx("event.get", {"output": ["eventid", "clock"], "eventids": r_ids}) if r_ids else []
    fim = {r["eventid"]: int(r["clock"]) for r in recuperacoes}
    return historico_quedas(eventos, fim, agora)


# Histórico muda devagar: 10 min basta e a página não espera o Zabbix.
# Falha vira None, que não substitui um histórico bom já guardado.
_cache_quedas: CacheSWR[dict[str, dict] | None] = CacheSWR("cftv.quedas", ttl=600, valido=lambda v: v is not None)


def get_cached_historico_quedas() -> dict[str, dict]:
    """Quedas de ping por host nos últimos 30 dias (cache 10 min; falha = vazio)."""

    def carregar() -> dict[str, dict] | None:
        try:
            return _buscar_quedas()
        except (requests.RequestException, RuntimeError, KeyError, ValueError) as exc:
            log.warning("cftv_monitoring.quedas_falhou", erro=str(exc))
            return None

    return _cache_quedas.get(carregar) or {}


def get_cached_cftv_summary() -> dict:
    """Retorna dispositivos CFTV do Zabbix com cache TTL 2min."""
    global _cache_data, _cache_ts
    with _lock:
        if _cache_valido():
            log.debug("cftv_monitoring.cache.hit")
            return _cache_data  # type: ignore[return-value]

    log.info("cftv_monitoring.cache.miss")
    try:
        dados = _buscar_cftv()
    except Exception as exc:
        log.warning("cftv_monitoring.busca_falhou", erro=str(exc))
        dados = {"enabled": False, "devices": [], "_erro": str(exc)}
    with _lock:
        _cache_data = dados
        _cache_ts = time.monotonic()
    return dados


def invalidar_cache_cftv() -> None:
    """Descarta o cache de dispositivos (após alterar um host no Zabbix pelo dashboard)."""
    global _cache_data, _cache_ts
    with _lock:
        _cache_data, _cache_ts = None, 0.0
