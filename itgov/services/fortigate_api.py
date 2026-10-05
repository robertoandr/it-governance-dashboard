"""Leitura direta dos FortiGates pela REST API (token de API somente leitura).

Configuração por unidade no ambiente::

    FORTIGATE_SEDE_URL=https://172.29.3.254:5443
    FORTIGATE_SEDE_TOKEN=<token>

Unidade com URL e sem token aparece como pendente na página Links WAN.

Duas leituras, cada uma com cache próprio:

- ``get_cached_fortigates``: sistema, SD-WAN (health-checks) e interfaces WAN,
  no mesmo formato de ``fortinet_service`` (que lê pelo template do Zabbix),
  para a página Links WAN usar as duas fontes juntas.
- ``get_cached_clientes``: concessões DHCP e tabela ARP, que dão nome, MAC e
  classe DHCP aos IPs da página Rede.

Os FortiGates usam certificado autoassinado na interface de gerência (CN
"FortiGate", sem o IP), que nenhuma CA valida. Com ``FORTIGATE_<UNIDADE>_SHA256``
(impressão digital do certificado, ``openssl x509 -fingerprint -sha256``) a
conexão só é aceita se o certificado for exatamente aquele — protege contra
intermediário mesmo sem CA. Sem a impressão digital, a conexão segue sem
verificação (rede interna, token só de leitura) e isso é registrado no log.
"""

from __future__ import annotations

import ipaddress
import os
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import urlparse

import requests
import structlog
from requests.adapters import HTTPAdapter

from itgov.utils.cache_swr import CacheSWR

log = structlog.get_logger(__name__)

_TIMEOUT = 15
_ENV = re.compile(r"^FORTIGATE_([A-Z0-9_]+)_URL$")


def configurados() -> tuple[list[dict[str, str]], list[str]]:
    """FortiGates com URL e token, e os nomes dos que só têm URL (token pendente).

    Returns:
        ``(prontos, pendentes)``; cada pronto tem ``nome``, ``url`` e ``token``.
    """
    prontos, pendentes = [], []
    for chave, url in sorted(os.environ.items()):
        m = _ENV.match(chave)
        if not m or not url.strip():
            continue
        nome = m.group(1).replace("_", " ").title()
        token = os.getenv(f"FORTIGATE_{m.group(1)}_TOKEN", "").strip()
        if token:
            sha256 = os.getenv(f"FORTIGATE_{m.group(1)}_SHA256", "").strip()
            prontos.append({"nome": nome, "url": url.strip().rstrip("/"), "token": token, "sha256": sha256})
        else:
            pendentes.append(nome)
    return prontos, pendentes


class _CertificadoFixo(HTTPAdapter):
    """Aceita só o certificado com a impressão digital SHA-256 informada."""

    def __init__(self, sha256: str) -> None:
        self._sha256 = sha256.replace(":", "").lower()
        super().__init__()

    def init_poolmanager(self, *args: Any, **kwargs: Any) -> None:
        kwargs["assert_fingerprint"] = self._sha256
        super().init_poolmanager(*args, **kwargs)


def _sessao(fw: dict[str, str]) -> requests.Session:
    sessao = requests.Session()
    if fw.get("sha256"):
        sessao.mount("https://", _CertificadoFixo(fw["sha256"]))
    else:
        log.warning("fortigate_api.sem_impressao_digital", fortigate=fw["nome"])
    return sessao


def _get(fw: dict[str, str], caminho: str) -> Any:
    with _sessao(fw) as sessao:
        resp = sessao.get(
            f"{fw['url']}/api/v2/{caminho}",
            headers={"Authorization": f"Bearer {fw['token']}"},
            timeout=_TIMEOUT,
            # Autoassinado: a CA não valida; a confiança vem da impressão digital (_CertificadoFixo)
            verify=False,  # nosec B501
        )
    resp.raise_for_status()
    dados = resp.json()
    if dados.get("status") != "success":
        raise ValueError(f"FortiGate {caminho}: {dados.get('status')}")
    return dados.get("results")


# ── Sistema, SD-WAN e interfaces ─────────────────────────────────────────────


def status_link(latencia: float | None, perda: float | None, caiu: bool) -> str:
    """Mesma régua de ``fortinet_service``: down, degraded, warn ou up."""
    perda = perda or 0.0
    if caiu:
        return "down"
    if (latencia is not None and latencia >= 300) or perda >= 5:
        return "degraded"
    if (latencia is not None and latencia >= 100) or perda >= 1:
        return "warn"
    return "up"


def montar_sdwan(health: dict[str, dict[str, dict]], rotulos: dict[str, str]) -> list[dict[str, Any]]:
    """Health-checks do SD-WAN agrupados por SLA, só com os membros de cada um.

    O FortiGate devolve todas as interfaces em cada SLA; as que não participam
    vêm com ``status: error`` e ficam de fora.
    """
    resultado = []
    for sla, membros in sorted((health or {}).items()):
        linhas = []
        for iface, m in sorted(membros.items()):
            if m.get("status") not in ("up", "down"):
                continue
            lat = m.get("latency")
            perda = m.get("packet_loss")
            linhas.append(
                {
                    "iface": iface,
                    "label": rotulos.get(iface) or iface,
                    "latency_ms": round(float(lat), 1) if lat is not None else None,
                    "jitter_ms": round(float(m["jitter"]), 1) if m.get("jitter") is not None else None,
                    "loss_pct": round(float(perda), 1) if perda is not None else None,
                    "status": status_link(lat, perda, m.get("status") == "down"),
                }
            )
        if linhas:
            resultado.append({"sla": sla, "members": linhas})
    return resultado


