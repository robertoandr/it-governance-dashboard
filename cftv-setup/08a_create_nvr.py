#!/usr/bin/env python3
# ═══════════════════════════════════════════════════════════════
#   Fase 3a — Criar Host NVR Hikvision DS-7632NXI-K2
#   Projeto: Dashboard de Governança de TI — Grupo Gadens
#
#   Cria 1 host no Zabbix:
#   • Nome técnico:  nvr-centro-01
#   • Nome visível:  NVR Centro - Hikvision DS-7632NXI-K2
#   • IP:            172.29.11.20
#   • Grupos:        Lojas/Centro/CFTV/NVR (47) + CFTV/NVRs (52)
#   • Template:      Zabbix agent/ICMP ping (disponibilidade)
#   • Tags:          vendor, model, loja, tipo, criticidade
# ═══════════════════════════════════════════════════════════════

import json
import ssl
import sys
import urllib.request
import urllib.error
from pathlib import Path

# ─── Configurações ──────────────────────────────────────────────
BASE_DIR = Path("/opt/it-gov-dashboard/cftv-setup")
CONFIG_FILE = BASE_DIR / ".zabbix_config.json"
GROUPS_FILE = BASE_DIR / ".hostgroups_ids.json"
OUTPUT_FILE = BASE_DIR / ".nvr_host_id.json"

# Dados do NVR (hardcoded — só 1 host)
NVR_DATA = {
    "host": "nvr-centro-01",
    "name": "NVR Centro - Hikvision DS-7632NXI-K2",
    "ip": "172.29.11.20",
    "vendor": "Hikvision",
    "model": "DS-7632NXI-K2",
    "loja": "Centro",
    "tipo": "NVR",
    "criticidade": "Alta",
    "canais_total": "32",
    "canais_uso": "18",
}

# IDs dos grupos (lidos do .hostgroups_ids.json)
GROUP_KEYS = ["Lojas/Centro/CFTV/NVR", "CFTV/NVRs"]

# Templates pra anexar (vamos descobrir IDs dinamicamente)
TEMPLATES_DESIRED = [
    "ICMP Ping",  # Template nativo do Zabbix
]


# ─── Funções auxiliares ─────────────────────────────────────────
def banner(titulo):
    print("\n" + "═" * 64)
    print(f"  {titulo}")
    print("═" * 64)


def zabbix_api(url, method, params, auth=None):
    """Chama API Zabbix 7.0 com suporte HTTPS self-signed."""
    payload = {
        "jsonrpc": "2.0",
        "method": method,
        "params": params,
        "id": 1,
    }
    headers = {"Content-Type": "application/json-rpc"}
    if auth and method not in ("user.login", "apiinfo.version"):
        headers["Authorization"] = f"Bearer {auth}"

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers)

    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    try:
        with urllib.request.urlopen(req, timeout=30, context=ssl_ctx) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        print(f"   ❌ Erro de rede: {e}")
        sys.exit(1)

    if "error" in result:
        err = result["error"]
        print(f"   ❌ API erro {err.get('code')}: {err.get('message')}")
        print(f"      Detalhes: {err.get('data')}")
        sys.exit(1)

    return result.get("result")


# ─── Main ───────────────────────────────────────────────────────
banner("🎥 FASE 3a — CRIAR NVR HIKVISION")
print(f"  Projeto: Dashboard de Governança de TI — Grupo Gadens")
print(f"  Equipamento: {NVR_DATA['vendor']} {NVR_DATA['model']}")
print(f"  Local: Loja {NVR_DATA['loja']} ({NVR_DATA['ip']})")

# ─── [1/6] Carregar configs ────────────────────────────────────
print("\n[1/6] Carregando configurações")

if not CONFIG_FILE.exists():
    print(f"   ❌ Config não encontrada: {CONFIG_FILE}")
    sys.exit(1)
with open(CONFIG_FILE) as f:
    cfg = json.load(f)
print(f"   ✅ URL: {cfg['api_url']}")

if not GROUPS_FILE.exists():
    print(f"   ❌ Mapping de grupos não encontrado: {GROUPS_FILE}")
    print(f"      Execute primeiro: 07_create_hostgroups.py")
    sys.exit(1)
with open(GROUPS_FILE) as f:
    groups_map = json.load(f)
print(f"   ✅ Grupos mapeados: {len(groups_map)} entradas")

# ─── [2/6] Autenticar ──────────────────────────────────────────
print("\n[2/6] Autenticando na API")

token = zabbix_api(
    cfg["api_url"],
    "user.login",
    {
        "username": cfg["user"],
        "password": cfg["password"],
    },
)
print(f"   ✅ Login OK como '{cfg['user']}'")
print(f"   ✅ Token: {token[:16]}...")

# ─── [3/6] Validar IDs dos grupos ──────────────────────────────
print("\n[3/6] Validando grupos de destino")

group_ids = []
for key in GROUP_KEYS:
    if key not in groups_map:
        print(f"   ❌ Grupo não encontrado no mapping: {key}")
        sys.exit(1)
    gid = groups_map[key]
    group_ids.append({"groupid": str(gid)})
    print(f"   ✅ {key} → ID {gid}")

# ─── [4/6] Buscar template ICMP Ping ───────────────────────────
print("\n[4/6] Localizando template(s)")

