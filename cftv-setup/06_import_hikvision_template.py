#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════════
  Import Hikvision Template — Zabbix 7.0.26
═══════════════════════════════════════════════════════════════════
  Importa o template oficial 'Hikvision camera by HTTP' já baixado
  em /tmp/template_cctv_hikvision.yaml para o Zabbix.

  Retorna os IDs dos templates Hikvision para uso nas fases seguintes.

  Uso:
      sudo -u zabbix python3 06_import_hikvision_template.py

  Projeto: Dashboard de Governança de TI — Grupo Gadens
  Autor:   Roberto + Claude Opus 4.7
  Data:    2026-05-19
═══════════════════════════════════════════════════════════════════
"""

import contextlib
import sys
from pathlib import Path

sys.path.insert(0, "/opt/it-gov-dashboard")

try:
    import requests

    import config
except ImportError as e:
    sys.exit(f"❌ Falha ao importar dependências: {e}\n   Verifique se /opt/it-gov-dashboard/config.py existe.")

# ══════════════════════════════════════════════════════════════════
# CONFIGURAÇÕES
# ══════════════════════════════════════════════════════════════════
TEMPLATE_FILE = "/tmp/template_cctv_hikvision.yaml"
ZBX_URL = config.ZABBIX_URL
ZBX_USER = config.ZABBIX_USER
ZBX_PASS = config.ZABBIX_PASSWORD


# Cores ANSI para output amigável
class C:
    OK = "\033[92m"
    WARN = "\033[93m"
    ERR = "\033[91m"
    INFO = "\033[96m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    END = "\033[0m"


# ══════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════
def banner(text, char="═"):
    """Imprime um banner formatado."""
    line = char * 64
    print(f"\n{C.BOLD}{line}")
    print(f"  {text}")
    print(f"{line}{C.END}")


def step(num, text):
    """Imprime uma etapa do processo."""
    print(f"\n{C.INFO}[{num}]{C.END} {C.BOLD}{text}{C.END}")


def success(text):
    print(f"   {C.OK}✅ {text}{C.END}")


def warn(text):
    print(f"   {C.WARN}⚠️  {text}{C.END}")


def error(text):
    print(f"   {C.ERR}❌ {text}{C.END}")


def call(method, params, auth=None, timeout=60):
    """Wrapper genérico para chamadas Zabbix API."""
    body = {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}
    if auth:
        body["auth"] = auth

    try:
        response = requests.post(ZBX_URL, json=body, timeout=timeout)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Falha na conexão com Zabbix: {e}") from e

    data = response.json()
    if "error" in data:
        raise RuntimeError(
            f"API erro em {method}: {data['error'].get('message', '')} | {data['error'].get('data', '')}"
        )
    return data["result"]


# ══════════════════════════════════════════════════════════════════
# VALIDAÇÕES PRÉ-EXECUÇÃO
# ══════════════════════════════════════════════════════════════════
def preflight():
    """Valida arquivo, credenciais e conectividade antes de começar."""
    step("1/5", "Validações pré-execução")

    # 1.1 — Arquivo existe?
    path = Path(TEMPLATE_FILE)
    if not path.exists():
        error(f"Arquivo não encontrado: {TEMPLATE_FILE}")
        print(f"\n   {C.DIM}Solução: baixe o template antes de rodar:{C.END}")
        print(f"   {C.DIM}wget -O {TEMPLATE_FILE} \\{C.END}")
        print(
            f"   {C.DIM}  'https://git.zabbix.com/projects/ZBX/repos/zabbix/raw/"
            f"templates/cctv/hikvision/template_cctv_hikvision.yaml?at=release/7.0'{C.END}"
        )
        sys.exit(1)

    size = path.stat().st_size
    if size < 40000:
        error(f"Arquivo muito pequeno ({size} bytes) — pode estar corrompido")
        sys.exit(1)
    success(f"Arquivo OK: {TEMPLATE_FILE} ({size:,} bytes)")

    # 1.2 — Config carregada?
    if not all([ZBX_URL, ZBX_USER, ZBX_PASS]):
        error("Credenciais Zabbix não encontradas em config.py")
        sys.exit(1)
    success(f"Config carregada: {ZBX_URL}")

    # 1.3 — Zabbix acessível?
    try:
        version = call("apiinfo.version", [])
        success(f"Zabbix API responde: v{version}")
    except Exception as e:
        error(f"Zabbix inacessível: {e}")
        sys.exit(1)


# ══════════════════════════════════════════════════════════════════
# AUTENTICAÇÃO
# ══════════════════════════════════════════════════════════════════
def authenticate():
    step("2/5", "Autenticação no Zabbix")
    try:
        token = call("user.login", {"username": ZBX_USER, "password": ZBX_PASS})
        success(f"Login OK como '{ZBX_USER}'")
        return token
    except Exception as e:
        error(f"Falha no login: {e}")
        sys.exit(1)


# ══════════════════════════════════════════════════════════════════
# CHECAR SE JÁ EXISTE
# ══════════════════════════════════════════════════════════════════
def check_existing(token):
    step("3/5", "Verificando se template já foi importado")

    existing = call(
        "template.get",
        {"output": ["templateid", "host", "name"], "search": {"host": "Hikvision"}},
        auth=token,
    )

    if existing:
        warn(f"Encontrados {len(existing)} template(s) Hikvision já no Zabbix:")
        for t in existing:
            print(f"      • ID {t['templateid']:>6} → {t['host']}")
        print(f"\n   {C.DIM}O import vai ATUALIZAR esses templates (não duplica).{C.END}")
        return existing
    else:
        success("Nenhum template Hikvision encontrado — primeira importação")
        return []


# ══════════════════════════════════════════════════════════════════
# IMPORTAÇÃO
# ══════════════════════════════════════════════════════════════════
def import_template(token):
    step("4/5", "Importando template no Zabbix")

    with open(TEMPLATE_FILE, encoding="utf-8") as f:
        yaml_content = f.read()

    print(f"   {C.DIM}Enviando {len(yaml_content):,} bytes para API...{C.END}")

    rules = {
        "template_groups": {"createMissing": True},
        "host_groups": {"createMissing": True},
        "templates": {"createMissing": True, "updateExisting": True},
        "items": {"createMissing": True, "updateExisting": True},
        "triggers": {"createMissing": True, "updateExisting": True},
        "graphs": {"createMissing": True, "updateExisting": True},
        "discoveryRules": {"createMissing": True, "updateExisting": True},
        "valueMaps": {"createMissing": True, "updateExisting": True},
    }

    try:
        call(
            "configuration.import",
            {"format": "yaml", "rules": rules, "source": yaml_content},
            auth=token,
            timeout=120,
        )
        success("Importação concluída com sucesso!")
    except Exception as e:
        error(f"Falha no import: {e}")
        sys.exit(1)


# ══════════════════════════════════════════════════════════════════
# RECUPERAR IDs
# ══════════════════════════════════════════════════════════════════
def get_template_ids(token):
    step("5/5", "Recuperando IDs dos templates criados")

    templates = call(
        "template.get",
        {
            "output": ["templateid", "host", "name", "description"],
            "search": {"host": "Hikvision"},
            "selectItems": "count",
            "selectTriggers": "count",
            "selectDiscoveries": "count",
        },
        auth=token,
    )

    if not templates:
        error("Nenhum template Hikvision encontrado após import (estranho!)")
        sys.exit(1)

    main_template = None
    for t in templates:
        if t["host"] == "Hikvision camera by HTTP":
            main_template = t
            break

    banner("📋 TEMPLATES HIKVISION INSTALADOS", char="═")

    for t in templates:
        is_main = t["host"] == "Hikvision camera by HTTP"
        marker = f"{C.OK}👉{C.END}" if is_main else "  "
        items = t.get("items", "0")
        trigs = t.get("triggers", "0")
        discs = t.get("discoveries", "0")

        print(f"\n  {marker} {C.BOLD}ID {t['templateid']}{C.END} — {t['host']}")
        print(f"      📊 Items: {items} | Triggers: {trigs} | Discovery: {discs}")
        if t.get("name") and t["name"] != t["host"]:
            print(f"      💬 {C.DIM}{t['name']}{C.END}")

    return main_template


# ══════════════════════════════════════════════════════════════════
# RESUMO FINAL
# ══════════════════════════════════════════════════════════════════
def print_summary(main_template):
    banner("✅ PROCESSO CONCLUÍDO", char="═")

    if not main_template:
        warn("Template principal 'Hikvision camera by HTTP' não localizado")
        warn("Use o ID manualmente baseado na lista acima")
        return

    tid = main_template["templateid"]

    print(f"\n  🎯 {C.BOLD}TEMPLATE PRINCIPAL:{C.END}")
    print(f"     ID:    {C.OK}{C.BOLD}{tid}{C.END}")
    print("     Nome:  Hikvision camera by HTTP")

    print(f"\n  📌 {C.BOLD}PRÓXIMOS PASSOS:{C.END}\n")
    print(f"     {C.DIM}# Salvar o ID para próxima fase:{C.END}")
    print(f"     {C.INFO}export HIKVISION_TEMPLATE_ID={tid}{C.END}\n")
    print(f"     {C.DIM}# Senha do NVR (não fica no histórico):{C.END}")
    print(f"     {C.INFO}read -rs DVR_PASSWORD && export DVR_PASSWORD{C.END}\n")
    print(f"     {C.DIM}# Rodar Fase 3 (criar NVR + 18 câmeras):{C.END}")
    print(f"     {C.INFO}sudo -E -u zabbix python3 \\{C.END}")
    print(f"     {C.INFO}  /opt/it-gov-dashboard/cftv-setup/08_create_nvr_and_cameras.py{C.END}")

    # Salvar ID em arquivo para referência
    id_file = "/opt/it-gov-dashboard/cftv-setup/.hikvision_template_id"
    try:
        with open(id_file, "w") as f:
            f.write(f"HIKVISION_TEMPLATE_ID={tid}\n")
        print(f"\n  💾 ID também salvo em: {C.DIM}{id_file}{C.END}")
    except Exception as e:
        warn(f"Não consegui salvar arquivo de referência: {e}")

    print()


# ══════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════
def main():
    banner("🎬 IMPORT HIKVISION TEMPLATE — Zabbix 7.0", char="═")
    print(f"  {C.DIM}Projeto: Dashboard de Governança de TI — Grupo Gadens{C.END}")

    try:
        preflight()
        token = authenticate()
        check_existing(token)
        import_template(token)
        main_template = get_template_ids(token)

        # Logout (best-effort, ignora falhas)
        with contextlib.suppress(Exception):
            call("user.logout", [], auth=token)

        print_summary(main_template)

    except KeyboardInterrupt:
        print(f"\n{C.WARN}⚠️  Interrompido pelo usuário{C.END}")
        sys.exit(130)
    except Exception as e:
        print(f"\n{C.ERR}❌ ERRO FATAL: {e}{C.END}")
        sys.exit(1)


if __name__ == "__main__":
    main()