def montar_wans(interfaces: dict[str, dict], wans: list[dict]) -> list[dict[str, Any]]:
    """Interfaces com papel WAN: operadora (alias), IP, link, velocidade e tráfego acumulado."""
    linhas = []
    for cfg in wans:
        nome = cfg.get("name", "")
        mon = (interfaces or {}).get(nome, {})
        ip = (cfg.get("ip") or "").split(" ")[0]
        linhas.append(
            {
                "iface": nome,
                "operadora": cfg.get("alias") or mon.get("alias", "").strip("()") or nome,
                "ip": "" if ip == "0.0.0.0" else ip,  # nosec B104 — valor lido, não bind
                "link": bool(mon.get("link")),
                "speed_mbps": mon.get("speed") or 0,
                "rx_gb": round((mon.get("rx_bytes") or 0) / 1e9, 1),
                "tx_gb": round((mon.get("tx_bytes") or 0) / 1e9, 1),
                "erros": (mon.get("rx_errors") or 0) + (mon.get("tx_errors") or 0),
            }
        )
    return linhas


# Interfaces internas do FortiOS que não são redes de usuários (FortiLink/NAC)
_REDES_DE_SISTEMA = ("nac_segment", "quarantine", "rspan", "fortilink")


def montar_redes(cfg: list[dict]) -> list[dict[str, Any]]:
    """Redes internas do FortiGate (interfaces LAN e VLANs com IP), para a topologia.

    Ficam de fora as WANs, os túneis e as interfaces de sistema (NAC, quarentena,
    RSPAN do FortiLink).

    Returns:
        Um dict por rede: ``iface``, ``alias``, ``cidr``, ``gateway``, ``vlan``,
        ``pai`` e ``ativa``.
    """
    redes = []
    for c in cfg or []:
        nome = c.get("name", "")
        ip_mask = (c.get("ip") or "").split()
        if c.get("role") == "wan" or c.get("type") == "tunnel" or nome.startswith(_REDES_DE_SISTEMA):
            continue
        if len(ip_mask) != 2 or ip_mask[0] == "0.0.0.0":  # nosec B104 — valor lido, não bind
            continue
        try:
            rede = ipaddress.ip_network(f"{ip_mask[0]}/{ip_mask[1]}", strict=False)
        except ValueError:
            continue
        redes.append(
            {
                "iface": nome,
                "alias": c.get("alias") or "",
                "cidr": str(rede),
                "gateway": ip_mask[0],
                "vlan": int(c.get("vlanid") or 0),
                "pai": c.get("interface") or "",
                "ativa": c.get("status", "up") == "up",
            }
        )
    return sorted(redes, key=lambda r: (ipaddress.ip_network(r["cidr"]).network_address, r["iface"]))


def montar_ipsec(tuneis: list[dict]) -> list[dict[str, Any]]:
    """Túneis IPsec (``monitor/vpn/ipsec``) com status e tráfego.

    Túneis antigos (nome com ``OLD``) ficam de fora: estão desligados de propósito.

    Args:
        tuneis: Resultado cru da API.

    Returns:
        Um item por túnel: nome, gateway remoto, status (``up`` se alguma fase 2
        está de pé) e tráfego em GB.
    """
    saida = []
    for t in tuneis:
        nome = t.get("name") or ""
        if not nome or "OLD" in nome.upper():
            continue
        fases = t.get("proxyid") or []
        saida.append(
            {
                "nome": nome,
                "remoto": t.get("rgwy") or "",
                "status": "up" if any(f.get("status") == "up" for f in fases) else "down",
                "rx_gb": round((t.get("incoming_bytes") or 0) / 1e9, 1),
                "tx_gb": round((t.get("outgoing_bytes") or 0) / 1e9, 1),
            }
        )
    return sorted(saida, key=lambda t: t["nome"])


def _ler_ipsec(fw: dict[str, str]) -> list[dict[str, Any]]:
    # Opcional: token sem permissão de VPN não derruba o resto da leitura
    try:
        return montar_ipsec(_get(fw, "monitor/vpn/ipsec") or [])
    except (requests.RequestException, ValueError) as exc:
        log.warning("fortigate_api.ipsec_falhou", fortigate=fw["nome"], erro=str(exc))
        return []


