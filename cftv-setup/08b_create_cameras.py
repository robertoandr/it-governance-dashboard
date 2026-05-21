#!/usr/bin/env python3
# ═══════════════════════════════════════════════════════════════
#   Fase 3b — Criar 18 Câmeras IP do NVR Centro
#   Projeto: Dashboard de Governança de TI — Grupo Gadens
#
#   Cria 18 hosts no Zabbix (cam-loja-d01 a cam-loja-d18):
#   • Template:  ICMP Ping (mesma estratégia do NVR)
#   • Grupos:    Lojas/Centro/CFTV/Cameras (48) + CFTV/Cameras (53)
#   • Tags:      canal, ip_status, vendor, parent_nvr, etc.
#   • Inventory: rastreabilidade técnica completa
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
NVR_FILE = BASE_DIR / ".nvr_host_id.json"
OUTPUT_FILE = BASE_DIR / ".cameras_ids.json"

# Rede esperada (pra detectar anomalias)
EXPECTED_NETWORK = "172.29.11."

# ─── Dados das 18 câmeras ───────────────────────────────────────
# Estrutura: (canal, ip)
# Localização vai como tag genérica "a_definir" (atualizar depois)
CAMERAS = [
    ("D1",  "172.29.11.151"),
    ("D2",  "172.29.11.5"),
    ("D3",  "172.29.11.7"),
    ("D4",  "172.29.11.6"),
    ("D5",  "172.29.11.3"),
    ("D6",  "172.29.11.2"),
    ("D7",  "172.29.11.4"),
    ("D8",  "172.29.11.12"),
    ("D9",  "172.29.11.150"),
    ("D10", "172.29.11.8"),
    ("D11", "172.29.11.13"),
    ("D12", "172.29.11.11"),
    ("D13", "172.29.11.9"),
    ("D14", "172.29.11.15"),
    ("D15", "192.168.1.124"),   # ⚠️ IP fora da rede (rastreado via tag)
    ("D16", "172.29.11.10"),
    ("D17", "172.29.11.14"),
    ("D18", "172.29.11.148"),
]

# Grupos onde TODAS as câmeras vão entrar
GROUP_KEYS = ["Lojas/Centro/CFTV/Cameras", "CFTV/Cameras"]

# Templates a anexar
TEMPLATES_DESIRED = ["ICMP Ping"]


# ─── Helpers ────────────────────────────────────────────────────
def banner(t):
    print("\n" + "═" * 64)
    print(f"  {t}")
    print("═" * 64)


def zabbix_api(url, method, params, auth=None):
    payload = {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}
    headers = {"Content-Type": "application/json-rpc"}
    if auth and method not in ("user.login", "apiinfo.version"):
        headers["Authorization"] = f"Bearer {auth}"

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers)

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    try:
        with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
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


def classify_ip(ip):
    """Classifica IP e retorna (status, tag_value)."""
    if ip.startswith(EXPECTED_NETWORK):
        return "ok", "rede_correta"
    else:
        return "warning", "fora_da_rede"


# ─── Main ───────────────────────────────────────────────────────
banner("📹 FASE 3b — CRIAR 18 CÂMERAS IP")
print(f"  Projeto: Dashboard de Governança de TI — Grupo Gadens")
print(f"  Local: Loja Centro (NVR 172.29.11.20)")
print(f"  Total a criar: {len(CAMERAS)} câmeras")

# ─── [1/7] Carregar configs ────────────────────────────────────
print("\n[1/7] Carregando configurações")

for f in [CONFIG_FILE, GROUPS_FILE, NVR_FILE]:
    if not f.exists():
        print(f"   ❌ Arquivo não encontrado: {f}")
        sys.exit(1)

cfg = json.loads(CONFIG_FILE.read_text())
groups_map = json.loads(GROUPS_FILE.read_text())
nvr_data = json.loads(NVR_FILE.read_text())

print(f"   ✅ URL: {cfg['api_url']}")
print(f"   ✅ Grupos mapeados: {len(groups_map)}")
print(f"   ✅ NVR pai: {nvr_data['host']} (ID {nvr_data['host_id']})")

# ─── [2/7] Autenticar ──────────────────────────────────────────
print("\n[2/7] Autenticando na API")
token = zabbix_api(cfg["api_url"], "user.login", {
    "username": cfg["user"],
    "password": cfg["password"],
})
print(f"   ✅ Login OK como '{cfg['user']}'")

# ─── [3/7] Validar grupos ──────────────────────────────────────
print("\n[3/7] Validando grupos de destino")
group_ids = []
for key in GROUP_KEYS:
    if key not in groups_map:
        print(f"   ❌ Grupo não encontrado: {key}")
        sys.exit(1)
    gid = str(groups_map[key])
    group_ids.append({"groupid": gid})
    print(f"   ✅ {key} → ID {gid}")

# ─── [4/7] Localizar templates ─────────────────────────────────
print("\n[4/7] Localizando templates")
template_ids = []
for tname in TEMPLATES_DESIRED:
    tpls = zabbix_api(cfg["api_url"], "template.get", {
        "output": ["templateid", "name"],
        "filter": {"name": [tname]},
    }, auth=token)
    if tpls:
        template_ids.append({"templateid": tpls[0]["templateid"]})
        print(f"   ✅ {tname} → ID {tpls[0]['templateid']}")
    else:
        print(f"   ⚠️  Template '{tname}' não encontrado")

