#!/usr/bin/env python3
"""Cria Template_CFTV_Ping com items ICMP + triggers."""

import sys

sys.path.insert(0, "/opt/it-gov-dashboard")
import requests

import config

ZBX_URL = config.ZABBIX_URL
TEMPLATE_NAME = "Template_CFTV_Ping"
TEMPLATE_GROUP_NAME = "Templates"  # grupo padrão de templates no Zabbix


def call(method, params, auth=None):
    body = {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}
    if auth:
        body["auth"] = auth
    r = requests.post(ZBX_URL, json=body, timeout=15)
    data = r.json()
    if "error" in data:
        raise RuntimeError(f"{method}: {data['error']}")
    return data["result"]


token = call("user.login", {"username": config.ZABBIX_USER, "password": config.ZABBIX_PASSWORD}, auth=None)
print("✓ Login OK")

# 1. Acha o template group "Templates"
tg = call(
    "templategroup.get",
    {"output": ["groupid", "name"], "filter": {"name": TEMPLATE_GROUP_NAME}},
    auth=token,
)
if not tg:
    # Zabbix 6+: pode ser que use templategroup.get; em 7.x existe certeza
    raise RuntimeError(f"Template group '{TEMPLATE_GROUP_NAME}' não encontrado")
tg_id = tg[0]["groupid"]
print(f"✓ Template group '{TEMPLATE_GROUP_NAME}' = {tg_id}")

# 2. Verifica se template já existe
existing = call(
    "template.get",
    {"output": ["templateid", "host"], "filter": {"host": TEMPLATE_NAME}},
    auth=token,
)
if existing:
    template_id = existing[0]["templateid"]
    print(f"ℹ Template já existe: id={template_id} — vou apenas garantir items/triggers")
else:
    result = call(
        "template.create",
        {
            "host": TEMPLATE_NAME,
            "name": "CFTV Ping ICMP",
            "description": "Monitoramento básico de dispositivos CFTV via ICMP. 1 ping/min, 2 triggers (5min High, 1h Disaster).",
            "groups": [{"groupid": tg_id}],
        },
        auth=token,
    )
    template_id = result["templateids"][0]
    print(f"✓ Template criado: id={template_id}")

# 3. Cria os 3 items ICMP
items_to_create = [
    {
        "name": "ICMP ping",
        "key_": "icmpping",
        "type": 3,  # Simple check
        "value_type": 3,  # Numeric unsigned
        "delay": "1m",
        "description": "1 = responde, 0 = não responde",
    },
    {
        "name": "ICMP loss",
        "key_": "icmppingloss",
        "type": 3,
        "value_type": 0,  # Numeric float
        "delay": "1m",
        "units": "%",
        "description": "% de pacotes perdidos",
    },
    {
        "name": "ICMP response time",
        "key_": "icmppingsec",
        "type": 3,
        "value_type": 0,
        "delay": "1m",
        "units": "s",
        "description": "Tempo de resposta médio",
    },
]

created_items = []
for item_def in items_to_create:
    # Verifica se já existe
    found = call(
        "item.get",
        {
            "output": ["itemid", "key_"],
            "templateids": [template_id],
            "filter": {"key_": item_def["key_"]},
        },
        auth=token,
    )
    if found:
        print(f"  ℹ item '{item_def['key_']}' já existe (id={found[0]['itemid']})")
        created_items.append(found[0]["itemid"])
        continue
    item_def["hostid"] = template_id
    r = call("item.create", item_def, auth=token)
    print(f"  ✓ item '{item_def['key_']}' criado (id={r['itemids'][0]})")
    created_items.append(r["itemids"][0])

# 4. Cria as 2 triggers
triggers_to_create = [
    {
        "description": "CFTV: {HOST.NAME} offline > 5 min",
        "expression": f"last(/{TEMPLATE_NAME}/icmpping,#3)=0",
        "priority": 4,  # High
        "comments": "Dispositivo CFTV não responde ao ping ICMP há pelo menos 3 verificações (5 min).",
        "manual_close": 1,
    },
    {
        "description": "CFTV: {HOST.NAME} OFFLINE > 1 HORA",
        "expression": f"min(/{TEMPLATE_NAME}/icmpping,1h)=0",
        "priority": 5,  # Disaster
        "comments": "Dispositivo CFTV offline há mais de 1 hora. Escalada para nível Disaster.",
        "manual_close": 1,
    },
]

created_triggers = {}
for tr_def in triggers_to_create:
    found = call(
        "trigger.get",
        {
            "output": ["triggerid", "description"],
            "templateids": [template_id],
            "filter": {"description": tr_def["description"]},
        },
        auth=token,
    )
    if found:
        print(f"  ℹ trigger '{tr_def['description'][:50]}...' já existe (id={found[0]['triggerid']})")
        created_triggers[tr_def["priority"]] = found[0]["triggerid"]
        continue
    r = call("trigger.create", tr_def, auth=token)
    print(f"  ✓ trigger '{tr_def['description'][:50]}...' criada (id={r['triggerids'][0]})")
    created_triggers[tr_def["priority"]] = r["triggerids"][0]

# 5. Cria dependência: Disaster (1h) depende de High (5min)
if 4 in created_triggers and 5 in created_triggers:
    high_id = created_triggers[4]
    disaster_id = created_triggers[5]
    # trigger.update permite definir dependencies
    try:
        call(
            "trigger.update",
            {
                "triggerid": disaster_id,
                "dependencies": [{"triggerid": high_id}],
            },
            auth=token,
        )
        print(f"  ✓ Dependência: Disaster ({disaster_id}) depende de High ({high_id})")
    except Exception as e:
        print(f"  ⚠ dependência não aplicada: {e}")

print()
print("=== Resumo template ===")
print(f"  Template ID: {template_id}")
print(f"  Items: {len(created_items)}")
print(f"  Triggers: {len(created_triggers)}")

call("user.logout", [], auth=token)
print()
print("✓ Etapa 2 concluída")
