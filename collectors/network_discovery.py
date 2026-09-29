#!/usr/bin/env python3
"""
Collector de descoberta de ativos de infraestrutura de rede.

Fluxo:
  1. nmap varre INFRA_SCAN_RANGES + as faixas de IP das unidades cadastradas
     no dashboard (tabela ``unidades`` do app.db, montado só leitura)
  2. Cada host é classificado (categoria Zabbix) e recebe um tipo de ativo
     sugerido (vm, impressora, camera, servidor, ...) para revisão na página Rede
  3. Resultado escrito no InfluxDB (gov_infra_assets / gov_infra_asset_detail)
  4. Opcional (INFRA_ZABBIX_AUTOREGISTER=true): host inexistente no Zabbix é
     criado no grupo + template correto
  5. Pode rodar como cron/systemd ou via --scan-now

Classificação automática:
  Porta 10050 (Zabbix agent)  → Servidores / Linux servers   + Linux by Zabbix agent
  Porta 22 (SSH) sem agente   → Servidores / Linux servers   + template ICMP
  Porta 3389 (RDP)            → Servidores (Windows)         + template ICMP
  Porta 161 UDP (SNMP)        → Firewall / Rede              + Network Generic Device by SNMP
  Porta 3306/5432 (DB)        → Databases                    + template ICMP
  Porta 443/80 apenas         → Applications                 + template ICMP
  ICMP apenas                 → Infra/Descobertos            + template ICMP

Uso:
    python3 collectors/network_discovery.py             # aguarda cron interno
    python3 collectors/network_discovery.py --scan-now  # executa imediatamente e sai
    python3 collectors/network_discovery.py --dry-run   # scan sem escrever no Zabbix/InfluxDB

Variáveis de ambiente:
    ZABBIX_URL          URL da API Zabbix (obrigatório)
    ZABBIX_TOKEN        Token Bearer (obrigatório)
    INFLUX_URL          URL do InfluxDB (obrigatório para métricas)
    INFLUX_TOKEN        Token InfluxDB
    INFLUX_ORG          Org InfluxDB
    INFLUX_BUCKET_RAW   Bucket (padrão: governance_raw)
    INFRA_SCAN_RANGES   Ranges nmap separados por vírgula
                        (padrão: 172.29.0.0/22)
    INFRA_SCAN_INTERVAL Intervalo em segundos (padrão: 3600)
    INFRA_NMAP_ARGS     Args extras para nmap (padrão: -T4 --host-timeout 10s)
    INFRA_UNIDADES_DB   SQLite do app com a tabela unidades (padrão: /app-data/app.db)
    INFRA_ZABBIX_AUTOREGISTER  true para cadastrar hosts no Zabbix (padrão: false)
"""

from __future__ import annotations

import argparse
import ipaddress
import os
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)

# ── Carregar .env sem source ───────────────────────────────────────────────────
_env_file = Path(__file__).resolve().parent.parent / ".env"
if _env_file.exists():
    _env_vals: dict[str, str] = {}
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            _env_vals[_k.strip()] = _v.strip().strip('"').strip("'")
    for _k, _v in _env_vals.items():
        if _k not in os.environ:
            os.environ[_k] = _v

# ── Configuração ──────────────────────────────────────────────────────────────
_zbx_url_raw = os.getenv("ZABBIX_URL", "http://172.29.2.11:8080/api_jsonrpc.php")
ZABBIX_URL = "http://172.29.2.11:8080/api_jsonrpc.php" if "host.docker.internal" in _zbx_url_raw else _zbx_url_raw
ZABBIX_TOKEN = os.getenv("ZABBIX_TOKEN", "")
INFLUX_URL = os.getenv("INFLUX_URL", "").replace("localhost:8086", "localhost:18086")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN", "")
INFLUX_ORG = os.getenv("INFLUX_ORG", "")
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET_RAW", "governance_raw")

