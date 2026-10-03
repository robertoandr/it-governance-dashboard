"""Regras da página Rede sobre os dados da descoberta do Zabbix.

Funções puras (sem rede nem banco), usadas por ``rede_monitoring``:

- ``hosts_da_descoberta``: junta ``dservice`` por IP e cruza com os hosts já
  monitorados no Zabbix, no mesmo formato que o scanner nmap gravava.
- ``classificar``: tipo sugerido + motivo, a partir de grupo no Zabbix,
  descrição SNMP e portas abertas.
- ``sigla_unidade`` / ``sugerir_nome``: nomenclatura ``SIGLA-TIPO-NNN``.
- ``resumo_por_unidade`` e ``latencia_por_unidade``: cards e tabela por unidade.
"""

from __future__ import annotations

import ipaddress
import re
import statistics
import unicodedata
from collections.abc import Iterable
from typing import Any

# Tipos de check do Zabbix (dcheck.type) que viram porta TCP conhecida
_PORTA_DO_CHECK = {"0": 22, "4": 80, "9": 10050, "14": 443, "15": 23}
_CHECK_TCP = "8"
_CHECKS_SNMP = {"10", "11", "13"}

PORTAS_SERVIDOR = {22, 3389, 10050, 3306, 5432, 1433}

_VENDORS_IMPRESSORA = ("ricoh", "lexmark", "brother", "epson", "kyocera", "xerox", "canon", "laserjet", "printer")
_VENDORS_SWITCH = ("cisco", "mikrotik", "routeros", "huawei", "juniper", "tp-link", "d-link", "switch", "3com")
_VENDORS_CAMERA = ("hikvision", "intelbras", "dahua")
_VENDORS_AP = ("ubiquiti", "unifi", "aruba", "ruckus")

# Grupo do host no Zabbix → (tipo, rótulo do motivo)
_GRUPOS = (
    ("CFTV/Cameras", "camera"),
    ("CFTV/DVRs", "camera"),
    ("CFTV/NVRs", "camera"),
    ("CFTV/Controle de acesso", "outro"),
    ("Servidores", "servidor"),
    ("Linux servers", "servidor"),
    ("Zabbix servers", "servidor"),
)

SIGLA_TIPO: dict[str, str] = {
    "servidor": "SRV",
    "vm": "VM",
    "switch": "SW",
    "firewall": "FW",
    "ap": "AP",
    "impressora": "IMP",
    "camera": "CAM",
    "endpoint": "EST",
    "movel": "MOV",
    "app": "APP",
    "licenca": "LIC",
    "outro": "DSP",
}