def _ler_fortigate(fw: dict[str, str]) -> dict[str, Any]:
    base: dict[str, Any] = {
        "hostid": f"api:{fw['nome']}",
        "host": urlparse(fw["url"]).hostname or fw["url"],
        "name": fw["nome"],
        "unidade": fw["nome"],
        "origem": "api",
        "has_data": False,
        "api_up": False,
        "cpu_pct": None,
        "mem_pct": None,
        "uptime_h": None,
        "sdwan": [],
        "wans": [],
        "redes": [],
        "ipsec": [],
        "problems": [],
        "problem_count": 0,
        "modelo": "",
        "erro": "",
    }
    try:
        status = _get(fw, "monitor/system/status") or {}
        perf = _get(fw, "monitor/system/performance/status") or {}
        cfg = _get(fw, "cmdb/system/interface?format=name|alias|role|ip|vlanid|interface|type|status") or []
        interfaces = _get(fw, "monitor/system/interface") or {}
        health = _get(fw, "monitor/virtual-wan/health-check") or {}
    except (requests.RequestException, ValueError) as exc:
        log.warning("fortigate_api.leitura_falhou", fortigate=fw["nome"], erro=str(exc))
        return {**base, "erro": str(exc)}

    wans_cfg = [c for c in cfg if c.get("role") == "wan"]
    cpu = (perf.get("cpu") or {}).get("idle")
    mem = perf.get("mem") or {}
    rotulos = {c["name"]: c.get("alias") or c["name"] for c in wans_cfg}
    return {
        **base,
        "name": status.get("hostname") or fw["nome"],
        "has_data": True,
        "api_up": True,
        "cpu_pct": round(100 - float(cpu), 1) if cpu is not None else None,
        "mem_pct": round(mem["used"] / mem["total"] * 100, 1) if mem.get("total") else None,
        "sdwan": montar_sdwan(health, rotulos),
        "wans": montar_wans(interfaces, wans_cfg),
        "redes": montar_redes(cfg),
        "ipsec": _ler_ipsec(fw),
        "modelo": status.get("model", ""),
    }


def _buscar_fortigates() -> list[dict[str, Any]]:
    prontos, _ = configurados()
    if not prontos:
        return []
    with ThreadPoolExecutor(max_workers=len(prontos)) as ex:
        return list(ex.map(_ler_fortigate, prontos))


_cache_fw: CacheSWR[list[dict[str, Any]]] = CacheSWR("fortigate.sistema", 60, valido=bool)


def get_cached_fortigates() -> list[dict[str, Any]]:
    """Estado de cada FortiGate configurado (cache de 60 s)."""
    return _cache_fw.get(_buscar_fortigates)


# ── Clientes (DHCP + ARP) ────────────────────────────────────────────────────


def montar_clientes(fortigate: str, dhcp: list[dict], arp: list[dict]) -> list[dict[str, Any]]:
    """Um registro por IP: DHCP (nome, MAC, classe) completado pela ARP (MAC de IP fixo).

    Args:
        fortigate: Nome do FortiGate de origem.
        dhcp: ``monitor/system/dhcp``.
        arp: ``monitor/network/arp``.

    Returns:
        Lista com ``ip``, ``mac``, ``hostname``, ``vci``, ``interface``,
        ``fonte`` ("dhcp" ou "arp") e ``fortigate``.
    """
    por_ip: dict[str, dict[str, Any]] = {}
    for c in dhcp or []:
        ip = c.get("ip")
        if not ip or c.get("status") not in (None, "leased"):
            continue
        por_ip[ip] = {
            "ip": ip,
            "mac": (c.get("mac") or "").upper(),
            "hostname": (c.get("hostname") or "").strip(),
            "vci": (c.get("vci") or "").strip(),
            "interface": c.get("interface", ""),
            "fonte": "dhcp",
            "fortigate": fortigate,
        }
    for a in arp or []:
        ip = a.get("ip")
        if not ip:
            continue
        atual = por_ip.get(ip)
        if atual is None:
            por_ip[ip] = {
                "ip": ip,
                "mac": (a.get("mac") or "").upper(),
                "hostname": "",
                "vci": "",
                "interface": a.get("interface", ""),
                "fonte": "arp",
                "fortigate": fortigate,
            }
        elif not atual["mac"]:
            atual["mac"] = (a.get("mac") or "").upper()
    return list(por_ip.values())


def _ler_clientes(fw: dict[str, str]) -> list[dict[str, Any]]:
    try:
        return montar_clientes(fw["nome"], _get(fw, "monitor/system/dhcp"), _get(fw, "monitor/network/arp"))
    except (requests.RequestException, ValueError) as exc:
        log.warning("fortigate_api.clientes_falhou", fortigate=fw["nome"], erro=str(exc))
        return []


def _buscar_clientes() -> list[dict[str, Any]]:
    prontos, _ = configurados()
    if not prontos:
        return []
    with ThreadPoolExecutor(max_workers=len(prontos)) as ex:
        return [c for lista in ex.map(_ler_clientes, prontos) for c in lista]


_cache_clientes: CacheSWR[list[dict[str, Any]]] = CacheSWR("fortigate.clientes", 300, valido=bool)


def get_cached_clientes() -> list[dict[str, Any]]:
    """Clientes vistos pelos FortiGates (cache de 5 min); vazio sem FortiGate configurado."""
    if not configurados()[0]:
        return []
    return _cache_clientes.get(_buscar_clientes)