SCAN_RANGES = [r.strip() for r in os.getenv("INFRA_SCAN_RANGES", "172.29.0.0/22").split(",") if r.strip()]
UNIDADES_DB = os.getenv("INFRA_UNIDADES_DB", "/app-data/app.db")
ZABBIX_AUTOREGISTER = os.getenv("INFRA_ZABBIX_AUTOREGISTER", "false").strip().lower() in ("1", "true", "yes")
# Faixas maiores que isso são ignoradas: um /8 digitado por engano varreria 16M IPs
MENOR_PREFIXO = 16
SCAN_INTERVAL = int(os.getenv("INFRA_SCAN_INTERVAL", "3600"))
NMAP_ARGS = os.getenv("INFRA_NMAP_ARGS", "-T4 --host-timeout 15s")

# ── Zabbix hostgroups (por NOME) ──────────────────────────────────────────────
# IDs numéricos mudam a cada instalação: após a perda da VM o groupid 22 virou
# "Certificados SSL" e o 23 deixou de existir. _ZbxIds resolve os nomes em runtime.
GRP_LINUX = "Linux servers"
GRP_SERVIDORES = "Servidores"
GRP_FIREWALL = "Firewall"
GRP_DATABASES = "Databases"
GRP_VMS = "Virtual machines"
GRP_APPS = "Applications"
GRP_DISCOVERED = "Discovered hosts"

# ── Zabbix templates (por nome técnico) ───────────────────────────────────────
TMPL_LINUX_AGENT = "Linux by Zabbix agent"
TMPL_NETWORK_SNMP = "Network Generic Device by SNMP"
TMPL_WINDOWS_AGENT = "Windows by Zabbix agent"
TMPL_ICMP_PING = "ICMP Ping"
TMPL_MIKROTIK_SNMP = "Mikrotik by SNMP"
TMPL_CISCO_SNMP = "Cisco IOS by SNMP"

# Portas que identificam tipos de host
PORTS_ZABBIX_AGENT = {10050}
PORTS_SSH = {22}
PORTS_RDP = {3389}
PORTS_SNMP = {161}  # UDP
PORTS_DB = {3306, 5432, 1433, 27017, 6379}
PORTS_WEB = {80, 443, 8080, 8443}
PORTS_MGMT = {623, 664}  # IPMI
PORTS_PRINTER = {9100, 515, 631}  # RAW/JetDirect, LPD, IPP
PORTS_CAMERA = {554, 37777, 8000}  # RTSP, Intelbras/Dahua SDK, Hikvision SDK
PORTS_WINDOWS = {135, 139, 445}
PORTS_FORTINET = {541}

# Portas TCP varridas (as de classificação acima + serviços comuns)
SCAN_PORTS = sorted(
    {22, 80, 443, 623, 3306, 3389, 5432, 1433, 8080, 8443, 10050}
    | PORTS_PRINTER
    | PORTS_CAMERA
    | PORTS_WINDOWS
    | PORTS_FORTINET
)

# Prefixos de MAC (OUI) de placas virtuais
VM_OUIS = {
    "00:50:56": "VMware",
    "00:0C:29": "VMware",
    "00:05:69": "VMware",
    "00:15:5D": "Hyper-V",
    "BC:24:11": "Proxmox",
    "52:54:00": "QEMU/KVM",
    "08:00:27": "VirtualBox",
    "00:16:3E": "Xen",
}
_VENDORS_IMPRESSORA = (
    "hewlett",
    "hp inc",
    "brother",
    "epson",
    "ricoh",
    "kyocera",
    "lexmark",
    "xerox",
    "canon",
    "samsung electronics",
    "oki",
)
_VENDORS_CAMERA = ("intelbras", "hikvision", "dahua", "axis", "hanwha")
_VENDORS_AP = ("ubiquiti", "aruba", "ruckus", "cambium", "unifi")
_VENDORS_SWITCH = ("cisco", "mikrotik", "huawei", "juniper", "tp-link", "d-link", "3com", "extreme")

