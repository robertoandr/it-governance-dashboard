"""Dados de Rede para o dashboard.

Combina:
- Zabbix API: descoberta de rede (``dservice``) cruzada com os hosts já
  monitorados, discovery rules (drules) e itens de ping (latência/perda).
- InfluxDB: gov_infra_asset_detail do scanner nmap, quando ele roda — os
  dados dele (MAC, fabricante, portas) completam os do Zabbix.
- app.db: ``rede_vistos`` (primeira vez que cada IP apareceu e sugestão da IA).

Cache TTL 5min (a descoberta do Zabbix roda de hora em hora).

``montar_descobertos`` cruza cada host com a unidade (faixa de IP mais
específica) e com o ativo já cadastrado no inventário (pelo IP em metadata).
"""

from __future__ import annotations

import functools
import ipaddress
import os
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import requests
import structlog

from itgov.api.v1.rede_descoberta import aplicar_clientes, hosts_da_descoberta, latencia_por_unidade
from itgov.utils.cache_swr import CacheSWR

log = structlog.get_logger(__name__)

RECENTE_DIAS = 7  # IP que apareceu há menos disso é "novo na rede"

_CACHE_TTL = 300  # 5 minutos
_lock = threading.Lock()
_cache_data: dict | None = None
_cache_ts: float = 0.0


def _cache_valido() -> bool:
    return _cache_data is not None and (time.monotonic() - _cache_ts) < _CACHE_TTL


# ── Zabbix ────────────────────────────────────────────────────────────────────


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
        headers={"Content-Type": "application/json-rpc", "Authorization": f"Bearer {token}"},
        timeout=15,
        verify=False,  # nosec B501 # noqa: S501
    )
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"Zabbix API: {data['error']}")
    return data["result"]


_CHECK_TYPES = {
    "0": "SSH",
    "1": "LDAP",
    "2": "SMTP",
    "3": "FTP",
    "4": "HTTP",
    "5": "POP",
    "6": "NNTP",
    "7": "IMAP",
    "8": "TCP",
    "9": "Zabbix agent",
    "10": "SNMPv1",
    "11": "SNMPv2c",
    "12": "ICMP ping",
    "13": "SNMPv3",
    "14": "HTTPS",
    "15": "Telnet",
}


def _buscar_drules() -> list[dict]:
    """Retorna discovery rules com checks e status."""
    try:
        drules = _zbx(
            "drule.get",
            {
                "output": ["druleid", "name", "status", "iprange", "delay", "nextcheck"],
                "selectDChecks": ["type", "ports", "key_", "snmp_community"],
            },
        )
        result = []
        for dr in drules:
            checks = []
            for c in dr.get("dchecks", []):
                label = _CHECK_TYPES.get(str(c.get("type", "")), f"type={c.get('type')}")
                port = c.get("ports", "0")
                if port and port != "0":
                    label = f"{label}:{port}"
                checks.append(label)

            ranges = [r.strip() for r in dr.get("iprange", "").replace("\r\n", "\n").split(",") if r.strip()]
            nextcheck = int(dr.get("nextcheck", 0) or 0)
            result.append(
                {
                    "id": dr["druleid"],
                    "name": dr["name"],
                    "enabled": dr.get("status") == "0",
                    "ranges": ranges,
                    "delay": dr.get("delay", "—"),
                    "checks": checks,
                    "nextcheck": nextcheck,
                }
            )
        return result
    except Exception as exc:
        log.warning("rede_monitoring.drules_falhou", erro=str(exc))
        return []


def _buscar_descoberta() -> list[dict]:
    """Hosts da descoberta de rede do Zabbix, já classificados."""
    dchecks = {
        c["dcheckid"]: (str(c.get("type", "")), str(c.get("ports", "")))
        for c in _zbx("dcheck.get", {"output": ["dcheckid", "type", "ports"]})
    }
    dservices = _zbx("dservice.get", {"output": ["ip", "dcheckid", "status", "value", "dns", "lastup"]})
    zhosts = _zbx(
        "host.get",
        {"output": ["name"], "selectInterfaces": ["ip"], "selectHostGroups": ["name"], "filter": {"status": "0"}},
    )
    monitorados: dict[str, dict] = {}
    for h in zhosts:
        for itf in h.get("interfaces", []):
            if itf.get("ip"):
                monitorados.setdefault(
                    itf["ip"], {"nome": h["name"], "grupos": [g["name"] for g in h.get("hostgroups", [])]}
                )
    return hosts_da_descoberta(dservices, dchecks, monitorados)


