#!/usr/bin/env python3
"""
Collector de descoberta de ativos de infraestrutura de rede.

Fluxo:
  1. nmap varre os ranges configurados (INFRA_SCAN_RANGES)
  2. Cada host descoberto é classificado por OS + portas abertas
  3. Host inexistente no Zabbix → criado no grupo + template correto
  4. Resultado escrito no InfluxDB (gov_infra_assets) para o dashboard
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
"""

from __future__ import annotations

import argparse
import os
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

SCAN_RANGES = os.getenv("INFRA_SCAN_RANGES", "172.29.0.0/22").split(",")
SCAN_INTERVAL = int(os.getenv("INFRA_SCAN_INTERVAL", "3600"))
NMAP_ARGS = os.getenv("INFRA_NMAP_ARGS", "-T4 --host-timeout 10s")

# ── Zabbix group IDs ──────────────────────────────────────────────────────────
GRP_LINUX = "2"
GRP_SERVIDORES = "23"
GRP_FIREWALL = "22"
GRP_DATABASES = "20"
GRP_VMS = "6"
GRP_APPS = "19"
GRP_DISCOVERED = "5"

# ── Zabbix template IDs ───────────────────────────────────────────────────────
TMPL_LINUX_AGENT = "10001"  # Linux by Zabbix agent
TMPL_NETWORK_SNMP = "10226"  # Network Generic Device by SNMP
TMPL_WINDOWS_AGENT = "10081"  # Windows by Zabbix agent
TMPL_ICMP_PING = "10687"  # Template_CFTV_Ping (ICMP disponível nesta instância)
TMPL_MIKROTIK_SNMP = "10233"  # Mikrotik by SNMP
TMPL_CISCO_SNMP = "10218"  # Cisco IOS by SNMP

# Portas que identificam tipos de host
PORTS_ZABBIX_AGENT = {10050}
PORTS_SSH = {22}
PORTS_RDP = {3389}
PORTS_SNMP = {161}  # UDP
PORTS_DB = {3306, 5432, 1433, 27017, 6379}
PORTS_WEB = {80, 443, 8080, 8443}
PORTS_MGMT = {623, 664}  # IPMI


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
    args = f"{NMAP_ARGS} -sV -p 22,80,161,443,623,3306,3389,5432,1433,8080,8443,10050 --open"

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


def _existing_hosts(api: Any) -> dict[str, str]:
    """Retorna dict {ip: hostid} de todos os hosts com interface IP."""
    hosts = api.host.get(
        output=["hostid"],
        selectInterfaces=["ip", "interfaceid"],
    )
    result: dict[str, str] = {}
    for h in hosts:
        for iface in h.get("interfaces", []):
            ip = iface.get("ip", "")
            if ip:
                result[ip] = h["hostid"]
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


def _registrar_no_zabbix(host: DiscoveredHost, api: Any, existing: dict[str, str]) -> str:
    """Cria ou atualiza host no Zabbix. Retorna 'created'|'updated'|'skipped'."""
    from zabbix_utils.exceptions import APIRequestError

    host_tech = f"infra-{host.ip.replace('.', '-')}"
    host_name = host.hostname or host_tech
    iface_type = host.interface_type
    port = "10050" if iface_type == 1 else "161"

    groups = [{"groupid": g} for g in host.groups]
    templates = [{"templateid": t} for t in host.templates]
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

    existing_id = existing.get(host.ip)
    try:
        if existing_id:
            # Atualizar apenas grupos e templates (não mexer na interface)
            api.host.update(
                hostid=existing_id,
                groups=groups,
                templates=templates,
            )
            return "updated"
        else:
            api.host.create(
                host=host_tech,
                name=host_name,
                groups=groups,
                interfaces=[iface],
                templates=templates,
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


def run_scan(dry_run: bool = False) -> dict[str, int]:
    """Executa varredura completa e retorna estatísticas."""
    scan_start = datetime.now(UTC)
    log.info("network_discovery.inicio", ranges=SCAN_RANGES, dry_run=dry_run)

    # 1. nmap scan em todos os ranges
    all_hosts: list[DiscoveredHost] = []
    for ip_range in SCAN_RANGES:
        all_hosts.extend(_scan_range(ip_range))

    if not all_hosts:
        log.warning("network_discovery.nenhum_host_encontrado")
        return {"total": 0, "created": 0, "updated": 0, "skipped": 0}

    stats = {"total": len(all_hosts), "created": 0, "updated": 0, "skipped": 0}

    if not dry_run and ZABBIX_TOKEN:
        # 2. Sincronizar com Zabbix
        try:
            api = _zbx_api()
            existing = _existing_hosts(api)
            skip_ips = _skip_ips(api)

            for host in all_hosts:
                if host.ip in skip_ips:
                    log.debug("network_discovery.ip_ignorado", ip=host.ip, motivo="CFTV/M365")
                    stats["skipped"] += 1
                    continue
                result = _registrar_no_zabbix(host, api, existing)
                stats[result] = stats.get(result, 0) + 1
                log.info("network_discovery.zabbix_sync", ip=host.ip, resultado=result, categoria=host.category)
        except Exception as exc:
            log.error("network_discovery.zabbix_falhou", erro=str(exc))

        # 3. Escrever no InfluxDB
        _write_influx(all_hosts, scan_start)

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
        print(f"    {h.ip:<18} {h.category:<30} portas={ports}")
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
        log.info("network_discovery.inicio", ranges=SCAN_RANGES, dry_run=args.dry_run)
        all_hosts: list[DiscoveredHost] = []
        for ip_range in SCAN_RANGES:
            all_hosts.extend(_scan_range(ip_range))

        if args.report or args.dry_run:
            _print_report(all_hosts)

        if not args.dry_run and all_hosts:
            stats: dict[str, int] = {"total": len(all_hosts), "created": 0, "updated": 0, "skipped": 0}
            if ZABBIX_TOKEN:
                try:
                    api = _zbx_api()
                    existing = _existing_hosts(api)
                    skip_ips = _skip_ips(api)
                    for host in all_hosts:
                        if host.ip in skip_ips:
                            stats["skipped"] += 1
                            continue
                        result = _registrar_no_zabbix(host, api, existing)
                        stats[result] = stats.get(result, 0) + 1
                        log.info("network_discovery.zabbix_sync", ip=host.ip, resultado=result, categoria=host.category)
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