# Tipos de ativo sugeridos (espelham itgov.models.ativo.TIPOS_VALIDOS)
TIPO_VM = "vm"
TIPO_IMPRESSORA = "impressora"
TIPO_CAMERA = "camera"
TIPO_AP = "ap"
TIPO_FIREWALL = "firewall"
TIPO_SWITCH = "switch"
TIPO_SERVIDOR = "servidor"
TIPO_ENDPOINT = "endpoint"
TIPO_OUTRO = "outro"


@dataclass
class DiscoveredHost:
    ip: str
    hostname: str = ""
    mac: str = ""
    vendor: str = ""
    os_guess: str = ""
    open_tcp: set[int] = field(default_factory=set)
    open_udp: set[int] = field(default_factory=set)
    state: str = "up"

    # Classificação calculada
    category: str = "Outros"
    tipo_sugerido: str = TIPO_OUTRO
    motivo: str = ""
    groups: list[str] = field(default_factory=list)
    templates: list[str] = field(default_factory=list)
    interface_type: int = 1  # 1=agent, 2=SNMP, 3=IPMI

    def classify(self) -> None:
        """Classifica o host pelo perfil de portas e OS detectado."""
        has_agent = bool(self.open_tcp & PORTS_ZABBIX_AGENT)
        has_ssh = bool(self.open_tcp & PORTS_SSH)
        has_rdp = bool(self.open_tcp & PORTS_RDP)
        has_snmp = bool(self.open_udp & PORTS_SNMP) or 161 in self.open_tcp
        has_db = bool(self.open_tcp & PORTS_DB)
        has_web = bool(self.open_tcp & PORTS_WEB)
        os_lower = self.os_guess.lower()

        if has_agent:
            if "windows" in os_lower:
                self.category = "Servidor Windows"
                self.groups = [GRP_SERVIDORES]
                self.templates = [TMPL_LINUX_AGENT]  # usar agente mesmo no Windows por ora
            else:
                self.category = "Servidor Linux"
                self.groups = [GRP_LINUX, GRP_SERVIDORES]
                self.templates = [TMPL_LINUX_AGENT]
            self.interface_type = 1

        elif has_snmp:
            vendor_l = self.vendor.lower()
            if "cisco" in vendor_l or "cisco" in os_lower:
                self.category = "Switch/Router Cisco"
                self.templates = [TMPL_CISCO_SNMP]
            elif "mikrotik" in vendor_l or "mikrotik" in os_lower:
                self.category = "Router MikroTik"
                self.templates = [TMPL_MIKROTIK_SNMP]
            else:
                self.category = "Dispositivo de Rede"
                self.templates = [TMPL_NETWORK_SNMP]
            self.groups = [GRP_FIREWALL]
            self.interface_type = 2  # SNMP

        elif has_rdp:
            self.category = "Servidor Windows"
            self.groups = [GRP_SERVIDORES]
            self.templates = [TMPL_ICMP_PING]
            self.interface_type = 1

        elif has_ssh and not has_agent:
            self.category = "Servidor Linux (sem agente)"
            self.groups = [GRP_LINUX, GRP_SERVIDORES]
            self.templates = [TMPL_ICMP_PING]
            self.interface_type = 1

        elif has_db:
            self.category = "Banco de Dados"
            self.groups = [GRP_DATABASES]
            self.templates = [TMPL_ICMP_PING]

        elif has_web and not has_ssh:
            self.category = "Aplicacao Web"
            self.groups = [GRP_APPS]
            self.templates = [TMPL_ICMP_PING]

        else:
            self.category = "Host Generico"
            self.groups = [GRP_DISCOVERED]
            self.templates = [TMPL_ICMP_PING]

        self.tipo_sugerido, self.motivo = sugerir_tipo(self)


