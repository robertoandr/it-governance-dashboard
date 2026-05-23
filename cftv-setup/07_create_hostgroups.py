#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
═══════════════════════════════════════════════════════════════
  FASE 2 — Criação de Host Groups (Nested)
  Projeto: Dashboard de Governança de TI — Grupo Gadens
═══════════════════════════════════════════════════════════════

Cria estrutura hierárquica de grupos para governança multi-loja:

  Lojas/
    └─ Centro/
        ├─ CFTV
        ├─ Servidores
        ├─ Impressoras
        └─ Rede

  Por que nested?
  ├─ Permissões em cascata (futuro: usuário só vê sua loja)
  ├─ Filtros no dashboard por loja/categoria
  ├─ Escalável: adicionar Loja-Norte/Sul depois é trivial
  └─ Relatórios SLA por loja automáticos
"""

import json
import sys
import urllib.request
import urllib.error
from pathlib import Path

# ════════════════════════════════════════════════════════════
# CONFIGURAÇÃO
# ════════════════════════════════════════════════════════════
CONFIG_FILE = Path("/opt/it-gov-dashboard/cftv-setup/.zabbix_config.json")

# Estrutura de grupos a criar (nested com "/")
HOSTGROUPS = [
    # Topo
    "Lojas",
    # Loja Centro — completa
    "Lojas/Centro",
    "Lojas/Centro/CFTV",
    "Lojas/Centro/CFTV/NVR",
    "Lojas/Centro/CFTV/Cameras",
    "Lojas/Centro/Servidores",
    "Lojas/Centro/Impressoras",
    "Lojas/Centro/Rede",
    # Categorias transversais (úteis pra dashboards globais)
    "CFTV",
    "CFTV/NVRs",
    "CFTV/Cameras",
]


# ════════════════════════════════════════════════════════════
# UTILIDADES
# ════════════════════════════════════════════════════════════
def banner(text, char="═", width=64):
    print(char * width)
    print(f"  {text}")
    print(char * width)


def zabbix_api(url, method, params, auth=None):
    """Chama API Zabbix 7.0 — auth no header Authorization (HTTPS-aware)."""
    import ssl

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

    # Contexto SSL permissivo (cert self-signed ou IP direto)
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


# ════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════
def main():
    banner("🏢 FASE 2 — CRIAÇÃO DE HOST GROUPS")
    print("  Projeto: Dashboard de Governança de TI — Grupo Gadens\n")

    # ─── [1/4] Carregar config ──────────────────────────────
    print("[1/4] Carregando configuração")
    if not CONFIG_FILE.exists():
        print(f"   ❌ Config não encontrada: {CONFIG_FILE}")
        sys.exit(1)

    cfg = json.loads(CONFIG_FILE.read_text())
    url = cfg["api_url"]
    user = cfg["user"]
    password = cfg["password"]
    print(f"   ✅ URL: {url}")

    # ─── [2/4] Login ────────────────────────────────────────
    print("\n[2/4] Autenticando")
    auth_token = zabbix_api(
        url,
        "user.login",
        {
            "username": user,
            "password": password,
        },
    )
    print(f"   ✅ Login OK como '{user}'")

    # ─── [3/4] Listar grupos existentes ─────────────────────
    print("\n[3/4] Verificando grupos existentes")
    existing = zabbix_api(
        url,
        "hostgroup.get",
        {
            "output": ["groupid", "name"],
            "filter": {"name": HOSTGROUPS},
        },
        auth_token,
    )

    existing_names = {g["name"]: g["groupid"] for g in existing}
    print(f"   📊 Já existem: {len(existing_names)} grupo(s)")
    for name, gid in existing_names.items():
        print(f"      • ID {gid:>6} → {name}")

    # ─── [4/4] Criar grupos faltantes ───────────────────────
    print("\n[4/4] Criando grupos faltantes")
    created = {}
    skipped = []

    for name in HOSTGROUPS:
        if name in existing_names:
            skipped.append(name)
            continue

        result = zabbix_api(
            url,
            "hostgroup.create",
            {
                "name": name,
            },
            auth_token,
        )
        gid = result["groupids"][0]
        created[name] = gid
        print(f"   ✅ Criado ID {gid:>6} → {name}")

    # ─── Resumo Final ───────────────────────────────────────
    print()
    banner("📋 RESUMO")
    print(f"  ✅ Criados:    {len(created):>3}")
    print(f"  ⏭️  Já existiam: {len(skipped):>3}")
    print(f"  📦 Total:      {len(HOSTGROUPS):>3}")

    # Consolidar IDs (criados + existentes) para próxima fase
    all_groups = {**existing_names, **created}

    print()
    banner("🎯 ESTRUTURA FINAL DE GRUPOS")
    for name in HOSTGROUPS:
        gid = all_groups.get(name, "?")
        indent = "  " * name.count("/")
        leaf = name.split("/")[-1] if "/" in name else name
        marker = "📁" if name in ("Lojas", "CFTV") else "└─"
        print(f"  {indent}{marker} {leaf:<30} (ID {gid})")

    # Salvar mapping pra próxima fase usar
    output_file = Path("/opt/it-gov-dashboard/cftv-setup/.hostgroups_ids.json")
    output_file.write_text(json.dumps(all_groups, indent=2, ensure_ascii=False))
    print(f"\n  💾 IDs salvos em: {output_file}")

    # IDs específicos que a Fase 3 vai usar
    print()
    banner("🔑 IDs CRÍTICOS PARA FASE 3")
    critical = [
        "Lojas/Centro/CFTV/NVR",
        "Lojas/Centro/CFTV/Cameras",
        "CFTV/NVRs",
        "CFTV/Cameras",
    ]
    for name in critical:
        gid = all_groups.get(name)
        print(f"  • {name:<35} → ID {gid}")

    # Logout (boa prática)
    zabbix_api(url, "user.logout", [], auth_token)

    print()
    banner("✅ FASE 2 CONCLUÍDA — Pronto para Fase 3", char="═")
    print("""
  📌 PRÓXIMO PASSO:
  
     # Confirmar senha do NVR (preserva entre sudo):
     read -rs DVR_PASSWORD && export DVR_PASSWORD
     
     # Rodar Fase 3 (criar NVR + 18 câmeras):
     sudo -E -u zabbix python3 \\
       /opt/it-gov-dashboard/cftv-setup/08_create_nvr_and_cameras.py
""")


if __name__ == "__main__":
    main()
