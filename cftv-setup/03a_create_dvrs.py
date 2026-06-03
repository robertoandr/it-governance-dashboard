#!/usr/bin/env python3
"""Cria os 3 DVRs no Zabbix vinculados ao Template_CFTV_Ping."""

import sys

sys.path.insert(0, "/opt/it-gov-dashboard")
import requests

import config

ZBX_URL = config.ZABBIX_URL
TEMPLATE_NAME = "Template_CFTV_Ping"
GROUP_DVR = "CFTV/DVRs"
GROUP_PARENT = "CFTV"


def call(method, params, auth=None):
    body = {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}
    if auth:
        body["auth"] = auth
    r = requests.post(ZBX_URL, json=body, timeout=15)
    data = r.json()
    if "error" in data:
        raise RuntimeError(f"{method}: {data['error']}")
    return data["result"]


# Login
token = call("user.login", {"username": config.ZABBIX_USER, "password": config.ZABBIX_PASSWORD}, auth=None)
print("✓ Login OK")

# Busca IDs de template e grupos
tpl = call("template.get", {"output": ["templateid"], "filter": {"host": TEMPLATE_NAME}}, auth=token)
if not tpl:
    raise RuntimeError(f"Template {TEMPLATE_NAME} não encontrado")
tpl_id = tpl[0]["templateid"]
print(f"✓ Template ID: {tpl_id}")

g1 = call("hostgroup.get", {"output": ["groupid"], "filter": {"name": GROUP_DVR}}, auth=token)
g2 = call("hostgroup.get", {"output": ["groupid"], "filter": {"name": GROUP_PARENT}}, auth=token)
if not g1 or not g2:
    raise RuntimeError("Host groups não encontrados")
group_ids = [{"groupid": g1[0]["groupid"]}, {"groupid": g2[0]["groupid"]}]
print(f"✓ Groups: {GROUP_PARENT}={g2[0]['groupid']}, {GROUP_DVR}={g1[0]['groupid']}")

# Hosts a criar
DVRS = [
    {"host": "DVR-1", "visible": "DVR-1 · 9º/8º/Elevadores/Faciais", "ip": "172.29.11.17"},
    {"host": "DVR-2", "visible": "DVR-2 · 7º/6º andar", "ip": "172.29.11.18"},
    {"host": "DVR-3", "visible": "DVR-3 · 5º/Térreo/Garagem", "ip": "172.29.11.19"},
]

created, existing = [], []
for d in DVRS:
    # Verifica se já existe
    found = call("host.get", {"output": ["hostid", "host"], "filter": {"host": d["host"]}}, auth=token)
    if found:
        existing.append(f"{d['host']} (id={found[0]['hostid']})")
        continue

    result = call(
        "host.create",
        {
            "host": d["host"],
            "name": d["visible"],
            "groups": group_ids,
            "templates": [{"templateid": tpl_id}],
            "interfaces": [
                {
                    "type": 1,  # Agent type (mas template usa simple check, então qualquer interface serve)
                    "main": 1,
                    "useip": 1,
                    "ip": d["ip"],
                    "dns": "",
                    "port": "10050",
                }
            ],
            "tags": [
                {"tag": "category", "value": "cftv"},
                {"tag": "subcategory", "value": "dvr"},
            ],
            "description": f"DVR de CFTV. IP: {d['ip']}. Monitorado via ICMP.",
        },
        auth=token,
    )
    created.append(f"{d['host']} → {d['ip']} (id={result['hostids'][0]})")

print()
print(f"✓ Criados ({len(created)}):")
for h in created:
    print(f"  + {h}")
print(f"ℹ Já existiam ({len(existing)}):")
for h in existing:
    print(f"  · {h}")

call("user.logout", [], auth=token)
print()
print("✓ Etapa 3a concluída — aguarde 2-3 min e veja no Zabbix UI se aparecem ícones verdes")