def sugerir_tipo(host: DiscoveredHost) -> tuple[str, str]:
    """Sugere o tipo de ativo pelo MAC, fabricante, portas e SO detectado.

    A ordem importa: sinais mais específicos (placa virtual, porta de
    impressão, RTSP) vêm antes dos genéricos (SSH/RDP).

    Returns:
        ``(tipo, motivo)`` — o motivo é exibido na página Rede para o usuário
        entender e corrigir a sugestão.
    """
    tcp = host.open_tcp
    vendor = host.vendor.lower()
    os_l = host.os_guess.lower()
    has_snmp = bool(host.open_udp & PORTS_SNMP) or 161 in tcp

    oui = host.mac.upper()[:8]
    if oui in VM_OUIS:
        return TIPO_VM, f"MAC {VM_OUIS[oui]}"
    if any(v in vendor for v in ("vmware", "qemu", "xensource", "proxmox")):
        return TIPO_VM, f"fabricante {host.vendor}"
    marca_impressora = any(v in vendor for v in _VENDORS_IMPRESSORA)
    if tcp & PORTS_PRINTER or (marca_impressora and not tcp & (PORTS_SSH | PORTS_RDP)):
        portas = sorted(tcp & PORTS_PRINTER)
        return TIPO_IMPRESSORA, f"portas {portas}" if portas else f"fabricante {host.vendor}"
    if tcp & PORTS_CAMERA or any(v in vendor for v in _VENDORS_CAMERA):
        portas = sorted(tcp & PORTS_CAMERA)
        return TIPO_CAMERA, f"portas {portas}" if portas else f"fabricante {host.vendor}"
    if tcp & PORTS_FORTINET or "fortinet" in vendor or "fortios" in os_l:
        return TIPO_FIREWALL, "Fortinet"
    if any(v in vendor for v in _VENDORS_AP):
        return TIPO_AP, f"fabricante {host.vendor}"
    if has_snmp and (
        any(v in vendor for v in _VENDORS_SWITCH) or any(v in os_l for v in ("ios", "routeros", "switch"))
    ):
        return TIPO_SWITCH, "SNMP + fabricante de rede"
    if "windows" in os_l and "server" not in os_l and not tcp & PORTS_RDP and tcp & PORTS_WINDOWS:
        return TIPO_ENDPOINT, host.os_guess
    if tcp & (PORTS_ZABBIX_AGENT | PORTS_SSH | PORTS_RDP | PORTS_DB):
        return TIPO_SERVIDOR, "agente/SSH/RDP/banco"
    if tcp & PORTS_WINDOWS:
        return TIPO_ENDPOINT, "compartilhamento Windows"
    if has_snmp:
        return TIPO_SWITCH, "SNMP"
    return TIPO_OUTRO, "sem assinatura conhecida"


def faixas_das_unidades(db_path: str = UNIDADES_DB) -> list[str]:
    """Lê as faixas de IP das unidades ativas do app.db do dashboard.

    Ausência do arquivo/tabela não é erro: o scanner segue só com
    INFRA_SCAN_RANGES (ex.: antes do primeiro deploy do cadastro de unidades).
    """
    if not Path(db_path).exists():
        log.info("network_discovery.unidades_db_ausente", caminho=db_path)
        return []
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            linhas = conn.execute("SELECT faixas_ip FROM unidades WHERE ativo = 1").fetchall()
    except sqlite3.Error as exc:
        log.warning("network_discovery.unidades_db_erro", caminho=db_path, erro=str(exc))
        return []
    return [f for (texto,) in linhas for f in (texto or "").split()]


def faixas_para_varrer(extras: list[str]) -> list[str]:
    """Une INFRA_SCAN_RANGES e ``extras``, normaliza e descarta faixas inválidas ou grandes demais."""
    resultado: list[str] = []
    for bruto in [*SCAN_RANGES, *extras]:
        try:
            rede = ipaddress.ip_network(bruto.strip(), strict=False)
        except ValueError:
            log.warning("network_discovery.faixa_invalida", faixa=bruto)
            continue
        if rede.prefixlen < MENOR_PREFIXO:
            log.warning("network_discovery.faixa_grande_demais", faixa=str(rede), minimo=f"/{MENOR_PREFIXO}")
            continue
        if str(rede) not in resultado:
            resultado.append(str(rede))
    return resultado


# ── nmap scanner ──────────────────────────────────────────────────────────────