def _buscar_medidas_ping() -> list[dict]:
    """Último ping, perda e disponibilidade em 24 h de cada host com ICMP no Zabbix."""
    itens = _zbx(
        "item.get",
        {
            "output": ["itemid", "key_", "lastvalue", "hostid"],
            "search": {"key_": "icmpping"},
            "startSearch": True,
            "filter": {"status": "0"},
            "monitored": True,
        },
    )
    hostids = sorted({i["hostid"] for i in itens})
    hosts = {
        h["hostid"]: h
        for h in _zbx("host.get", {"output": ["hostid", "name"], "hostids": hostids, "selectInterfaces": ["ip"]})
    }
    pings = [i["itemid"] for i in itens if i["key_"].split("[")[0] == "icmpping"]
    disp: dict[str, list[float]] = {}
    if pings:
        tendencias = _zbx(
            "trend.get",
            {"output": ["itemid", "value_avg"], "itemids": pings, "time_from": int(time.time()) - 86400},
        )
        for t in tendencias:
            disp.setdefault(t["itemid"], []).append(float(t["value_avg"]))

    por_host: dict[str, dict] = {}
    for i in itens:
        h = hosts.get(i["hostid"])
        ip = next((itf["ip"] for itf in (h or {}).get("interfaces", []) if itf.get("ip")), "")
        if h is None or not ip:
            continue
        m = por_host.setdefault(
            i["hostid"], {"ip": ip, "nome": h["name"], "ms": None, "perda": None, "up": False, "disp_24h": None}
        )
        chave = i["key_"].split("[")[0]
        try:
            valor = float(i.get("lastvalue") or 0)
        except ValueError:
            continue
        if chave == "icmppingsec":
            m["ms"] = valor * 1000
        elif chave == "icmppingloss":
            m["perda"] = valor
        elif chave == "icmpping":
            m["up"] = valor >= 1
            amostras = disp.get(i["itemid"])
            if amostras:
                m["disp_24h"] = sum(amostras) / len(amostras) * 100
    return list(por_host.values())


_cache_ping: CacheSWR[list[dict]] = CacheSWR("rede.ping", _CACHE_TTL, valido=bool)


def get_latencia(faixas: list[tuple[int, str]], nomes: dict[int, str]) -> dict[str, Any]:
    """Latência e estabilidade por unidade (cache de 5 min nas medidas do Zabbix)."""
    try:
        medidas = _cache_ping.get(_buscar_medidas_ping)
    except (requests.RequestException, RuntimeError) as exc:
        log.warning("rede_monitoring.ping_falhou", erro=str(exc))
        medidas = []
    return latencia_por_unidade(medidas, faixas, nomes)


# ── InfluxDB ──────────────────────────────────────────────────────────────────


def _query_influx(flux: str) -> list[dict[str, Any]]:
    from app.services.influxdb_provider import InfluxDBMetricsProvider

    return InfluxDBMetricsProvider()._query(flux)


