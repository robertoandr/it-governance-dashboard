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
from typing import Any

import requests
import structlog

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
            "selectInterfaces": ["ip"],
            "selectTags": ["tag", "value"],
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
        for t in triggers or []:
            for h in t.get("hosts", []):
                hid = h["hostid"]
                problems_by_host[hid] = problems_by_host.get(hid, 0) + 1

    # ── 4. Um registro por dispositivo, com status de ping ────────────────────
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
                "ip": (h.get("interfaces") or [{}])[0].get("ip", "?"),
                "subcat": subcat,
                "andar": tags.get("andar", "?"),
                "gravador": gravador_do_dispositivo(h["host"], subcat, tags),
                "is_gravador": subcat in SUBCATS_GRAVADOR,
                "canal": tags.get("canal") or tags.get("canal_nvr") or "",
                "loja": tags.get("loja", ""),
                "vendor": tags.get("vendor", ""),
                "model": tags.get("model", ""),
                "status": status,
                "problems": problems_by_host.get(hid, 0),
            }
        )

    return {"enabled": True, "devices": devices}


SUBCATS_GRAVADOR = frozenset({"dvr", "nvr"})
SEM_UNIDADE = "sem"


def gravador_do_dispositivo(host: str, subcat: str, tags: dict[str, str]) -> str:
    """Nome do gravador (DVR/NVR) ao qual o dispositivo pertence.

    O próprio gravador usa o nome do host (ex.: ``DVR-1``), que é o mesmo valor
    da tag ``dvr`` das suas câmeras. Câmeras Hikvision apontam para o NVR via
    ``parent_nvr``. Dispositivos sem gravador (faciais, antenas) retornam "".

    Args:
        host: Nome técnico do host no Zabbix.
        subcat: Tag ``subcategory`` do host.
        tags: Todas as tags do host.

    Returns:
        Chave do gravador, ou string vazia.
    """
    if subcat in SUBCATS_GRAVADOR:
        return host
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
) -> dict:
    """Agrupa dispositivos em cards por gravador e aplica o filtro de unidade.

    A unidade de um dispositivo vem do vínculo do seu gravador; sem vínculo,
    cai para a tag ``loja`` quando ela bate com o nome de uma unidade.

    Args:
        dados: Resultado de ``get_cached_cftv_summary``.
        unidade_por_gravador: Gravador → id da unidade (ou None).
        unidades: Id → nome completo da unidade, para exibição.
        unidade_por_loja: Nome de unidade (minúsculo) → id, para a tag ``loja``.
        filtro: Ids de unidade aceitos, ``SEM_UNIDADE`` para os sem unidade,
            ou None para todos.

    Returns:
        Dicionário com KPIs do recorte, ``cards`` por gravador e listas de
        offline/manutenção.
    """
    devices = dados.get("devices", [])

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
        host_gravador = next((d for d in devs if d["is_gravador"]), None)
        itens = sorted((d for d in devs if not d["is_gravador"]), key=_canal_key)
        vendors = sorted({d["vendor"] for d in devs if d["vendor"]})
        cards.append(
            {
                "gravador": gravador,
                "titulo": host_gravador["name"] if host_gravador else (gravador or "Sem gravador"),
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
        "down_list": sorted((d for d in todos if d["status"] == "down"), key=lambda d: (d["gravador"], d["name"])),
        "maint_list": sorted((d for d in todos if d["status"] == "maint"), key=lambda d: d["name"]),
    }


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