def _scan_range(ip_range: str) -> list[DiscoveredHost]:
    """Executa nmap e retorna lista de hosts descobertos."""
    try:
        import nmap  # type: ignore[import-untyped]
    except ImportError:
        log.error("network_discovery.nmap_nao_instalado", dica="pip install python-nmap --break-system-packages")
        return []

    nm = nmap.PortScanner()
    # Scan: portas TCP chave + UDP 161 (SNMP) + OS detection
    args = f"{NMAP_ARGS} -p {','.join(str(p) for p in SCAN_PORTS)} --open"

    log.info("network_discovery.scan_inicio", range=ip_range, args=args)
    try:
        nm.scan(hosts=ip_range.strip(), arguments=args)
    except Exception as exc:
        log.warning("network_discovery.nmap_falhou", range=ip_range, erro=str(exc))
        return []

    hosts: list[DiscoveredHost] = []
    for ip in nm.all_hosts():
        h = nm[ip]
        if h.state() != "up":
            continue

        host = DiscoveredHost(ip=ip)

        # Hostname
        hostnames = h.hostnames()
        if hostnames:
            host.hostname = hostnames[0].get("name", "")

        # MAC e vendor
        if "mac" in h.get("addresses", {}):
            host.mac = h["addresses"]["mac"]
        if "vendor" in h and host.mac in h["vendor"]:
            host.vendor = h["vendor"][host.mac]

        # OS guess
        os_matches = h.get("osmatch", [])
        if os_matches:
            host.os_guess = os_matches[0].get("name", "")

        # Portas TCP abertas
        for proto in ("tcp", "udp"):
            if proto in h:
                for port, info in h[proto].items():
                    if info.get("state") == "open":
                        if proto == "tcp":
                            host.open_tcp.add(port)
                        else:
                            host.open_udp.add(port)

        host.classify()
        hosts.append(host)
        log.info(
            "network_discovery.host_descoberto", ip=ip, categoria=host.category, portas_tcp=sorted(host.open_tcp)[:8]
        )

    return hosts


# ── Zabbix integration ────────────────────────────────────────────────────────


def _zbx_api() -> Any:
    """Retorna instância da API Zabbix."""
    import urllib3

    urllib3.disable_warnings()
    from zabbix_utils import ZabbixAPI

    # zabbix_utils lê ZABBIX_USER/ZABBIX_PASSWORD do env e conflita com token
    _saved_user = os.environ.pop("ZABBIX_USER", None)
    _saved_pass = os.environ.pop("ZABBIX_PASSWORD", None)
    try:
        api = ZabbixAPI(url=ZABBIX_URL, token=ZABBIX_TOKEN, skip_version_check=True)
    finally:
        if _saved_user is not None:
            os.environ["ZABBIX_USER"] = _saved_user
        if _saved_pass is not None:
            os.environ["ZABBIX_PASSWORD"] = _saved_pass
    return api


class _ZbxIds:
    """Resolve nomes de hostgroups/templates para IDs do Zabbix atual (com cache)."""

    def __init__(self, api: Any) -> None:
        self._api = api
        self._grupos: dict[str, str] = {}
        self._templates: dict[str, str | None] = {}

    def grupo(self, nome: str) -> str:
        """Retorna o groupid de ``nome``, criando o hostgroup se não existir."""
        if nome not in self._grupos:
            achados = self._api.hostgroup.get(output=["groupid"], filter={"name": nome})
            if achados:
                self._grupos[nome] = achados[0]["groupid"]
            else:
                self._grupos[nome] = self._api.hostgroup.create(name=nome)["groupids"][0]
                log.info("network_discovery.grupo_criado", grupo=nome)
        return self._grupos[nome]

    def template(self, nome: str) -> str | None:
        """Retorna o templateid de ``nome`` ou None (template ausente é ignorado com aviso)."""
        if nome not in self._templates:
            achados = self._api.template.get(output=["templateid"], filter={"host": nome})
            self._templates[nome] = achados[0]["templateid"] if achados else None
            if not achados:
                log.warning("network_discovery.template_ausente", template=nome)
        return self._templates[nome]