def _ler_assets_influx() -> dict:
    """Lê gov_infra_assets (summary) e gov_infra_asset_detail (por host) do InfluxDB."""
    from app.config import get_settings

    bucket = get_settings().influx.bucket_raw

    # Último ponto de resumo da varredura
    rows_sum = _query_influx(f"""
from(bucket: "{bucket}")
  |> range(start: -7d)
  |> filter(fn: (r) => r._measurement == "gov_infra_assets" and r.scan == "summary")
  |> last()
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
""")

    # toString() antes do pivot evita schema collision entre campos string e int
    rows_hosts = _query_influx(f"""
from(bucket: "{bucket}")
  |> range(start: -7d)
  |> filter(fn: (r) => r._measurement == "gov_infra_asset_detail")
  |> group(columns: ["ip", "_field"])
  |> last()
  |> map(fn: (r) => ({{r with _value: string(v: r._value)}}))
  |> group(columns: ["ip", "category", "os_guess"])
  |> pivot(rowKey: ["_time", "ip", "category", "os_guess"], columnKey: ["_field"], valueColumn: "_value")
  |> group()
""")

    # Histórico de totais (para sparkline)
    rows_hist = _query_influx(f"""
from(bucket: "{bucket}")
  |> range(start: -7d)
  |> filter(fn: (r) => r._measurement == "gov_infra_assets" and r.scan == "summary" and r._field == "total_descobertos")
  |> aggregateWindow(every: 1h, fn: last, createEmpty: false)
""")

    s = rows_sum[-1] if rows_sum else {}

    def _int(v: object) -> int:
        try:
            return int(str(v)) if v is not None else 0
        except (ValueError, TypeError):
            return 0

    hosts: list[dict] = []
    for row in rows_hosts:
        hosts.append(
            {
                "ip": row.get("ip", row.get("_field", "?")),
                "category": row.get("category", "Outros"),
                "hostname": str(row.get("hostname", "")),
                "vendor": str(row.get("vendor", "")),
                "os_guess": row.get("os_guess", ""),
                "open_ports": _int(row.get("open_ports_count")),
                "has_agent": _int(row.get("has_agent")) == 1,
                "has_snmp": _int(row.get("has_snmp")) == 1,
                "has_ssh": _int(row.get("has_ssh")) == 1,
                "mac": str(row.get("mac") or ""),
                "tipo_sugerido": str(row.get("tipo_sugerido") or "outro"),
                "motivo": str(row.get("motivo") or ""),
                "portas": str(row.get("open_ports") or ""),
                "scan_time": str(row.get("_time", "")),
            }
        )

    hist = [int(r.get("_value", 0) or 0) for r in rows_hist]

    last_scan = s.get("_time", "")

    return {
        "has_data": bool(s),
        "last_scan": str(last_scan)[:19].replace("T", " ") if last_scan else None,
        "total": int(s.get("total_descobertos", 0) or 0),
        "linux": int(s.get("servidores_linux", 0) or 0),
        "windows": int(s.get("servidores_windows", 0) or 0),
        "rede": int(s.get("dispositivos_rede", 0) or 0),
        "databases": int(s.get("bancos_de_dados", 0) or 0),
        "web": int(s.get("aplicacoes_web", 0) or 0),
        "genericos": int(s.get("hosts_genericos", 0) or 0),
        "com_agente": sum(1 for h in hosts if h["has_agent"]),
        "com_snmp": sum(1 for h in hosts if h["has_snmp"]),
        "hosts": sorted(hosts, key=lambda x: (x["category"], x["ip"])),
        "history": hist,
    }


# ── Revisão: unidade + ativo cadastrado ──────────────────────────────────────

SEM_UNIDADE = "sem"
STATUS_NOVO = "novos"
STATUS_CADASTRADO = "cadastrados"
STATUS_RECENTE = "recentes"


@functools.lru_cache(maxsize=2048)
def _rede(cidr: str) -> ipaddress.IPv4Network | ipaddress.IPv6Network | None:
    """Faixa já convertida: a página Rede testa ~1700 IPs contra as mesmas faixas."""
    try:
        return ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        return None


def unidade_do_ip(ip: str, faixas: list[tuple[int, str]]) -> int | None:
    """Unidade cuja faixa contém ``ip``; com sobreposição, vence a mais específica.

    Args:
        ip: Endereço descoberto.
        faixas: Pares ``(unidade_id, cidr)``.

    Returns:
        Id da unidade, ou None se nenhuma faixa contém o IP.
    """
    try:
        endereco = ipaddress.ip_address(ip)
    except ValueError:
        return None
    melhor: tuple[int, int] | None = None  # (prefixlen, unidade_id)
    for unidade_id, cidr in faixas:
        rede = _rede(cidr)
        if rede is None:
            continue
        if endereco.version == rede.version and endereco in rede and (melhor is None or rede.prefixlen > melhor[0]):
            melhor = (rede.prefixlen, unidade_id)
    return melhor[1] if melhor else None


