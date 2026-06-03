#!/usr/bin/env python3
# ═══════════════════════════════════════════════════════════════
#   Fase 4b — Triggers Customizadas para CFTV
#   Projeto: Dashboard de Governança de TI — Grupo Gadens
#
#   Cria triggers diretamente nos hosts (NVR + Câmeras):
#   • NVR: 1 trigger Disaster (down 2min)
#   • Cada câmera: 4 triggers (Average down, High down, latência, loss)
#   • Usa macros globais criadas na Fase 4a
# ═══════════════════════════════════════════════════════════════

import json
import ssl
import sys
import urllib.request
from pathlib import Path

BASE_DIR = Path("/opt/it-gov-dashboard/cftv-setup")
CONFIG_FILE = BASE_DIR / ".zabbix_config.json"
GROUPS_FILE = BASE_DIR / ".hostgroups_ids.json"
OUTPUT_FILE = BASE_DIR / ".triggers_ids.json"

# Severidades Zabbix: 0=NotClass, 1=Info, 2=Warn, 3=Avg, 4=High, 5=Disaster
SEV_INFO, SEV_WARN, SEV_AVG, SEV_HIGH, SEV_DISASTER = 1, 2, 3, 4, 5


# ─── Definições das Triggers ────────────────────────────────────
def triggers_for_nvr(hostname):
    """Triggers do NVR (mais críticas)."""
    return [
        {
            "description": f"[CFTV-NVR] {hostname} INDISPONÍVEL — perda total de gravação",
            "expression": f"min(/{hostname}/icmpping,{{$CFTV.NVR.DOWN.DISASTER}})=0",
            "priority": SEV_DISASTER,
            "comments": "NVR não responde a ping há mais de 2 minutos. CFTV inteiro comprometido.",
            "manual_close": 1,
            "tags": [
                {"tag": "categoria", "value": "cftv"},
                {"tag": "tipo", "value": "nvr_down"},
                {"tag": "severidade", "value": "disaster"},
            ],
        },
    ]


def triggers_for_camera(hostname):
    """4 triggers para cada câmera."""
    return [
        # T-CAM-1: Average — instabilidade (2min)
        {
            "description": f"[CFTV-CAM] {hostname} instável (down há 2min)",
            "expression": f"min(/{hostname}/icmpping,{{$CFTV.CAM.DOWN.WARN}})=0",
            "priority": SEV_AVG,
            "comments": "Câmera não responde há 2 minutos. Pode ser instabilidade momentânea.",
            "manual_close": 1,
            "tags": [
                {"tag": "categoria", "value": "cftv"},
                {"tag": "tipo", "value": "cam_down"},
                {"tag": "severidade", "value": "average"},
            ],
        },
        # T-CAM-2: High — down confirmado (5min)
        {
            "description": f"[CFTV-CAM] {hostname} OFFLINE (down há 5min)",
            "expression": f"min(/{hostname}/icmpping,{{$CFTV.CAM.DOWN.HIGH}})=0",
            "priority": SEV_HIGH,
            "comments": "Câmera offline há 5 minutos. Acionar técnico para verificação física.",
            "manual_close": 1,
            "tags": [
                {"tag": "categoria", "value": "cftv"},
                {"tag": "tipo", "value": "cam_down"},
                {"tag": "severidade", "value": "high"},
            ],
        },
        # T-CAM-3: Warning — latência alta
        {
            "description": f"[CFTV-CAM] {hostname} latência alta (>5ms)",
            "expression": f"avg(/{hostname}/icmppingsec,5m)>{{$CFTV.PING.LATENCY.WARN}}",
            "priority": SEV_WARN,
            "comments": "Latência média nos últimos 5min acima de 5ms. Pode indicar congestionamento de rede ou problema PoE.",
            "manual_close": 1,
            "tags": [
                {"tag": "categoria", "value": "cftv"},
                {"tag": "tipo", "value": "latencia"},
                {"tag": "severidade", "value": "warning"},
            ],
        },
        # T-CAM-4: Warning — packet loss
        {
            "description": f"[CFTV-CAM] {hostname} packet loss >10%",
            "expression": f"avg(/{hostname}/icmppingloss,5m)>{{$CFTV.PING.LOSS.WARN}}",
            "priority": SEV_WARN,
            "comments": "Perda de pacotes superior a 10% nos últimos 5min. Verificar cabeamento, switch ou interferência.",
            "manual_close": 1,
            "tags": [
                {"tag": "categoria", "value": "cftv"},
                {"tag": "tipo", "value": "packet_loss"},
                {"tag": "severidade", "value": "warning"},
            ],
        },
    ]