def _existing_hosts(api: Any) -> dict[str, dict[str, Any]]:
    """Retorna {ip: {hostid, groupids, templateids}} de todos os hosts com interface IP."""
    hosts = api.host.get(
        output=["hostid"],
        selectInterfaces=["ip", "interfaceid"],
        selectHostGroups=["groupid"],
        selectParentTemplates=["templateid"],
    )
    result: dict[str, dict[str, Any]] = {}
    for h in hosts:
        info = {
            "hostid": h["hostid"],
            "groupids": {g["groupid"] for g in h.get("hostgroups", [])},
            "templateids": {t["templateid"] for t in h.get("parentTemplates", [])},
        }
        for iface in h.get("interfaces", []):
            ip = iface.get("ip", "")
            if ip:
                result[ip] = info
    return result


def _skip_ips(api: Any) -> set[str]:
    """IPs a ignorar: câmeras CFTV e hosts M365 (têm páginas próprias)."""
    cftv_gids = [
        g["groupid"]
        for g in api.hostgroup.get(output=["groupid", "name"])
        if g["name"].startswith("CFTV") or g["name"] == "Microsoft 365"
    ]
    if not cftv_gids:
        return set()
    hosts = api.host.get(
        output=["hostid"],
        groupids=cftv_gids,
        selectInterfaces=["ip"],
    )
    skip: set[str] = set()
    for h in hosts:
        for iface in h.get("interfaces", []):
            ip = iface.get("ip", "")
            if ip:
                skip.add(ip)
    return skip


def _registrar_no_zabbix(host: DiscoveredHost, api: Any, existing: dict[str, dict[str, Any]], ids: _ZbxIds) -> str:
    """Cria ou atualiza host no Zabbix. Retorna 'created'|'updated'|'skipped'.

    Hosts existentes só GANHAM grupos/templates: os que já estavam vinculados
    (inclusive os configurados à mão) são preservados.
    """
    from zabbix_utils.exceptions import APIRequestError

    host_tech = f"infra-{host.ip.replace('.', '-')}"
    host_name = host.hostname or host_tech
    iface_type = host.interface_type
    port = "10050" if iface_type == 1 else "161"

    try:
        # Resolve (e pode criar) grupos via API: falha aqui fica restrita a este host
        groupids = {ids.grupo(g) for g in host.groups}
        templateids = {t for t in (ids.template(n) for n in host.templates) if t}
    except APIRequestError as exc:
        log.warning("network_discovery.zabbix_ids_erro", ip=host.ip, erro=str(exc))
        return "skipped"
    # Zabbix rejeita DNS com chars inválidos (ex: "_gateway"); useip=1 então DNS fica vazio
    safe_dns = host.hostname if host.hostname and host.hostname[0].isalnum() else ""
    iface = {
        "type": str(iface_type),
        "main": "1",
        "useip": "1",
        "ip": host.ip,
        "dns": safe_dns,
        "port": port,
    }

    atual = existing.get(host.ip)
    try:
        if atual:
            novos_grupos = groupids - atual["groupids"]
            novos_templates = templateids - atual["templateids"]
            if not (novos_grupos or novos_templates):
                return "skipped"
            # Soma aos vínculos atuais; interface não é alterada
            api.host.update(
                hostid=atual["hostid"],
                groups=[{"groupid": g} for g in sorted(atual["groupids"] | groupids)],
                templates=[{"templateid": t} for t in sorted(atual["templateids"] | templateids)],
            )
            return "updated"
        else:
            api.host.create(
                host=host_tech,
                name=host_name,
                groups=[{"groupid": g} for g in sorted(groupids)],
                interfaces=[iface],
                templates=[{"templateid": t} for t in sorted(templateids)],
                description=f"Descoberto por network_discovery em {datetime.now(UTC).strftime('%Y-%m-%d')}. OS: {host.os_guess}. Portas: {sorted(host.open_tcp)}",
                inventory_mode=1,  # automático
                inventory={"vendor": host.vendor, "os": host.os_guess},
            )
            return "created"
    except APIRequestError as exc:
        log.warning("network_discovery.zabbix_erro", ip=host.ip, erro=str(exc))
        return "skipped"