def montar_descobertos(
    hosts: list[dict],
    faixas: list[tuple[int, str]],
    ativos_por_ip: dict[str, dict],
    unidades: dict[int, str],
    filtro_unidade: set[int] | str | None = None,
    filtro_status: str = "",
    filtro_tipo: str = "",
) -> dict[str, Any]:
    """Prepara a fila de revisão da página Rede.

    Args:
        hosts: Hosts de ``get_cached_rede_summary``.
        faixas: Pares ``(unidade_id, cidr)`` das unidades ativas.
        ativos_por_ip: IP → ``{"id", "nome", "tipo"}`` dos ativos já cadastrados.
        unidades: Id → nome completo da unidade.
        filtro_unidade: Ids aceitos, ``SEM_UNIDADE`` ou None para todas.
        filtro_status: ``STATUS_NOVO`` (sem cadastro), ``STATUS_CADASTRADO``,
            ``STATUS_RECENTE`` (apareceu na rede há pouco) ou "" para todos.
        filtro_tipo: Só hosts com este ``tipo_sugerido``; "" para todos. Não
            entra em ``por_tipo``, que conta o recorte inteiro para servir de filtro.

    Returns:
        ``hosts`` enriquecidos e filtrados, mais contagens do recorte por unidade.
    """
    linhas: list[dict] = []
    for h in hosts:
        uid = unidade_do_ip(h["ip"], faixas)
        if filtro_unidade == SEM_UNIDADE and uid is not None:
            continue
        if isinstance(filtro_unidade, set) and uid not in filtro_unidade:
            continue
        ativo = ativos_por_ip.get(h["ip"])
        if filtro_status == STATUS_NOVO and ativo:
            continue
        if filtro_status == STATUS_CADASTRADO and not ativo:
            continue
        if filtro_status == STATUS_RECENTE and not h.get("recente"):
            continue
        linhas.append({**h, "unidade_id": uid, "unidade": unidades.get(uid, "") if uid else "", "ativo": ativo})

    por_tipo: dict[str, int] = {}
    for linha in linhas:
        por_tipo[linha["tipo_sugerido"]] = por_tipo.get(linha["tipo_sugerido"], 0) + 1
    if filtro_tipo:
        linhas = [linha for linha in linhas if linha["tipo_sugerido"] == filtro_tipo]
    novos = sum(1 for linha in linhas if not linha["ativo"])
    return {
        "hosts": sorted(
            linhas,
            key=lambda x: (x["ativo"] is not None, not x.get("recente"), x["unidade"] or "~", _ip_key(x["ip"])),
        ),
        "total": len(linhas),
        "novos": novos,
        "cadastrados": len(linhas) - novos,
        "recentes": sum(1 for linha in linhas if linha.get("recente")),
        "por_tipo": dict(sorted(por_tipo.items(), key=lambda kv: -kv[1])),
    }


def _ip_key(ip: str) -> tuple[int, int]:
    try:
        endereco = ipaddress.ip_address(ip)
    except ValueError:
        return (9, 0)
    return (endereco.version, int(endereco))


def host_descoberto(ip: str) -> dict | None:
    """Último registro do host ``ip`` na descoberta (via cache da página)."""
    return next((h for h in get_cached_rede_summary().get("hosts", []) if h["ip"] == ip), None)


# ── Montagem final ─────────────────────────────────────────────────────────────


def _juntar(zabbix: list[dict], nmap: list[dict]) -> list[dict]:
    """Une as duas fontes por IP; o nmap completa MAC, fabricante e portas.

    O tipo vindo do Zabbix vale quando o host já é monitorado lá (sinal mais
    forte); fora isso, vale o do nmap, que vê MAC e mais portas.
    """
    por_ip = {h["ip"]: dict(h) for h in zabbix}
    for n in nmap:
        atual = por_ip.get(n["ip"])
        if atual is None:
            por_ip[n["ip"]] = {**n, "online": True, "origem": "nmap", "zabbix_grupos": [], "snmp_descr": ""}
            continue
        for campo in ("hostname", "vendor", "os_guess", "mac", "portas"):
            if n.get(campo) and not atual.get(campo):
                atual[campo] = n[campo]
        if not atual.get("zabbix_grupos") and n.get("tipo_sugerido") not in (None, "", "outro"):
            atual["tipo_sugerido"], atual["motivo"] = n["tipo_sugerido"], n.get("motivo", "")
        atual["origem"] = "zabbix+nmap"
    return list(por_ip.values())


def _clientes_fortigate() -> list[dict]:
    """DHCP/ARP dos FortiGates; falha de leitura não derruba a página."""
    from itgov.services.fortigate_api import get_cached_clientes

    try:
        return get_cached_clientes()
    except (requests.RequestException, ValueError) as exc:
        log.warning("rede_monitoring.fortigate_falhou", erro=str(exc))
        return []


def _utc(dt: datetime) -> datetime:
    # SQLite devolve datetime sem fuso mesmo gravando com UTC
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _marco(fortigate: str) -> str:
    return f"fortigate:{fortigate}"[:45]