# ─── Helpers ────────────────────────────────────────────────────
def banner(t):
    print("\n" + "═" * 70)
    print(f"  {t}")
    print("═" * 70)


def api(url, method, params, auth=None):
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
        with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
            result = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print(f"   ❌ Erro de rede: {e}")
        sys.exit(1)
    if "error" in result:
        err = result["error"]
        print(f"   ❌ {err.get('message')}: {err.get('data')}")
        return None
    return result.get("result")


# ─── Main ───────────────────────────────────────────────────────
banner("⚡ FASE 4b — CRIAR TRIGGERS CUSTOMIZADAS CFTV")

cfg = json.loads(CONFIG_FILE.read_text())
groups = json.loads(GROUPS_FILE.read_text())

print("\n[1/5] Autenticando")
tok = api(cfg["api_url"], "user.login", {"username": cfg["user"], "password": cfg["password"]})
print("   ✅ Login OK")

print("\n[2/5] Buscando hosts CFTV (NVRs + Câmeras)")
nvr_gid = str(groups["CFTV/NVRs"])
cam_gid = str(groups["CFTV/Cameras"])

nvrs = api(cfg["api_url"], "host.get", {"groupids": [nvr_gid], "output": ["hostid", "host"]}, auth=tok)

cams = api(cfg["api_url"], "host.get", {"groupids": [cam_gid], "output": ["hostid", "host"]}, auth=tok)

print(f"   ✅ NVRs: {len(nvrs)}")
print(f"   ✅ Câmeras: {len(cams)}")
print(f"   📊 Triggers planejadas: {len(nvrs) * 1 + len(cams) * 4}")

print("\n[3/5] Listando triggers existentes (para evitar duplicação)")
existing = (
    api(
        cfg["api_url"],
        "trigger.get",
        {
            "hostids": [h["hostid"] for h in nvrs + cams],
            "output": ["triggerid", "description"],
        },
        auth=tok,
    )
    or []
)
existing_descs = {t["description"] for t in existing}
print(f"   ℹ️  Triggers já existentes nestes hosts: {len(existing)}")

print("\n[4/5] Criando triggers")
print("─" * 70)

results = {"criadas": [], "ja_existiam": [], "erros": []}


def create_trigger_list(host, trig_list, label):
    for t in trig_list:
        if t["description"] in existing_descs:
            results["ja_existiam"].append(t["description"])
            print(f"   ⏭️  {label:20s} já existe: {t['description'][:50]}...")
            continue
        r = api(cfg["api_url"], "trigger.create", t, auth=tok)
        if r and "triggerids" in r:
            tid = r["triggerids"][0]
            results["criadas"].append({"id": tid, "desc": t["description"]})
            sev_icon = {1: "🔵", 2: "🟡", 3: "🟠", 4: "🔴", 5: "⚫"}.get(t["priority"], "⚪")
            print(f"   ✅ {label:20s} {sev_icon} ID {tid}: {t['description'][:50]}...")
        else:
            results["erros"].append(t["description"])
            print(f"   ❌ {label:20s} falhou: {t['description'][:50]}...")


# NVRs
for nvr in nvrs:
    print(f"\n  🎥 NVR: {nvr['host']}")
    create_trigger_list(nvr, triggers_for_nvr(nvr["host"]), nvr["host"])

# Câmeras
for cam in cams:
    print(f"\n  📹 Câmera: {cam['host']}")
    create_trigger_list(cam, triggers_for_camera(cam["host"]), cam["host"])

print("\n[5/5] Salvando mapeamento")
OUTPUT_FILE.write_text(json.dumps(results, indent=2, ensure_ascii=False))
print(f"   💾 {OUTPUT_FILE}")

api(cfg["api_url"], "user.logout", [], auth=tok)

banner("✅ FASE 4b CONCLUÍDA")
print(f"""
  📊 RESULTADO:
     ✅ Criadas:      {len(results["criadas"]):3d}
     ⏭️  Já existiam:  {len(results["ja_existiam"]):3d}
     ❌ Erros:         {len(results["erros"]):3d}
     ────────────────────────
     📦 Total alvo:   {len(nvrs) * 1 + len(cams) * 4:3d}

  💡 ONDE VER NO ZABBIX:
     • Configuration → Hosts → [clicar host] → Triggers
     • Monitoring → Problems (vai mostrar D01 e D15 ativos!)

  🎯 PRÓXIMO PASSO:
     Fase 4c → Criar dependências NVR → Câmeras
              (evita avalanche de alertas se NVR cair)
""")