# ── InfluxDB integration ──────────────────────────────────────────────────────


def _write_influx(hosts: list[DiscoveredHost], scan_ts: datetime) -> None:
    """Escreve resumo da varredura no InfluxDB."""
    if not (INFLUX_URL and INFLUX_TOKEN and INFLUX_ORG):
        log.debug("network_discovery.influx_nao_configurado")
        return

    try:
        from influxdb_client import InfluxDBClient, Point, WritePrecision
        from influxdb_client.client.write_api import SYNCHRONOUS
    except ImportError:
        log.warning("network_discovery.influxdb_client_faltando")
        return

    client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
    write_api = client.write_api(write_options=SYNCHRONOUS)

    from collections import Counter

    cat_counts = Counter(h.category for h in hosts)
    total = len(hosts)

    # Ponto de resumo da varredura
    summary = (
        Point("gov_infra_assets")
        .tag("scan", "summary")
        .field("total_descobertos", total)
        .field(
            "servidores_linux", cat_counts.get("Servidor Linux", 0) + cat_counts.get("Servidor Linux (sem agente)", 0)
        )
        .field("servidores_windows", cat_counts.get("Servidor Windows", 0))
        .field(
            "dispositivos_rede",
            cat_counts.get("Dispositivo de Rede", 0)
            + cat_counts.get("Switch/Router Cisco", 0)
            + cat_counts.get("Router MikroTik", 0),
        )
        .field("bancos_de_dados", cat_counts.get("Banco de Dados", 0))
        .field("aplicacoes_web", cat_counts.get("Aplicacao Web", 0))
        .field("hosts_genericos", cat_counts.get("Host Generico", 0))
        .time(scan_ts, WritePrecision.S)
    )
    points = [summary]

    # Ponto por host descoberto (tag = ip)
    for h in hosts:
        p = (
            Point("gov_infra_asset_detail")
            .tag("ip", h.ip)
            .tag("category", h.category)
            .tag("os_guess", h.os_guess[:64] if h.os_guess else "unknown")
            .field("hostname", h.hostname or h.ip)
            .field("vendor", h.vendor or "")
            .field("mac", h.mac or "")
            .field("tipo_sugerido", h.tipo_sugerido)
            .field("motivo", h.motivo)
            .field("open_ports", ",".join(str(p) for p in sorted(h.open_tcp)))
            .field("open_ports_count", len(h.open_tcp))
            .field("has_agent", int(10050 in h.open_tcp))
            .field("has_snmp", int(bool(h.open_udp & PORTS_SNMP)))
            .field("has_ssh", int(22 in h.open_tcp))
            .time(scan_ts, WritePrecision.S)
        )
        points.append(p)

    try:
        write_api.write(bucket=INFLUX_BUCKET, record=points)
        log.info("network_discovery.influx_escrito", pontos=len(points))
    except Exception as exc:
        log.warning("network_discovery.influx_erro", erro=str(exc))
    finally:
        client.close()


# ── Pipeline principal ────────────────────────────────────────────────────────


def _sync_zabbix(all_hosts: list[DiscoveredHost], stats: dict[str, int]) -> None:
    """Registra no Zabbix os hosts descobertos, ignorando os de CFTV/M365."""
    api = _zbx_api()
    ids = _ZbxIds(api)
    existing = _existing_hosts(api)
    skip_ips = _skip_ips(api)
    for host in all_hosts:
        if host.ip in skip_ips:
            log.debug("network_discovery.ip_ignorado", ip=host.ip, motivo="CFTV/M365")
            stats["skipped"] += 1
            continue
        result = _registrar_no_zabbix(host, api, existing, ids)
        stats[result] = stats.get(result, 0) + 1
        log.info("network_discovery.zabbix_sync", ip=host.ip, resultado=result, categoria=host.category)


