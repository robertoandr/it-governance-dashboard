#!/usr/bin/env python3
"""
Cria/atualiza hosts de links WAN no Zabbix e registra no banco de dados da aplicação.

Uso:
    python3 infra-setup/02_create_link_hosts.py

Adicione novos links na lista LINKS abaixo e execute novamente (idempotente).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import urllib3

_env_vals: dict[str, str] = {}
_env_file = Path(__file__).resolve().parent.parent / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            _env_vals[k] = v
    for k, v in _env_vals.items():
        if k not in os.environ:
            os.environ[k] = v

ZABBIX_URL = os.environ.get("ZABBIX_URL", "http://172.29.2.11:8080/api_jsonrpc.php")
if "host.docker.internal" in ZABBIX_URL:
    ZABBIX_URL = "http://172.29.2.11:8080/api_jsonrpc.php"
ZABBIX_TOKEN = os.environ.get("ZABBIX_TOKEN", "")

os.environ.pop("ZABBIX_USER", None)
os.environ.pop("ZABBIX_PASSWORD", None)

urllib3.disable_warnings()

try:
    from zabbix_utils import ZabbixAPI
    from zabbix_utils.exceptions import APIRequestError
except ImportError:
    sys.exit("Instale: pip install zabbix-utils --break-system-packages")

# ── Template e grupo ──────────────────────────────────────────────────────────
TMPL_ICMP_PING = "10564"  # ICMP Ping (nativo Zabbix)
GRP_LINKS_NAME = "Links WAN"

# ── Links a registrar ─────────────────────────────────────────────────────────
# Edite aqui para adicionar novos links.
# 'host' deve ser único no Zabbix.
LINKS = [
    {
        "host": "link-wan-01",
        "name": "Link WAN 01 — 45.166.249.159",
        "ip": "45.166.249.159",
        "cidr": "45.166.249.159/32",
        "provider": "",
        "circuit_id": "",
        "link_type": "dedicado",
        "bandwidth_mbps": None,
    },
    {
        "host": "link-wan-02",
        "name": "Link WAN 02 — 189.112.100.49",
        "ip": "189.112.100.49",
        "cidr": "189.112.100.49/30",
        "provider": "",
        "circuit_id": "",
        "link_type": "dedicado",
        "bandwidth_mbps": None,
    },
]


def banner(text: str) -> None:
    print(f"\n{'=' * 60}\n  {text}\n{'=' * 60}")


def ok(msg: str) -> None:
    print(f"  [OK] {msg}")


def info(msg: str) -> None:
    print(f"  [--] {msg}")


def main() -> None:
    banner("Configuração de Links WAN no Zabbix")

    api = ZabbixAPI(url=ZABBIX_URL, token=ZABBIX_TOKEN, skip_version_check=True)
    print(f"  Zabbix {api.api_version()} conectado")

    # ── 1. Garantir grupo Links WAN ───────────────────────────────────────────
    banner("1. Grupo 'Links WAN'")
    existing_groups = {g["name"]: g["groupid"] for g in api.hostgroup.get(output=["groupid", "name"])}
    if GRP_LINKS_NAME not in existing_groups:
        result = api.hostgroup.create(name=GRP_LINKS_NAME)
        grp_id = result["groupids"][0]
        ok(f"Grupo criado: {GRP_LINKS_NAME} → ID {grp_id}")
    else:
        grp_id = existing_groups[GRP_LINKS_NAME]
        info(f"Grupo ja existe: {GRP_LINKS_NAME} (ID {grp_id})")

    # ── 2. Criar/atualizar hosts ──────────────────────────────────────────────
    banner("2. Hosts dos links")

    # hosts existentes por 'host' (technical name)
    existing_hosts = {h["host"]: h["hostid"] for h in api.host.get(output=["hostid", "host"])}

    results: list[dict] = []

    for link in LINKS:
        host_tech = link["host"]
        host_name = link["name"]
        ip = link["ip"]

        if host_tech in existing_hosts:
            hostid = existing_hosts[host_tech]
            try:
                api.host.update(
                    hostid=hostid,
                    groups=[{"groupid": grp_id}],
                    templates=[{"templateid": TMPL_ICMP_PING}],
                    name=host_name,
                    status=0,
                )
                ok(f"Host atualizado: {host_name} (ID {hostid})")
            except APIRequestError as e:
                print(f"  [AVISO] update {host_tech}: {e}")
        else:
            try:
                result = api.host.create(
                    host=host_tech,
                    name=host_name,
                    groups=[{"groupid": grp_id}],
                    interfaces=[
                        {
                            "type": "1",  # agent (necessario mesmo sem agente real)
                            "main": "1",
                            "useip": "1",
                            "ip": ip,
                            "dns": "",
                            "port": "10050",
                        }
                    ],
                    templates=[{"templateid": TMPL_ICMP_PING}],
                    description=f"Link WAN monitorado via ICMP. CIDR: {link['cidr']}",
                    status=0,
                )
                hostid = result["hostids"][0]
                ok(f"Host criado: {host_name} → hostid={hostid}")
            except APIRequestError as e:
                print(f"  [ERRO] create {host_tech}: {e}")
                hostid = None

        if hostid:
            results.append({**link, "zabbix_hostid": hostid})

    # ── 3. Registrar no banco de dados da aplicação ───────────────────────────
    banner("3. Registrando links no banco de dados")
    _registrar_no_db(results)

    banner("CONCLUIDO")
    print("  Links disponiveis em: http://localhost:5000/links\n")


def _registrar_no_db(links: list[dict]) -> None:
    """Registra (upsert) links no SQLite da aplicação via Flask app context."""
    project_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(project_root))

    try:
        from app import create_app
        from app.extensions import db
        from app.models.link import Link

        app = create_app()
        with app.app_context():
            db.create_all()
            for lnk in links:
                existing = Link.query.filter_by(ip=lnk["ip"]).first()
                if existing:
                    existing.zabbix_hostid = lnk.get("zabbix_hostid")
                    existing.name = lnk["name"]
                    info(f"DB atualizado: {lnk['ip']}")
                else:
                    obj = Link(
                        name=lnk["name"],
                        ip=lnk["ip"],
                        cidr=lnk["cidr"],
                        provider=lnk.get("provider", ""),
                        circuit_id=lnk.get("circuit_id", ""),
                        link_type=lnk.get("link_type", "dedicado"),
                        bandwidth_mbps=lnk.get("bandwidth_mbps"),
                        zabbix_hostid=lnk.get("zabbix_hostid"),
                        active=True,
                    )
                    db.session.add(obj)
                    ok(f"DB inserido: {lnk['ip']} — {lnk['name']}")
            db.session.commit()
    except Exception as exc:
        print(f"  [AVISO] Nao foi possivel registrar no banco: {exc}")
        print("  Execute manualmente via shell Flask ou acesse /links para adicionar via UI.")


if __name__ == "__main__":
    main()
