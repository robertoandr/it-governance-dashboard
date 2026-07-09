#!/usr/bin/env python3
"""
Cria/atualiza no Zabbix o monitoramento do sensor de temperatura do datacenter
(Nextcon DSB WIFI, canal ThingSpeak público — sem autenticação).

Cria (idempotente):
  - Hostgroup "Sensores IoT" (se ainda não existir)
  - Host "DSB-WIFI-Datacenter" (IP 0.0.0.0 — sem agente, dados via HTTP agent item)
  - Item HTTP agent "Temperatura Datacenter (°C)" — GET direto no ThingSpeak,
    preprocessing JSONPath extrai $.feeds[0].field1
  - Trigger High "Datacenter: temperatura acima de 20°C"

Uso:
    python3 infra-setup/04_create_datacenter_temp_sensor.py

Pré-requisito: NEXTCON_CHANNEL_ID no .env (raiz do projeto).
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
if not ZABBIX_URL.endswith("/api_jsonrpc.php"):
    ZABBIX_URL = ZABBIX_URL.rstrip("/") + "/api_jsonrpc.php"
ZABBIX_TOKEN = os.environ.get("ZABBIX_TOKEN", "")
NEXTCON_CHANNEL_ID = os.environ.get("NEXTCON_CHANNEL_ID", "")

os.environ.pop("ZABBIX_USER", None)
os.environ.pop("ZABBIX_PASSWORD", None)

urllib3.disable_warnings()

try:
    from zabbix_utils import ZabbixAPI
    from zabbix_utils.exceptions import APIRequestError
except ImportError:
    sys.exit("Instale: pip install zabbix-utils --break-system-packages")

# ── Constantes ─────────────────────────────────────────────────────────────
GRP_IOT_NAME = "Sensores IoT"
HOST_TECH = "DSB-WIFI-Datacenter"
HOST_NAME = "DSB-WIFI-Datacenter"
ITEM_KEY = "temperatura.datacenter"
ITEM_NAME = "Temperatura Datacenter (°C)"
TRIGGER_DESCR = "Datacenter: temperatura acima de 20°C"
TEMP_LIMIT_C = 20
SEVERITY_HIGH = 4

# Tipos numéricos da API Zabbix 7.0
ITEM_TYPE_HTTP_AGENT = 19
VALUE_TYPE_FLOAT = 0
REQUEST_METHOD_GET = 0
PREPROC_JSONPATH = "12"


def banner(text: str) -> None:
    print(f"\n{'=' * 60}\n  {text}\n{'=' * 60}")


def ok(msg: str) -> None:
    print(f"  [OK] {msg}")


def info(msg: str) -> None:
    print(f"  [--] {msg}")


def warn(msg: str) -> None:
    print(f"  [!!] {msg}")


def main() -> None:
    banner("Sensor de Temperatura do Datacenter (Nextcon/ThingSpeak)")

    if not NEXTCON_CHANNEL_ID:
        sys.exit("  [ERRO] NEXTCON_CHANNEL_ID não configurado no .env")

    api = ZabbixAPI(url=ZABBIX_URL, token=ZABBIX_TOKEN, skip_version_check=True)
    print(f"  Zabbix {api.api_version()} conectado — canal ThingSpeak {NEXTCON_CHANNEL_ID}")

    # ── 1. Hostgroup ──────────────────────────────────────────────────────────
    banner("1. Grupo 'Sensores IoT'")
    existing_groups = {g["name"]: g["groupid"] for g in api.hostgroup.get(output=["groupid", "name"])}
    if GRP_IOT_NAME not in existing_groups:
        result = api.hostgroup.create(name=GRP_IOT_NAME)
        grp_id = result["groupids"][0]
        ok(f"Grupo criado: {GRP_IOT_NAME} → ID {grp_id}")
    else:
        grp_id = existing_groups[GRP_IOT_NAME]
        info(f"Grupo já existe: {GRP_IOT_NAME} (ID {grp_id})")

    # ── 2. Host ───────────────────────────────────────────────────────────────
    banner("2. Host do sensor")
    existing_hosts = {h["host"]: h["hostid"] for h in api.host.get(output=["hostid", "host"])}

    if HOST_TECH in existing_hosts:
        hostid = existing_hosts[HOST_TECH]
        try:
            api.host.update(hostid=hostid, groups=[{"groupid": grp_id}], name=HOST_NAME, status=0)
            ok(f"Host atualizado: {HOST_NAME} (ID {hostid})")
        except APIRequestError as exc:
            warn(f"update {HOST_TECH}: {exc}")
    else:
        try:
            result = api.host.create(
                host=HOST_TECH,
                name=HOST_NAME,
                groups=[{"groupid": grp_id}],
                interfaces=[
                    {
                        "type": "1",  # agent (necessário mesmo sem agente real)
                        "main": "1",
                        "useip": "1",
                        "ip": "0.0.0.0",
                        "dns": "",
                        "port": "10050",
                    }
                ],
                description="Sensor de temperatura Nextcon DSB WIFI. Dados via HTTP agent item, "
                f"lidos diretamente da API pública do ThingSpeak (canal {NEXTCON_CHANNEL_ID}).",
                status=0,
            )
            hostid = result["hostids"][0]
            ok(f"Host criado: {HOST_NAME} → hostid={hostid}")
        except APIRequestError as exc:
            sys.exit(f"  [ERRO] create host {HOST_TECH}: {exc}")

    # ── 3. Item (HTTP agent + JSONPath) ──────────────────────────────────────
    banner("3. Item de temperatura (HTTP agent)")
    item_url = f"https://api.thingspeak.com/channels/{NEXTCON_CHANNEL_ID}/feeds.json?results=1"
    preprocessing = [
        {
            "type": PREPROC_JSONPATH,
            "params": "$.feeds[0].field1",
            "error_handler": "0",
            "error_handler_params": "",
        }
    ]

    existing_items = {i["key_"]: i["itemid"] for i in api.item.get(hostids=[hostid], output=["itemid", "key_"])}

    item_def = {
        "name": ITEM_NAME,
        "key_": ITEM_KEY,
        "hostid": hostid,
        "type": ITEM_TYPE_HTTP_AGENT,
        "value_type": VALUE_TYPE_FLOAT,
        "url": item_url,
        "request_method": REQUEST_METHOD_GET,
        "timeout": "10s",
        "delay": "5m",
        "history": "7d",
        "trends": "365d",
        "units": "°C",
        "verify_peer": 0,
        "verify_host": 0,
        "preprocessing": preprocessing,
        "description": "Temperatura atual do datacenter, lida do sensor Nextcon DSB WIFI via ThingSpeak.",
    }

    if ITEM_KEY in existing_items:
        itemid = existing_items[ITEM_KEY]
        try:
            api.item.update(itemid=itemid, **{k: v for k, v in item_def.items() if k != "hostid"})
            ok(f"Item atualizado: {ITEM_NAME} (ID {itemid})")
        except APIRequestError as exc:
            warn(f"update item {ITEM_KEY}: {exc}")
    else:
        try:
            result = api.item.create(**item_def)
            itemid = result["itemids"][0]
            ok(f"Item criado: {ITEM_NAME} → itemid={itemid}")
        except APIRequestError as exc:
            sys.exit(f"  [ERRO] create item {ITEM_KEY}: {exc}")

    # ── 4. Trigger ────────────────────────────────────────────────────────────
    banner("4. Trigger de alerta (High)")
    expression = f"last(/{HOST_TECH}/{ITEM_KEY})>{TEMP_LIMIT_C}"

    existing_triggers = {
        t["description"]: t["triggerid"] for t in api.trigger.get(hostids=[hostid], output=["triggerid", "description"])
    }

    trigger_def = {
        "description": TRIGGER_DESCR,
        "expression": expression,
        "priority": SEVERITY_HIGH,
        "manual_close": 1,
        "comments": "Temperatura do datacenter acima do limite operacional seguro.",
        "tags": [
            {"tag": "categoria", "value": "datacenter"},
            {"tag": "tipo", "value": "temperatura_alta"},
        ],
    }

    if TRIGGER_DESCR in existing_triggers:
        triggerid = existing_triggers[TRIGGER_DESCR]
        try:
            api.trigger.update(
                triggerid=triggerid,
                expression=expression,
                priority=SEVERITY_HIGH,
                comments=trigger_def["comments"],
                tags=trigger_def["tags"],
            )
            ok(f"Trigger atualizada: {TRIGGER_DESCR} (ID {triggerid})")
        except APIRequestError as exc:
            warn(f"update trigger: {exc}")
    else:
        try:
            result = api.trigger.create(**trigger_def)
            triggerid = result["triggerids"][0]
            ok(f"Trigger criada: {TRIGGER_DESCR} → triggerid={triggerid}")
        except APIRequestError as exc:
            sys.exit(f"  [ERRO] create trigger: {exc}")

    banner("CONCLUÍDO")
    print(f"  Host: {HOST_NAME} (ID {hostid})")
    print(f"  Item: {ITEM_NAME} — {ITEM_KEY}")
    print(f"  Trigger: {TRIGGER_DESCR}")
    print("  Próximo passo: infra-setup/05_create_clickup_alert.py (webhook ClickUp)\n")


if __name__ == "__main__":
    main()