def run_scan(dry_run: bool = False) -> dict[str, int]:
    """Executa varredura completa e retorna estatísticas."""
    scan_start = datetime.now(UTC)
    ranges = faixas_para_varrer(faixas_das_unidades())
    log.info("network_discovery.inicio", ranges=ranges, dry_run=dry_run, zabbix_autoregister=ZABBIX_AUTOREGISTER)

    # 1. nmap scan em todos os ranges
    all_hosts: list[DiscoveredHost] = []
    for ip_range in ranges:
        all_hosts.extend(_scan_range(ip_range))

    if not all_hosts:
        log.warning("network_discovery.nenhum_host_encontrado")
        return {"total": 0, "created": 0, "updated": 0, "skipped": 0}

    stats = {"total": len(all_hosts), "created": 0, "updated": 0, "skipped": 0}

    if not dry_run:
        # 2. Escrever no InfluxDB (fonte da página Rede)
        _write_influx(all_hosts, scan_start)

        # 3. Cadastro automático no Zabbix — opt-in; o fluxo padrão é revisar na página Rede
        if ZABBIX_AUTOREGISTER and ZABBIX_TOKEN:
            try:
                _sync_zabbix(all_hosts, stats)
            except Exception as exc:
                log.error("network_discovery.zabbix_falhou", erro=str(exc))

    log.info(
        "network_discovery.concluido", **stats, duração_s=round((datetime.now(UTC) - scan_start).total_seconds(), 1)
    )
    return stats


# ── Relatório de consola ──────────────────────────────────────────────────────


def _print_report(hosts: list[DiscoveredHost]) -> None:
    from collections import Counter

    cats = Counter(h.category for h in hosts)
    print(f"\n{'=' * 60}")
    print(f"  RELATÓRIO — {len(hosts)} hosts descobertos")
    print(f"{'=' * 60}")
    for cat, count in sorted(cats.items(), key=lambda x: -x[1]):
        print(f"  {cat:<30} {count:>4}")
    print("\n  Detalhes:")
    for h in sorted(hosts, key=lambda x: (x.category, x.ip)):
        ports = sorted(h.open_tcp)[:6]
        print(f"    {h.ip:<18} {h.category:<30} {h.tipo_sugerido:<11} portas={ports}")
    print()


# ── Entry point ───────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Network Discovery Collector")
    parser.add_argument("--scan-now", action="store_true", help="Executar imediatamente e sair")
    parser.add_argument("--dry-run", action="store_true", help="Scan sem gravar no Zabbix/InfluxDB")
    parser.add_argument("--report", action="store_true", help="Imprimir relatório no terminal")
    args = parser.parse_args()

    if args.scan_now or args.dry_run:
        # Modo único: scan uma vez, relatório + gravação usam os mesmos hosts
        scan_start = datetime.now(UTC)
        ranges = faixas_para_varrer(faixas_das_unidades())
        log.info("network_discovery.inicio", ranges=ranges, dry_run=args.dry_run)
        all_hosts: list[DiscoveredHost] = []
        for ip_range in ranges:
            all_hosts.extend(_scan_range(ip_range))

        if args.report or args.dry_run:
            _print_report(all_hosts)

        if not args.dry_run and all_hosts:
            stats: dict[str, int] = {"total": len(all_hosts), "created": 0, "updated": 0, "skipped": 0}
            if ZABBIX_AUTOREGISTER and ZABBIX_TOKEN:
                try:
                    _sync_zabbix(all_hosts, stats)
                except Exception as exc:
                    log.error("network_discovery.zabbix_falhou", erro=str(exc))
            _write_influx(all_hosts, scan_start)
            print(f"\n  Resultado: {stats}")
        sys.exit(0)

    # Modo contínuo (daemon)
    log.info("network_discovery.daemon_inicio", intervalo_s=SCAN_INTERVAL)
    while True:
        try:
            run_scan()
        except Exception as exc:
            log.error("network_discovery.erro_inesperado", erro=str(exc))
        log.info("network_discovery.aguardando", proxima_varredura_s=SCAN_INTERVAL)
        time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    main()
