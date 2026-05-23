#!/usr/bin/env python3
"""Cria host groups CFTV no Zabbix via API."""
import sys

sys.path.insert(0, "/opt/it-gov-dashboard")
import requests

import config

ZBX_URL = config.ZABBIX_URL

def call(method, params, auth=None):
    body = {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}
    if auth: body["auth"] = auth
    r = requests.post(ZBX_URL, json=body, timeout=15)
    data = r.json()
    if "error" in data:
        raise RuntimeError(f"{method}: {data['error']}")
    return data["result"]

# Login
token = call("user.login", {
    "username": config.ZABBIX_USER,
    "password": config.ZABBIX_PASSWORD
}, auth=None)
print("✓ Login OK")

groups_to_create = [
    "CFTV",
    "CFTV/DVRs",
    "CFTV/Câmeras",
    "CFTV/Faciais",
    "CFTV/Antenas",
    "CFTV/Telas",
]

created, existing = [], []
for name in groups_to_create:
    # Verifica se já existe
    found = call("hostgroup.get", {"output": ["groupid", "name"], "filter": {"name": name}}, auth=token)
    if found:
        existing.append(f"{name} (id={found[0]['groupid']})")
        continue
    result = call("hostgroup.create", {"name": name}, auth=token)
    created.append(f"{name} (id={result['groupids'][0]})")

print()
print(f"✓ Criados ({len(created)}):")
for g in created: print(f"  + {g}")
print(f"ℹ Já existiam ({len(existing)}):")
for g in existing: print(f"  · {g}")

# Lista grupos CFTV no fim
print()
print("=== Estado atual dos grupos CFTV ===")
all_cftv = call("hostgroup.get", {
    "output": ["groupid", "name"],
    "search": {"name": "CFTV"}
}, auth=token)
for g in sorted(all_cftv, key=lambda x: x["name"]):
    print(f"  {g['groupid']:>5}  {g['name']}")

# Logout
call("user.logout", [], auth=token)