template_ids = []
for tname in TEMPLATES_DESIRED:
    tpls = zabbix_api(
        cfg["api_url"],
        "template.get",
        {
            "output": ["templateid", "name"],
            "filter": {"name": [tname]},
        },
        auth=token,
    )
    if not tpls:
        print(f"   ⚠️  Template '{tname}' não encontrado — pulando")
        continue
    tid = tpls[0]["templateid"]
    template_ids.append({"templateid": tid})
    print(f"   ✅ {tname} → ID {tid}")

if not template_ids:
    print("   ⚠️  Nenhum template encontrado — host será criado sem template")

# ─── [5/6] Verificar se host já existe ─────────────────────────
print("\n[5/6] Verificando se host já existe")

existing = zabbix_api(
    cfg["api_url"],
    "host.get",
    {
        "output": ["hostid", "host", "name"],
        "filter": {"host": [NVR_DATA["host"]]},
    },
    auth=token,
)

if existing:
    host_id = existing[0]["hostid"]
    print(f"   ⏭️  Host já existe: {NVR_DATA['host']} (ID {host_id})")
    print(f"      Nome visível: {existing[0]['name']}")
    print(f"      Pulando criação. Use update se quiser modificar.")
else:
    # ─── [6/6] Criar host ──────────────────────────────────────
    print("\n[6/6] Criando host no Zabbix")

    host_params = {
        "host": NVR_DATA["host"],
        "name": NVR_DATA["name"],
        "interfaces": [
            {
                "type": 1,  # 1=Agent, 2=SNMP, 3=IPMI, 4=JMX
                "main": 1,
                "useip": 1,
                "ip": NVR_DATA["ip"],
                "dns": "",
                "port": "10050",  # Porta padrão (mesmo sem agent, pra ping)
            }
        ],
        "groups": group_ids,
        "templates": template_ids,
        "tags": [
            {"tag": "vendor", "value": NVR_DATA["vendor"]},
            {"tag": "model", "value": NVR_DATA["model"]},
            {"tag": "loja", "value": NVR_DATA["loja"]},
            {"tag": "tipo", "value": NVR_DATA["tipo"]},
            {"tag": "criticidade", "value": NVR_DATA["criticidade"]},
            {"tag": "canais_total", "value": NVR_DATA["canais_total"]},
            {"tag": "canais_uso", "value": NVR_DATA["canais_uso"]},
        ],
        "inventory_mode": 1,  # 1 = Automático
        "inventory": {
            "type": "NVR",
            "type_full": f"{NVR_DATA['vendor']} {NVR_DATA['model']}",
            "name": NVR_DATA["name"],
            "alias": NVR_DATA["host"],
            "vendor": NVR_DATA["vendor"],
            "model": NVR_DATA["model"],
            "location": f"Loja {NVR_DATA['loja']}",
            "notes": (
                f"NVR de CFTV - Loja {NVR_DATA['loja']}\n"
                f"Capacidade: {NVR_DATA['canais_total']} canais\n"
                f"Em uso: {NVR_DATA['canais_uso']} câmeras\n"
                f"Criticidade: {NVR_DATA['criticidade']}"
            ),
        },
    }

    result = zabbix_api(cfg["api_url"], "host.create", host_params, auth=token)
    host_id = result["hostids"][0]
    print(f"   ✅ Host criado!")
    print(f"      ID:       {host_id}")
    print(f"      Host:     {NVR_DATA['host']}")
    print(f"      Nome:     {NVR_DATA['name']}")
    print(f"      IP:       {NVR_DATA['ip']}")
    print(f"      Grupos:   {len(group_ids)}")
    print(f"      Templates:{len(template_ids)}")
    print(f"      Tags:     7")

# ─── Persistir ID ───────────────────────────────────────────────
output_data = {
    "host_id": str(host_id),
    "host": NVR_DATA["host"],
    "name": NVR_DATA["name"],
    "ip": NVR_DATA["ip"],
    "group_ids": [g["groupid"] for g in group_ids],
    "template_ids": [t["templateid"] for t in template_ids],
}
with open(OUTPUT_FILE, "w") as f:
    json.dump(output_data, f, indent=2, ensure_ascii=False)
print(f"\n   💾 Dados salvos em: {OUTPUT_FILE}")

# ─── Logout ─────────────────────────────────────────────────────
zabbix_api(cfg["api_url"], "user.logout", [], auth=token)

# ─── Resumo final ───────────────────────────────────────────────
banner("✅ FASE 3a CONCLUÍDA")
print(f"""
  🎯 PRÓXIMOS PASSOS:

  1️⃣  Validar no Zabbix Web:
      https://172.29.2.11/zabbix → Data collection → Hosts
      Procure: {NVR_DATA["host"]}
  
  2️⃣  Aguardar 1-5 minutos pro primeiro ping:
      Status deve ficar VERDE (UP)
  
  3️⃣  Se status ficar VERMELHO:
      • Verificar conectividade: ping {NVR_DATA["ip"]}
      • Verificar firewall entre Zabbix e NVR
      • Confirmar ICMP habilitado no NVR
  
  4️⃣  Quando estiver OK, criar as 18 câmeras:
      Fase 3b — me passar a lista de IPs

  📊 Comando rápido pra ver status via API:
      curl -sk -X POST -H 'Content-Type: application/json-rpc' \\
        -d '{{"jsonrpc":"2.0","method":"host.get",
             "params":{{"hostids":"{host_id}",
                       "output":["host","status","available"]}},
             "id":1}}' \\
        {cfg["api_url"]}
""")