def hosts_da_descoberta(
    dservices: list[dict[str, Any]],
    dchecks: dict[str, tuple[str, str]],
    monitorados: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Agrupa os serviços descobertos por IP e classifica cada host.

    Args:
        dservices: Resultado de ``dservice.get`` (ip, dcheckid, status, value, dns, lastup).
        dchecks: ``dcheckid`` → ``(tipo, portas)`` do ``dcheck.get``.
        monitorados: IP → ``{"nome", "grupos"}`` dos hosts já monitorados no Zabbix.

    Returns:
        Um dict por IP, com os campos que a página Rede já usava (``hostname``,
        ``vendor``, ``portas``, ``tipo_sugerido``...) mais ``online``,
        ``ultimo_visto`` (epoch), ``zabbix_host`` e ``snmp_descr``.
    """
    por_ip: dict[str, dict[str, Any]] = {}
    for svc in dservices:
        ip = svc.get("ip") or ""
        if not ip:
            continue
        h: dict[str, Any] = por_ip.setdefault(
            ip, {"portas": set(), "snmp": "", "dns": "", "online": False, "snmp_ok": False, "ultimo": 0}
        )
        tipo, portas = dchecks.get(str(svc.get("dcheckid", "")), ("", ""))
        if tipo == _CHECK_TCP and str(portas).isdigit():
            h["portas"].add(int(portas))
        elif tipo in _PORTA_DO_CHECK:
            h["portas"].add(_PORTA_DO_CHECK[tipo])
        elif tipo in _CHECKS_SNMP:
            h["snmp_ok"] = True
            h["snmp"] = h["snmp"] or (svc.get("value") or "")
        if svc.get("dns") and svc["dns"] not in ("_gateway",):
            h["dns"] = h["dns"] or svc["dns"]
        if str(svc.get("status")) == "0":
            h["online"] = True
        h["ultimo"] = max(h["ultimo"], int(svc.get("lastup") or 0))

    hosts = []
    for ip, h in por_ip.items():
        zbx = monitorados.get(ip, {})
        abertas = sorted(h["portas"])
        snmp = h["snmp"].strip()
        host: dict[str, Any] = {
            "ip": ip,
            "category": "Zabbix discovery",
            "hostname": zbx.get("nome") or h["dns"],
            "vendor": _fabricante(snmp),
            "os_guess": snmp[:80],
            "open_ports": len(abertas),
            "has_agent": 10050 in abertas,
            "has_snmp": h["snmp_ok"],
            "has_ssh": 22 in abertas,
            "mac": "",
            "portas": ",".join(str(p) for p in abertas),
            "scan_time": "",
            "online": h["online"],
            "ultimo_visto": h["ultimo"],
            "zabbix_host": zbx.get("nome", ""),
            "zabbix_grupos": zbx.get("grupos", []),
            "snmp_descr": snmp,
            "origem": "zabbix",
        }
        host["tipo_sugerido"], host["motivo"] = classificar(host)
        hosts.append(host)
    return hosts


def _fabricante(snmp: str) -> str:
    """Primeira palavra da descrição SNMP quando é um fabricante conhecido."""
    if not snmp:
        return ""
    baixo = snmp.lower()
    for nome in (*_VENDORS_IMPRESSORA, *_VENDORS_SWITCH, *_VENDORS_CAMERA, *_VENDORS_AP, "fortinet", "fortigate"):
        if nome in baixo and nome not in ("printer", "switch", "laserjet", "routeros"):
            return nome.title()
    return ""


def classificar(host: dict[str, Any]) -> tuple[str, str]:
    """Sugere o tipo do ativo e explica de onde veio a sugestão.

    A ordem vai do sinal mais confiável (já monitorado no Zabbix) ao mais fraco
    (portas). Sem nenhum sinal o host fica "outro" — só ping não diz o que é.

    Args:
        host: Host no formato de ``hosts_da_descoberta``.

    Returns:
        ``(tipo, motivo)``.
    """
    for grupo, tipo in _GRUPOS:
        if grupo in host.get("zabbix_grupos", []):
            return tipo, f"monitorado no Zabbix ({grupo})"

    descr = (host.get("snmp_descr") or "").lower()
    portas = {int(p) for p in str(host.get("portas") or "").split(",") if p.strip().isdigit()}
    if descr:
        if any(v in descr for v in _VENDORS_IMPRESSORA):
            return "impressora", "descrição SNMP de impressora"
        if "fortigate" in descr or "fortios" in descr:
            return "firewall", "descrição SNMP Fortinet"
        if any(v in descr for v in _VENDORS_AP):
            return "ap", "descrição SNMP de access point"
        if any(v in descr for v in _VENDORS_CAMERA):
            return "camera", "descrição SNMP de câmera/DVR"
        if any(v in descr for v in _VENDORS_SWITCH):
            return "switch", "descrição SNMP de equipamento de rede"
        if "windows" in descr:
            return ("servidor", "SNMP Windows com RDP") if 3389 in portas else ("endpoint", "SNMP Windows")
        if "linux" in descr:
            return "servidor", "SNMP Linux"

    por_dhcp = _pelo_dhcp(host.get("vci") or "", host.get("hostname") or "")
    if por_dhcp:
        return por_dhcp

    servidor = sorted(portas & PORTAS_SERVIDOR)
    if servidor:
        return "servidor", f"portas {servidor}"
    if host.get("has_snmp"):
        return "switch", "responde SNMP"
    if portas:
        return "outro", f"só portas {sorted(portas)}"
    return "outro", "só responde a ping"


# Nome de host (DHCP) → tipo; a ordem importa (celular antes de "estação")
_NOMES = (
    (re.compile(r"IPHONE|IPAD|GALAXY|REDMI|XIAOMI|MOTO[ -]?[A-Z0-9]|POCO|ANDROID", re.I), "movel", "celular/tablet"),
    (re.compile(r"PRINTER|IMPRESSORA|EPSON|RICOH|LEXMARK|^(?:BRN|NPI|HP)[0-9A-F]{6,12}$", re.I),
     "impressora", "impressora"),
    (re.compile(r"\b(?:CAM|DVR|NVR)|HIKVISION|INTELBRAS", re.I), "camera", "câmera/DVR"),
    (re.compile(r"\bSRV|SERVER|SERVIDOR", re.I), "servidor", "servidor"),
    (re.compile(r"^(?:DESKTOP|LAPTOP|NOTE|NB|PC|WS)[-_]|NOTEBOOK", re.I), "endpoint", "computador"),
    (re.compile(r"UNIFI|\bUAP|\bAP[-_]", re.I), "ap", "access point"),
)  # fmt: skip


def _pelo_dhcp(vci: str, hostname: str) -> tuple[str, str] | None:
    """Tipo pela classe DHCP (vci) e pelo nome de host que o próprio aparelho informa."""
    if hostname:
        for padrao, tipo, rotulo in _NOMES:
            if padrao.search(hostname):
                return tipo, f"nome {hostname} ({rotulo})"
    baixo = vci.lower()
    if baixo.startswith("android"):
        return "movel", f"DHCP {vci}"
    if baixo.startswith("msft"):
        return "endpoint", "DHCP Windows (MSFT)"
    if baixo.startswith("ubnt"):
        return "ap", "DHCP Ubiquiti"
    return None


def aplicar_clientes(hosts: list[dict[str, Any]], clientes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Completa os hosts com o DHCP/ARP dos FortiGates e acrescenta os que a varredura não viu.

    Nome e MAC só preenchem o que está vazio; o tipo é refeito quando as regras
    ainda não tinham sinal (``outro``) e o host não é monitorado no Zabbix.

    Args:
        hosts: Hosts da descoberta (e do nmap).
        clientes: ``fortigate_api.get_cached_clientes``.

    Returns:
        A lista de hosts atualizada (mesmos dicts, mais os novos).
    """
    por_ip = {h["ip"]: h for h in hosts}
    for c in clientes:
        h = por_ip.get(c["ip"])
        if h is None:
            h = {
                "ip": c["ip"], "category": "FortiGate", "hostname": "", "vendor": "", "os_guess": "", "open_ports": 0,
                "has_agent": False, "has_snmp": False, "has_ssh": False, "mac": "", "portas": "", "scan_time": "",
                "online": c["fonte"] == "arp", "ultimo_visto": 0, "zabbix_host": "", "zabbix_grupos": [],
                "snmp_descr": "", "origem": "fortigate", "tipo_sugerido": "outro", "motivo": "",
            }  # fmt: skip
            por_ip[c["ip"]] = h
            hosts.append(h)
        else:
            h["origem"] = f"{h.get('origem', 'zabbix')}+fortigate"
        h["hostname"] = h.get("hostname") or c["hostname"]
        h["mac"] = h.get("mac") or c["mac"]
        h["vci"] = c["vci"]
        h["fortigate"] = c["fortigate"]
        if h.get("tipo_sugerido") in (None, "", "outro") and not h.get("zabbix_grupos"):
            h["tipo_sugerido"], h["motivo"] = classificar(h)
    return hosts


def precisa_ia(host: dict[str, Any]) -> bool:
    """Host que as regras não classificaram mas tem algum sinal para a IA analisar."""
    if host.get("tipo_sugerido") != "outro" or host.get("zabbix_grupos"):
        return False
    return bool(host.get("portas") or host.get("snmp_descr") or host.get("hostname") or host.get("vci"))


# ── Nomenclatura ─────────────────────────────────────────────────────────────


def sigla_unidade(nome: str) -> str:
    """Sigla de 3 letras da unidade: "Shopping" → SHO, "Sede Centro" → SCE.

    Uma palavra: as 3 primeiras letras. Mais de uma: as iniciais, completadas
    com as letras seguintes da última palavra. Sem nome, ``GER`` (geral).
    """
    sem_acento = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode()
    palavras = re.findall(r"[A-Za-z]+", sem_acento)
    if not palavras:
        return "GER"
    if len(palavras) == 1:
        return palavras[0][:3].upper()
    sigla = "".join(p[0] for p in palavras)[:3]
    sigla += palavras[-1][1 : 1 + 3 - len(sigla)]
    return sigla.upper()


def sugerir_nome(tipo: str, sigla: str, existentes: Iterable[str]) -> str:
    """Próximo nome livre no padrão ``SIGLA-TIPO-NNN`` (ex.: ``SHO-CAM-012``).

    Args:
        tipo: Tipo do ativo.
        sigla: Sigla da unidade (``sigla_unidade``).
        existentes: Nomes de ativos já cadastrados.
    """
    prefixo = f"{sigla}-{SIGLA_TIPO.get(tipo, 'DSP')}-"
    usados = [int(n[len(prefixo) :]) for n in existentes if n.startswith(prefixo) and n[len(prefixo) :].isdigit()]
    return f"{prefixo}{max(usados, default=0) + 1:03d}"


# ── Por unidade ──────────────────────────────────────────────────────────────


def _unidade(ip: str, redes: list[tuple[int, ipaddress.IPv4Network | ipaddress.IPv6Network]]) -> int | None:
    try:
        endereco = ipaddress.ip_address(ip)
    except ValueError:
        return None
    melhor: tuple[int, int] | None = None
    for uid, rede in redes:
        if endereco.version == rede.version and endereco in rede and (melhor is None or rede.prefixlen > melhor[0]):
            melhor = (rede.prefixlen, uid)
    return melhor[1] if melhor else None


def _redes(faixas: list[tuple[int, str]]) -> list[tuple[int, ipaddress.IPv4Network | ipaddress.IPv6Network]]:
    redes = []
    for uid, cidr in faixas:
        try:
            redes.append((uid, ipaddress.ip_network(cidr, strict=False)))
        except ValueError:
            continue
    return redes


def resumo_por_unidade(
    hosts: list[dict[str, Any]],
    faixas: list[tuple[int, str]],
    nomes: dict[int, str],
    cadastrados: set[str],
) -> list[dict[str, Any]]:
    """Um card por unidade: total, online, sem cadastro e novos na rede.

    Args:
        hosts: Hosts já enriquecidos (com ``recente``).
        faixas: Pares ``(unidade_id, cidr)``.
        nomes: Id → nome da unidade.
        cadastrados: IPs que já são ativos no inventário.

    Returns:
        Lista ordenada pelo nome; "Sem unidade" (id None) por último, se houver.
    """
    redes = _redes(faixas)
    cards: dict[int | None, dict[str, Any]] = {}
    for h in hosts:
        uid = _unidade(h["ip"], redes)
        c = cards.setdefault(
            uid,
            {"id": uid, "nome": nomes.get(uid, "Sem unidade") if uid else "Sem unidade", "total": 0, "online": 0,
             "sem_cadastro": 0, "recentes": 0},
        )  # fmt: skip
        c["total"] += 1
        c["online"] += 1 if h.get("online") else 0
        c["sem_cadastro"] += 0 if h["ip"] in cadastrados else 1
        c["recentes"] += 1 if h.get("recente") else 0
    return sorted(cards.values(), key=lambda c: (c["id"] is None, c["nome"]))


def latencia_por_unidade(
    medidas: list[dict[str, Any]],
    faixas: list[tuple[int, str]],
    nomes: dict[int, str],
) -> dict[str, Any]:
    """Latência e estabilidade por unidade a partir dos itens de ping do Zabbix.

    Args:
        medidas: Um dict por host monitorado: ``ip``, ``nome``, ``ms`` (último
            tempo de resposta), ``perda`` (% de perda), ``up`` (último ping ok),
            ``disp_24h`` (% de pings ok nas últimas 24 h, ou None).
        faixas: Pares ``(unidade_id, cidr)``.
        nomes: Id → nome da unidade.

    Returns:
        ``unidades`` (uma linha por unidade) e ``piores`` (até 10 hosts com mais
        latência ou perda).
    """
    redes = _redes(faixas)
    grupos: dict[int | None, list[dict[str, Any]]] = {}
    for m in medidas:
        grupos.setdefault(_unidade(m["ip"], redes), []).append(m)

    linhas = []
    for uid, itens in grupos.items():
        tempos = sorted(m["ms"] for m in itens if m.get("up") and m.get("ms") is not None)
        perdas = [m["perda"] for m in itens if m.get("perda") is not None]
        disp = [m["disp_24h"] for m in itens if m.get("disp_24h") is not None]
        linhas.append(
            {
                "id": uid,
                "nome": nomes.get(uid, "Sem unidade") if uid else "Sem unidade",
                "hosts": len(itens),
                "offline": sum(1 for m in itens if not m.get("up")),
                "media_ms": round(statistics.fmean(tempos), 1) if tempos else None,
                "p95_ms": round(tempos[min(len(tempos) - 1, int(len(tempos) * 0.95))], 1) if tempos else None,
                "max_ms": round(tempos[-1], 1) if tempos else None,
                "perda_media": round(statistics.fmean(perdas), 1) if perdas else None,
                "com_perda": sum(1 for p in perdas if p > 0),
                "disp_24h": round(statistics.fmean(disp), 2) if disp else None,
            }
        )
    linhas.sort(key=lambda linha: (linha["id"] is None, linha["nome"]))

    def _gravidade(m: dict[str, Any]) -> tuple[float, float]:
        return (m.get("perda") or 0.0, m.get("ms") or 0.0)

    online = [m for m in medidas if m.get("up")]
    piores = [
        m for m in sorted(online, key=_gravidade, reverse=True) if (m.get("perda") or 0) > 0 or (m.get("ms") or 0) > 100
    ]
    return {"unidades": linhas, "piores": piores[:10]}


def faixas_fora_da_varredura(faixas: list[tuple[str, str]], ranges: list[str]) -> list[tuple[str, str]]:
    """Faixas de unidade que nenhuma discovery rule ativa do Zabbix varre, nem em parte.

    Uma faixa larga da unidade (ex.: /16) conta como coberta quando a regra
    varre ao menos uma sub-rede dela — normalmente só as sub-redes em uso.

    Args:
        faixas: Pares ``(nome da unidade, cidr)``.
        ranges: Faixas das drules ativas (CIDR; intervalos ``a.b.c.d-e`` são
            comparados pelo primeiro e último endereço).

    Returns:
        As faixas (com o nome da unidade) que precisam entrar na regra.
    """
    cobertos: list[tuple[int, int]] = []
    for r in ranges:
        try:
            if "-" in r:
                inicio, fim = r.split("-", 1)
                primeiro = ipaddress.ip_address(inicio.strip())
                ultimo_txt = fim.strip() if "." in fim else inicio.rsplit(".", 1)[0] + "." + fim.strip()
                cobertos.append((int(primeiro), int(ipaddress.ip_address(ultimo_txt))))
            else:
                rede = ipaddress.ip_network(r.strip(), strict=False)
                cobertos.append((int(rede.network_address), int(rede.broadcast_address)))
        except ValueError:
            continue
    fora = []
    for nome, cidr in faixas:
        try:
            rede = ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            continue
        a, b = int(rede.network_address), int(rede.broadcast_address)
        if not any(ini <= b and a <= fim for ini, fim in cobertos):
            fora.append((nome, str(rede)))
    return fora
