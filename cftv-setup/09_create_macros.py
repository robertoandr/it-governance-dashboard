#!/usr/bin/env python3
# ═══════════════════════════════════════════════════════════════
#   Fase 4a — Macros Globais para CFTV
#   Projeto: Dashboard de Governança de TI — Grupo Gadens
#
#   Cria macros globais (visíveis em TODOS os hosts):
#   • Thresholds de latência, loss, downtime
#   • Permite ajuste centralizado de TODAS as triggers
#   • Boa prática enterprise (DRY - Don't Repeat Yourself)
# ═══════════════════════════════════════════════════════════════

import json
import ssl
import sys
import urllib.request
from pathlib import Path

BASE_DIR = Path("/opt/it-gov-dashboard/cftv-setup")
CONFIG_FILE = BASE_DIR / ".zabbix_config.json"
OUTPUT_FILE = BASE_DIR / ".macros_ids.json"

# ─── Macros a criar ─────────────────────────────────────────────
# Formato: (macro, valor, descrição)
MACROS = [
    # ─── Downtime (em segundos) ───
    ("{$CFTV.CAM.DOWN.WARN}", "120",
     "Tempo em segundos para alertar câmera como instável (Average)"),

    ("{$CFTV.CAM.DOWN.HIGH}", "300",
     "Tempo em segundos para alertar câmera como down crítico (High)"),

    ("{$CFTV.NVR.DOWN.DISASTER}", "120",
     "Tempo em segundos para alertar NVR como disaster (perde tudo)"),

    # ─── Qualidade de rede ───
    ("{$CFTV.PING.LOSS.WARN}", "10",
     "Packet loss % acima do qual gera Warning (qualidade ruim)"),

    ("{$CFTV.PING.LOSS.HIGH}", "30",
     "Packet loss % acima do qual gera High (rede degradada)"),

    ("{$CFTV.PING.LATENCY.WARN}", "0.005",
     "Latência em segundos (5ms) acima da qual gera Warning"),

    ("{$CFTV.PING.LATENCY.HIGH}", "0.020",
     "Latência em segundos (20ms) acima da qual gera High"),

    # ─── Correlação ───
    ("{$CFTV.MASS.DOWN.THRESHOLD}", "3",
     "Número de câmeras down simultâneas para acionar alerta correlacionado"),

    # ─── Horário comercial (uso futuro em ações) ───
    ("{$CFTV.BUSINESS.HOURS}", "1-5,08:00-22:00;6,08:00-18:00",
     "Horário comercial das lojas (seg-sex 8-22h, sáb 8-18h)"),
]


# ─── Helpers ────────────────────────────────────────────────────
def banner(t):
    print("\n" + "═" * 64)
    print(f"  {t}")
    print("═" * 64)


def api(url, method, params, auth=None):
    payload = {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}
    headers = {"Content-Type": "application/json-rpc"}
    if auth and method not in ("user.login", "apiinfo.version"):
        headers["Authorization"] = f"Bearer {auth}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers)
    ctx = ssl.create_default_context(); ctx.check_hostname=False; ctx.verify_mode=ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
            result = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print(f"   ❌ Erro de rede: {e}"); sys.exit(1)
    if "error" in result:
        err = result["error"]
        print(f"   ❌ {err.get('message')}: {err.get('data')}")
        sys.exit(1)
    return result.get("result")


# ─── Main ───────────────────────────────────────────────────────
banner("⚙️  FASE 4a — CRIAR MACROS GLOBAIS CFTV")
print(f"  Total a criar/atualizar: {len(MACROS)}")

cfg = json.loads(CONFIG_FILE.read_text())

print("\n[1/4] Autenticando")
tok = api(cfg["api_url"], "user.login",
          {"username": cfg["user"], "password": cfg["password"]})
print("   ✅ Login OK")

print("\n[2/4] Listando macros globais existentes")
existing = api(cfg["api_url"], "usermacro.get",
               {"globalmacro": True, "output": "extend"}, auth=tok)
existing_map = {m["macro"]: m["globalmacroid"] for m in existing}
print(f"   ℹ️  Macros globais já existentes: {len(existing)}")

print("\n[3/4] Criando/atualizando macros")
print("─" * 64)
results = {"criados": [], "atualizados": []}

for macro, value, desc in MACROS:
    if macro in existing_map:
        mid = existing_map[macro]
        api(cfg["api_url"], "usermacro.updateglobal", {
            "globalmacroid": mid,
            "value": value,
            "description": desc,
        }, auth=tok)
        print(f"   🔄 {macro:35s} = {value:10s}  (atualizado)")
        results["atualizados"].append({"macro": macro, "id": mid})
    else:
        r = api(cfg["api_url"], "usermacro.createglobal", {
            "macro": macro,
            "value": value,
            "description": desc,
        }, auth=tok)
        mid = r["globalmacroids"][0]
        print(f"   ✅ {macro:35s} = {value:10s}  (criado ID {mid})")
        results["criados"].append({"macro": macro, "id": mid})

print("\n[4/4] Salvando mapeamento")
OUTPUT_FILE.write_text(json.dumps(results, indent=2, ensure_ascii=False))
print(f"   💾 {OUTPUT_FILE}")

api(cfg["api_url"], "user.logout", [], auth=tok)

banner("✅ FASE 4a CONCLUÍDA")
print(f"""
  📊 RESULTADO:
     ✅ Criados:      {len(results['criados']):2d}
     🔄 Atualizados:  {len(results['atualizados']):2d}
     ─────────────────────
     📦 Total:        {len(MACROS):2d}

  💡 ONDE VER NO ZABBIX:
     Administration → Macros
     (apenas usuários Super Admin enxergam)

  🎯 PRÓXIMO PASSO:
     Fase 4b → Criar triggers customizadas usando essas macros
""")