def _registrar_vistos(hosts: list[dict]) -> dict[str, Any]:
    """Grava IPs novos em ``rede_vistos`` e devolve o registro de cada IP.

    Na primeira carga (tabela vazia) tudo entra como ``baseline``: o que já
    estava na rede não aparece como novo. O mesmo vale para a primeira leitura
    de cada FortiGate (marcador ``fortigate:<nome>`` na própria tabela), senão
    os clientes DHCP de uma unidade apareceriam todos como novos de uma vez.
    """
    from flask import has_app_context
    from sqlalchemy.exc import SQLAlchemyError

    from app.extensions import db
    from app.models.rede import RedeVisto

    if not has_app_context():
        return {}
    try:
        vistos = {v.ip: v for v in RedeVisto.query.all()}
        primeira_carga = not vistos
        agora = datetime.now(UTC)
        fortigates_novos = {
            h["fortigate"] for h in hosts if h.get("fortigate") and _marco(h["fortigate"]) not in vistos
        }
        for h in hosts:
            v = vistos.get(h["ip"])
            if v is None:
                baseline = primeira_carga or h.get("fortigate") in fortigates_novos
                v = RedeVisto(ip=h["ip"], primeiro_visto=agora, ultimo_visto=agora, baseline=baseline)
                db.session.add(v)
                vistos[h["ip"]] = v
            elif h.get("online"):
                v.ultimo_visto = agora
        for nome in fortigates_novos:
            db.session.add(RedeVisto(ip=_marco(nome), primeiro_visto=agora, ultimo_visto=agora, baseline=True))
        db.session.commit()
        return vistos
    except SQLAlchemyError as exc:
        db.session.rollback()
        log.warning("rede_monitoring.vistos_falhou", erro=str(exc))
        return {}


def _enriquecer(hosts: list[dict], vistos: dict[str, Any], agora: datetime) -> None:
    """Marca ``recente`` e aplica a sugestão da IA onde as regras não souberam."""
    limite = agora - timedelta(days=RECENTE_DIAS)
    for h in hosts:
        v = vistos.get(h["ip"])
        h["recente"] = bool(v and not v.baseline and _utc(v.primeiro_visto) >= limite)
        h["primeiro_visto"] = _utc(v.primeiro_visto).isoformat() if v else ""
        h["ia_nome"] = (v.ia_nome if v else "") or ""
        if v and v.ia_tipo and v.ia_tipo != "outro" and h.get("tipo_sugerido") == "outro":
            h["tipo_sugerido"], h["motivo"], h["por_ia"] = v.ia_tipo, f"IA: {v.ia_motivo}", True


def _buscar_rede() -> dict:
    from flask import current_app, has_app_context

    from app.services.rede_ia import ia_ativa, processar_em_segundo_plano

    drules = _buscar_drules()
    erro = ""
    try:
        zabbix = _buscar_descoberta()
    except (requests.RequestException, RuntimeError) as exc:
        log.warning("rede_monitoring.descoberta_falhou", erro=str(exc))
        zabbix, erro = [], str(exc)
    try:
        influx = _ler_assets_influx()
    except Exception as exc:  # provider do Influx levanta tipos variados (rede, auth, query)
        log.warning("rede_monitoring.influx_falhou", erro=str(exc))
        influx = {"has_data": False, "hosts": [], "last_scan": None}

    hosts = _juntar(zabbix, influx.get("hosts", []))
    clientes = _clientes_fortigate()
    aplicar_clientes(hosts, clientes)
    _enriquecer(hosts, _registrar_vistos(hosts), datetime.now(UTC))
    if has_app_context():
        processar_em_segundo_plano(current_app._get_current_object(), hosts)  # type: ignore[attr-defined]

    all_ranges: list[str] = []
    for dr in drules:
        for r in dr["ranges"]:
            if r not in all_ranges:
                all_ranges.append(r)

    return {
        "enabled": True,
        "hosts": hosts,
        "total": len(hosts),
        "online": sum(1 for h in hosts if h.get("online")),
        "recentes": sum(1 for h in hosts if h.get("recente")),
        "fontes": {
            "zabbix": len(zabbix),
            "nmap": len(influx.get("hosts", [])),
            "nmap_ultimo": influx.get("last_scan"),
            "fortigate": len(clientes),
        },
        "ia_ativa": ia_ativa(),
        "erro_descoberta": erro,
        "drules": drules,
        "active_drules": [dr for dr in drules if dr["enabled"]],
        "scan_ranges": all_ranges,
    }


def get_cached_rede_summary() -> dict:
    """Retorna dados de rede com cache TTL 5min."""
    global _cache_data, _cache_ts
    with _lock:
        if _cache_valido():
            log.debug("rede_monitoring.cache.hit")
            return _cache_data  # type: ignore[return-value]

    log.info("rede_monitoring.cache.miss")
    try:
        dados = _buscar_rede()
    except Exception as exc:
        log.warning("rede_monitoring.busca_falhou", erro=str(exc))
        dados = {
            "enabled": False,
            "hosts": [],
            "total": 0,
            "online": 0,
            "recentes": 0,
            "fontes": {"zabbix": 0, "nmap": 0, "nmap_ultimo": None},
            "ia_ativa": False,
            "erro_descoberta": str(exc),
            "drules": [],
            "active_drules": [],
            "scan_ranges": [],
        }
    with _lock:
        _cache_data = dados
        _cache_ts = time.monotonic()
    return dados
