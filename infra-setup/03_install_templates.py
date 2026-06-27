#!/usr/bin/env python3
"""
Instala templates e media types ausentes no Zabbix 7.0.

Fontes:
  - Oficial:    https://github.com/zabbix/zabbix (release/7.0)
  - Community:  https://github.com/zabbix/community-templates (main)

Uso:
    python3 infra-setup/03_install_templates.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import requests
import urllib3

urllib3.disable_warnings()

# ── .env ──────────────────────────────────────────────────────────────────────
_env_vals: dict[str, str] = {}
_env_file = Path(__file__).resolve().parent.parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            _env_vals[_k.strip()] = _v.strip().strip('"').strip("'")
    for _k, _v in _env_vals.items():
        if _k not in os.environ:
            os.environ[_k] = _v

ZABBIX_URL = os.environ.get("ZABBIX_URL", "http://172.29.2.11:8080/api_jsonrpc.php")
if "host.docker.internal" in ZABBIX_URL:
    ZABBIX_URL = "http://172.29.2.11:8080/api_jsonrpc.php"
if not ZABBIX_URL.endswith("/api_jsonrpc.php"):
    ZABBIX_URL = ZABBIX_URL.rstrip("/") + "/api_jsonrpc.php"
ZABBIX_TOKEN = os.environ.get("ZABBIX_TOKEN", "")

os.environ.pop("ZABBIX_USER", None)
os.environ.pop("ZABBIX_PASSWORD", None)

try:
    from zabbix_utils import ZabbixAPI
    from zabbix_utils.exceptions import APIRequestError
except ImportError:
    sys.exit("Instale: pip install zabbix-utils --break-system-packages")

# ── Constantes de URL ─────────────────────────────────────────────────────────
RAW_OFFICIAL = "https://raw.githubusercontent.com/zabbix/zabbix/refs/heads/release/7.0/templates"
RAW_COMMUNITY = "https://raw.githubusercontent.com/zabbix/community-templates/refs/heads/main"
API_COMMUNITY = "https://api.github.com/repos/zabbix/community-templates/contents"

# ── Mapa de templates a instalar ──────────────────────────────────────────────
# Formato: (descricao, url_do_yaml)
RAW_COMM = "https://raw.githubusercontent.com/zabbix/community-templates/refs/heads/main"

TEMPLATES_TO_IMPORT: list[tuple[str, str]] = [
    # ── Active Directory ──────────────────────────────────────────────────────
    (
        "Active Directory — AD DS Health",
        f"{RAW_COMM}/Operating_Systems/Windows/template_ad_ds_health_and_performance/6.0/template_ad_ds_health_and_performance.yaml",
    ),
    # ── Hyper-V (versao 7.4 disponivel) ──────────────────────────────────────
    (
        "Hyper-V Replication by Zabbix agent",
        f"{RAW_COMM}/Virtualization/Hyper-V/template_hyper-v_replication_by_zabbix_agent/7.4/template_hyper-v_replication_by_zabbix_agent.yaml",
    ),
    # ── Impressoras ───────────────────────────────────────────────────────────
    (
        "Brother — Monochrome Printers by SNMP",
        f"{RAW_COMM}/Printers/Brother/template_brother_monochrome_printers/6.0/template_brother_monochrome_printers.yaml",
    ),
    (
        "Lexmark — Generic Laser Printer Color by SNMP",
        f"{RAW_COMM}/Printers/Lexmark/template_lexmark_generic_laser_printer-color/6.0/template_lexmark_generic_laser_printer-color.yaml",
    ),
    (
        "Ricoh — SNMP Printers",
        f"{RAW_COMM}/Printers/Ricoh/template_ricoh_snmp_printers/6.0/template_ricoh_snmp_printers.yaml",
    ),
]

# ── Media types a instalar (Teams, Slack, Telegram) ──────────────────────────
MEDIA_TYPES_TO_IMPORT: list[tuple[str, str]] = [
    (
        "Microsoft Teams",
        f"{RAW_OFFICIAL}/media/msteams/media_msteams.yaml",
    ),
    (
        "Slack",
        f"{RAW_OFFICIAL}/media/slack/media_slack.yaml",
    ),
    (
        "Telegram",
        f"{RAW_OFFICIAL}/media/telegram/media_telegram.yaml",
    ),
]

# ── Templates de serviços de rede (criados programaticamente via API) ─────────
# Zabbix simple checks: tipo 3, sem interfaceid
NETWORK_SERVICE_TEMPLATES: list[dict] = [
    {"name": "Service DNS", "key": "net.dns.record[,{HOST.CONN},A,1,1]", "descr": "DNS resolution check"},
    {"name": "Service FTP", "key": "net.tcp.service[ftp,{HOST.CONN},21]", "descr": "FTP port 21 check"},
    {"name": "Service HTTPS", "key": "net.tcp.service[https,{HOST.CONN},443]", "descr": "HTTPS port 443 check"},
    {"name": "Service IMAP", "key": "net.tcp.service[imap,{HOST.CONN},143]", "descr": "IMAP port 143 check"},
    {"name": "Service LDAP", "key": "net.tcp.service[ldap,{HOST.CONN},389]", "descr": "LDAP port 389 check"},
    {"name": "Service NTP", "key": "net.tcp.service[ntp,{HOST.CONN},123]", "descr": "NTP port 123 check"},
    {"name": "Service SMTP", "key": "net.tcp.service[smtp,{HOST.CONN},25]", "descr": "SMTP port 25 check"},
    {"name": "Service SSH", "key": "net.tcp.service[ssh,{HOST.CONN},22]", "descr": "SSH port 22 check"},
    {"name": "Service TCP", "key": "net.tcp.service.perf[{HOST.CONN},{$TCP.PORT}]", "descr": "TCP generic port check"},
    {"name": "Service DHCP", "key": "net.tcp.service[tcp,{HOST.CONN},67]", "descr": "DHCP port 67 check"},
]


def banner(text: str) -> None:
    print(f"\n{'=' * 60}\n  {text}\n{'=' * 60}")


def ok(msg: str) -> None:
    print(f"  [OK] {msg}")


def info(msg: str) -> None:
    print(f"  [--] {msg}")


def warn(msg: str) -> None:
    print(f"  [!!] {msg}")


def _fetch_yaml(url: str, descr: str) -> str | None:
    """Baixa um arquivo YAML de template. Retorna o conteúdo ou None."""
    try:
        resp = requests.get(url, timeout=30)
        if resp.status_code == 200:
            return resp.text
        warn(f"{descr}: HTTP {resp.status_code} em {url}")
        return None
    except Exception as exc:
        warn(f"{descr}: erro ao baixar — {exc}")
        return None


def _import_yaml(api: ZabbixAPI, yaml_content: str, descr: str) -> bool:
    """Importa YAML via configuration.import. Retorna True se OK."""
    try:
        api.configuration.import_(
            format="yaml",
            source=yaml_content,
            rules={
                "templates": {"createMissing": True, "updateExisting": True},
                "items": {"createMissing": True, "updateExisting": True},
                "triggers": {"createMissing": True, "updateExisting": True},
                "graphs": {"createMissing": True, "updateExisting": True},
                "valueMaps": {"createMissing": True, "updateExisting": True},
                "discoveryRules": {"createMissing": True, "updateExisting": True},
                "mediaTypes": {"createMissing": True, "updateExisting": True},
                "httptests": {"createMissing": True, "updateExisting": True},
            },
        )
        ok(descr)
        return True
    except APIRequestError as exc:
        warn(f"{descr}: {exc}")
        return False


def _existing_templates(api: ZabbixAPI) -> set[str]:
    return {t["name"] for t in api.template.get(output=["name"])}


def _existing_media_types(api: ZabbixAPI) -> set[str]:
    return {m["name"] for m in api.mediatype.get(output=["name"])}


def _create_service_template(api: ZabbixAPI, svc: dict, existing: set[str]) -> str:
    """Cria template de simple check para serviço de rede."""
    tname = svc["name"]
    if tname in existing:
        return "exists"

    try:
        result = api.template.create(
            host=tname,
            name=tname,
            groups=[{"groupid": "1"}],  # Templates group
            description=svc["descr"],
        )
        tid = result["templateids"][0]

        api.item.create(
            name=f"{tname} — disponibilidade",
            key_=svc["key"],
            hostid=tid,
            type=3,  # Simple check
            value_type=3,  # Unsigned int
            delay="60s",
            history="30d",
            trends="365d",
            units="",
            description=svc["descr"],
        )

        api.trigger.create(
            description=f"{tname} indisponível em {{{{HOST.NAME}}}}",
            expression=f"last(/{tname}/{svc['key']})=0",
            priority=3,  # Average
            manual_close=1,
        )
        return "created"
    except APIRequestError as exc:
        warn(f"{tname}: {exc}")
        return "error"


def main() -> None:
    banner("Instalação de Templates Zabbix 7.0")
    api = ZabbixAPI(url=ZABBIX_URL, token=ZABBIX_TOKEN, skip_version_check=True)
    print(f"  Zabbix {api.api_version()} conectado")

    existing_t = _existing_templates(api)
    existing_m = _existing_media_types(api)

    # ── 1. Templates via import YAML ──────────────────────────────────────────
    banner("1. Templates — download e importacao")
    stats = {"ok": 0, "skip": 0, "error": 0}
    for descr, url in TEMPLATES_TO_IMPORT:
        yaml_content = _fetch_yaml(url, descr)
        if not yaml_content:
            stats["error"] += 1
            continue
        if _import_yaml(api, yaml_content, descr):
            stats["ok"] += 1
        else:
            stats["error"] += 1
        time.sleep(0.3)

    print(f"\n  Templates importados: {stats['ok']}  erros: {stats['error']}")

    # ── 2. Media Types (Teams, Slack, Telegram) ───────────────────────────────
    banner("2. Media Types — Teams, Slack, Telegram")
    for descr, url in MEDIA_TYPES_TO_IMPORT:
        if descr in existing_m:
            info(f"{descr}: ja existe")
            continue
        yaml_content = _fetch_yaml(url, descr)
        if yaml_content and _import_yaml(api, yaml_content, descr):
            pass
        time.sleep(0.3)

    # ── 3. Templates de serviços de rede (simple checks) ─────────────────────
    banner("3. Templates de Servicos de Rede (Simple Check)")
    existing_t = _existing_templates(api)
    svc_stats = {"created": 0, "exists": 0, "error": 0}
    for svc in NETWORK_SERVICE_TEMPLATES:
        result = _create_service_template(api, svc, existing_t)
        svc_stats[result] = svc_stats.get(result, 0) + 1
        if result == "created":
            ok(f"{svc['name']} criado")
        elif result == "exists":
            info(f"{svc['name']}: ja existe")

    print(f"\n  Criados: {svc_stats['created']}  ja existiam: {svc_stats['exists']}  erros: {svc_stats['error']}")

    # ── 4. Resumo final ───────────────────────────────────────────────────────
    banner("RESUMO")
    total = api.template.get(countOutput=True)
    print(f"  Total de templates no Zabbix: {total}")
    print("\n  Templates nao cobertos por falta de template oficial:")
    print("    - Android / iOS: sem template oficial Zabbix 7.0 (monitoramento via MDM)")
    print("    - Cloud Foundry: descontinuado no Zabbix 7.x")
    print("    - TimescaleDB: usa template PostgreSQL (engine compativel)")
    print("    - PDF reports: funcao nativa do Zabbix em Relatorios > Gerar PDF")
    print("    - Power BI: integracao via Zabbix API + Power BI Connector")
    print("    - Asigra: sem template publico disponivel")
    print()


if __name__ == "__main__":
    main()