# ─── [5/7] Análise prévia ──────────────────────────────────────
print("\n[5/7] Análise prévia dos IPs")
anomalias = sum(1 for _, ip in CAMERAS if not ip.startswith(EXPECTED_NETWORK))
print(f"   📊 Total câmeras:       {len(CAMERAS)}")
print(f"   ✅ Rede correta:        {len(CAMERAS) - anomalias}")
print(f"   ⚠️  Fora da rede:       {anomalias}")
if anomalias:
    print(f"   🚨 Anomalias detectadas:")
    for ch, ip in CAMERAS:
        if not ip.startswith(EXPECTED_NETWORK):
            print(f"      • {ch}: {ip}  → tag 'ip_status: fora_da_rede'")

# ─── [6/7] Criar câmeras ───────────────────────────────────────
print("\n[6/7] Criando hosts")
print("─" * 64)

resultados = {"criados": [], "ja_existentes": [], "erros": []}

for idx, (canal, ip) in enumerate(CAMERAS, 1):
    canal_num = canal.replace("D", "").zfill(2)   # D1 → 01, D15 → 15
    host_tech = f"cam-loja-d{canal_num}"
    host_name = f"Câmera {canal} - Loja Centro (canal {canal})"
    
    ip_status, ip_tag = classify_ip(ip)
    icon = "⚠️ " if ip_status == "warning" else "  "
    
    # Verificar duplicata
    existing = zabbix_api(cfg["api_url"], "host.get", {
        "output": ["hostid", "host"],
        "filter": {"host": [host_tech]},
    }, auth=token)
    
    if existing:
        hid = existing[0]["hostid"]
        print(f" {idx:2d}/18 {icon}⏭️  {host_tech:20s} {ip:15s} já existe (ID {hid})")
        resultados["ja_existentes"].append({"host": host_tech, "id": hid})
        continue
    
    # Criar
    params = {
        "host": host_tech,
        "name": host_name,
        "interfaces": [{
            "type": 1, "main": 1, "useip": 1,
            "ip": ip, "dns": "", "port": "10050",
        }],
        "groups": group_ids,
        "templates": template_ids,
        "tags": [
            {"tag": "vendor", "value": "Hikvision"},
            {"tag": "tipo", "value": "Camera"},
            {"tag": "loja", "value": "Centro"},
            {"tag": "canal_nvr", "value": canal},
            {"tag": "parent_nvr", "value": nvr_data["host"]},
            {"tag": "parent_nvr_id", "value": str(nvr_data["host_id"])},
            {"tag": "ip_status", "value": ip_tag},
            {"tag": "localizacao", "value": "a_definir"},
            {"tag": "criticidade", "value": "Media"},
        ],
        "inventory_mode": 1,
        "inventory": {
            "type": "Camera IP",
            "name": host_name,
            "alias": host_tech,
            "vendor": "Hikvision",
            "location": "Loja Centro",
            "notes": (
                f"Câmera canal {canal} do NVR {nvr_data['host']}\n"
                f"IP atual: {ip}\n"
                f"Status IP: {ip_tag}\n"
                f"Localização física: a definir"
            ),
        },
    }
    
    try:
        r = zabbix_api(cfg["api_url"], "host.create", params, auth=token)
        hid = r["hostids"][0]
        print(f" {idx:2d}/18 {icon}✅ {host_tech:20s} {ip:15s} criado (ID {hid})")
        resultados["criados"].append({
            "host": host_tech, "id": hid, "ip": ip, "canal": canal, "ip_status": ip_tag,
        })
    except SystemExit:
        print(f" {idx:2d}/18 {icon}❌ {host_tech:20s} {ip:15s} ERRO")
        resultados["erros"].append({"host": host_tech, "ip": ip})

# ─── [7/7] Persistir e resumir ─────────────────────────────────
print("\n[7/7] Salvando mapeamento")
output = {
    "loja": "Centro",
    "nvr_parent": nvr_data["host"],
    "nvr_parent_id": nvr_data["host_id"],
    "total": len(CAMERAS),
    "criados": resultados["criados"],
    "ja_existentes": resultados["ja_existentes"],
    "erros": resultados["erros"],
}
OUTPUT_FILE.write_text(json.dumps(output, indent=2, ensure_ascii=False))
print(f"   💾 Salvo em: {OUTPUT_FILE}")

# Logout
zabbix_api(cfg["api_url"], "user.logout", [], auth=token)

# ─── Resumo final ───────────────────────────────────────────────
banner("✅ FASE 3b CONCLUÍDA")
print(f"""
  📊 RESULTADO:
     ✅ Criados:       {len(resultados['criados']):2d}
     ⏭️  Já existiam:   {len(resultados['ja_existentes']):2d}
     ❌ Erros:         {len(resultados['erros']):2d}
     ⚠️  Anomalias IP:  {anomalias:2d}
     ─────────────────────
     📦 Total:         {len(CAMERAS):2d}

  🎯 PRÓXIMOS PASSOS:

  1️⃣  Aguardar 1-5 min e validar status no Zabbix Web:
      https://172.29.2.11/zabbix → Monitoring → Hosts
      Filtro: cam-loja-d
  
  2️⃣  Identificar quais ficam OFFLINE:
      Resultado esperado: 
      • D15 (192.168.1.124) → OFFLINE garantido
      • +1 a 2 outras       → conforme suas suspeitas iniciais
  
  3️⃣  Atualizar localizações depois:
      Tag 'localizacao' → editar via web ou via script Fase 3c

  4️⃣  Próximas fases:
      • Fase 4: Triggers customizadas (alertas por loja)
      • Fase 5: Dashboard visual no Zabbix
      • Fase 6: Integração com dashboard externo (Grafana/web)
""")
