#!/usr/bin/env python3
"""
Cria/atualiza (idempotente) as 3 políticas de Conditional Access que substituem
os Security Defaults do tenant grupogadens.com.br:

  1. "GG - Require MFA for All Users"     — MFA para todos, exclui conta de emergência
  2. "GG - Block Legacy Authentication"   — bloqueia Exchange ActiveSync + Other clients
  3. "GG - Require MFA for Admins"        — MFA reforçado para roles privilegiadas

Todas nascem em `enabledForReportingButNotEnforced` (report-only) — nenhuma
delas bloqueia ou exige nada até serem promovidas para `enabled` manualmente
(ver docs/CA_MIGRATION_CHECKLIST.md). Isso é intencional: elas precisam poder
conviver com os Security Defaults ainda ativos.

SEGURANÇA — modo dry-run por padrão:
  Por padrão o script só GET as políticas existentes e IMPRIME o que faria —
  não cria nem altera nada. É necessário passar --apply explicitamente para
  de fato chamar POST/PATCH contra o tenant real. Isso existe porque:
    (a) políticas de acesso condicional são difíceis de reverter às cegas
        (uma política malformada pode travar login mesmo em report-only, se
        for promovida sem revisão), e
    (b) o pedido original foi "criar o script, não executar ainda".

Uso:
    python3 infra-setup/06_create_ca_policies.py            # dry-run (padrão)
    python3 infra-setup/06_create_ca_policies.py --apply    # cria/atualiza de verdade

Pré-requisitos:
  - AZURE_TENANT_ID / AZURE_CLIENT_ID / AZURE_CLIENT_SECRET no .env
  - App Registration com a permissão de aplicação
    Policy.ReadWrite.ConditionalAccess (Application) + admin consent
    (Policy.Read.All sozinho só permite o dry-run / listagem)
  - EMERGENCY_ACCOUNT_UPN no .env (default: flavio2022@grupogadens.onmicrosoft.com)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import requests

try:
    import msal
except ImportError:
    sys.exit("Instale: pip install msal --break-system-packages")

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

AZURE_TENANT_ID = os.environ.get("AZURE_TENANT_ID", "")
AZURE_CLIENT_ID = os.environ.get("AZURE_CLIENT_ID", "")
AZURE_CLIENT_SECRET = os.environ.get("AZURE_CLIENT_SECRET", "")
# ATENÇÃO: o UPN "flavio2022@grupogadens.onmicrosoft.com" citado no pedido
# original NÃO existe no tenant (Graph retorna 404) — confirmado via dry-run
# em 2026-07-10. O único usuário "flavio*" real é flavio@grupogadens.com.br
# (Flávio Mendonça de Araújo). Usando esse como default; CONFIRME com Roberto
# que esta é de fato a conta de emergência antes de rodar --apply.
EMERGENCY_ACCOUNT_UPN = os.environ.get("EMERGENCY_ACCOUNT_UPN", "flavio@grupogadens.com.br")

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPES = ["https://graph.microsoft.com/.default"]

# Role template IDs — GUIDs fixos e globais do Entra ID (mesmos em qualquer
# tenant), NÃO são específicos do grupogadens. Fonte: documentação Microsoft
# "Microsoft Entra built-in roles". Confirme contra o Portal antes de rodar
# --apply, caso a Microsoft os altere no futuro.
ROLE_TEMPLATE_IDS = {
    "Global Administrator": "62e90394-69f5-4237-9190-012177145e10",
    "Security Administrator": "194ae4cb-b126-40b2-bd5b-6091b380977d",
    "Exchange Administrator": "29232cdf-9323-42fd-ade2-1d097af3e4de",
    "SharePoint Administrator": "f28a1f50-f6e7-4571-818b-6a12f2af6b6c",
    "User Administrator": "fe930be7-5e62-47db-91af-98c3a49a38b1",
    "Helpdesk Administrator": "729827e3-9c14-49f7-bb1b-9608f156bbb8",
}

REQUIRED_ROLE = "Policy.ReadWrite.ConditionalAccess"


def banner(text: str) -> None:
    print(f"\n{'=' * 60}\n  {text}\n{'=' * 60}")


def ok(msg: str) -> None:
    print(f"  [OK] {msg}")


def info(msg: str) -> None:
    print(f"  [--] {msg}")


def warn(msg: str) -> None:
    print(f"  [!!] {msg}")


# ── Auth ───────────────────────────────────────────────────────────────────


def get_token() -> str:
    app = msal.ConfidentialClientApplication(
        AZURE_CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{AZURE_TENANT_ID}",
        client_credential=AZURE_CLIENT_SECRET,
    )
    result = app.acquire_token_for_client(scopes=GRAPH_SCOPES)
    if "access_token" not in result:
        error = result.get("error_description", result.get("error", "unknown"))
        sys.exit(f"  [ERRO] Falha ao obter token: {error}")
    return result["access_token"]


def check_write_permission(token: str) -> bool:
    """Decodifica as roles do token (sem validar assinatura — só leitura local)
    e confere se REQUIRED_ROLE está presente."""
    import base64
    import json

    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    decoded = json.loads(base64.b64decode(payload))
    roles = decoded.get("roles", [])
    return REQUIRED_ROLE in roles


# ── Graph helpers ────────────────────────────────────────────────────────────


def graph_get(token: str, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    r = requests.get(f"{GRAPH_BASE}{path}", headers={"Authorization": f"Bearer {token}"}, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def graph_post(token: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
    r = requests.post(f"{GRAPH_BASE}{path}", headers={"Authorization": f"Bearer {token}"}, json=body, timeout=30)
    r.raise_for_status()
    return r.json()


def graph_patch(token: str, path: str, body: dict[str, Any]) -> None:
    r = requests.patch(f"{GRAPH_BASE}{path}", headers={"Authorization": f"Bearer {token}"}, json=body, timeout=30)
    r.raise_for_status()


def resolve_user_id(token: str, upn: str) -> str:
    data = graph_get(token, f"/users/{upn}", params={"$select": "id,userPrincipalName"})
    return data["id"]


def list_ca_policies(token: str) -> list[dict[str, Any]]:
    return graph_get(token, "/identity/conditionalAccess/policies").get("value", [])


# ── Definições das 3 políticas ────────────────────────────────────────────────


def build_policy_mfa_all_users(exclude_user_id: str) -> dict[str, Any]:
    return {
        "displayName": "GG - Require MFA for All Users",
        "state": "enabledForReportingButNotEnforced",
        "conditions": {
            "applications": {"includeApplications": ["All"]},
            "users": {
                "includeUsers": ["All"],
                "excludeUsers": [exclude_user_id],
            },
            "clientAppTypes": ["all"],
        },
        "grantControls": {
            "operator": "OR",
            "builtInControls": ["mfa"],
        },
    }


def build_policy_block_legacy_auth() -> dict[str, Any]:
    return {
        "displayName": "GG - Block Legacy Authentication",
        "state": "enabledForReportingButNotEnforced",
        "conditions": {
            "applications": {"includeApplications": ["All"]},
            "users": {"includeUsers": ["All"]},
            "clientAppTypes": ["exchangeActiveSync", "other"],
        },
        "grantControls": {
            "operator": "OR",
            "builtInControls": ["block"],
        },
    }


def build_policy_mfa_admins() -> dict[str, Any]:
    return {
        "displayName": "GG - Require MFA for Admins",
        "state": "enabledForReportingButNotEnforced",
        "conditions": {
            "applications": {"includeApplications": ["All"]},
            "users": {"includeRoles": list(ROLE_TEMPLATE_IDS.values())},
            "clientAppTypes": ["all"],
        },
        "grantControls": {
            "operator": "OR",
            "builtInControls": ["mfa"],
        },
    }


# ── Idempotência ───────────────────────────────────────────────────────────


def ensure_policy(
    token: str,
    definition: dict[str, Any],
    existing_policies: list[dict[str, Any]],
    apply: bool,
) -> tuple[str, str | None]:
    """Retorna (ação, id). ação em {"criaria", "atualizaria", "criada", "atualizada"}."""
    name = definition["displayName"]
    match = next((p for p in existing_policies if p.get("displayName") == name), None)

    if not apply:
        return ("atualizaria" if match else "criaria"), (match["id"] if match else None)

    if match:
        graph_patch(token, f"/identity/conditionalAccess/policies/{match['id']}", definition)
        return "atualizada", match["id"]

    created = graph_post(token, "/identity/conditionalAccess/policies", definition)
    return "criada", created["id"]


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    apply = "--apply" in sys.argv[1:]

    banner("Conditional Access — GG policies (substituem Security Defaults)")

    if not (AZURE_TENANT_ID and AZURE_CLIENT_ID and AZURE_CLIENT_SECRET):
        sys.exit("  [ERRO] AZURE_TENANT_ID / AZURE_CLIENT_ID / AZURE_CLIENT_SECRET não configurados no .env")

    if apply:
        warn("Modo --apply: vai CRIAR/ATUALIZAR políticas reais no tenant.")
    else:
        info("Modo dry-run (padrão) — nada será escrito. Use --apply para executar de verdade.")

    token = get_token()
    ok("Token Graph obtido")

    if not check_write_permission(token):
        warn(f"Permissão de aplicação '{REQUIRED_ROLE}' NÃO encontrada no token.")
        print(
            "\n  Para conceder, no Portal Entra ID:\n"
            "    App registrations → governanca-ti-m365 → API permissions →\n"
            "    Add a permission → Microsoft Graph → Application permissions →\n"
            f"    {REQUIRED_ROLE} → Add permissions → Grant admin consent for grupogadens\n"
        )
        if apply:
            sys.exit("  [ERRO] Abortando --apply: sem permissão de escrita em Conditional Access.")
        info("Prosseguindo em dry-run mesmo assim (só leitura é necessária para simular).")
    else:
        ok(f"Permissão '{REQUIRED_ROLE}' presente")

    banner("Resolvendo conta de emergência")
    try:
        exclude_user_id = resolve_user_id(token, EMERGENCY_ACCOUNT_UPN)
        ok(f"{EMERGENCY_ACCOUNT_UPN} → {exclude_user_id}")
    except requests.HTTPError as exc:
        sys.exit(f"  [ERRO] Não foi possível resolver a conta de emergência {EMERGENCY_ACCOUNT_UPN}: {exc}")

    banner("Políticas existentes no tenant")
    existing = list_ca_policies(token)
    info(f"{len(existing)} política(s) encontrada(s) hoje")
    for p in existing:
        info(f"  - {p.get('displayName')}: state={p.get('state')}")

    definitions = [
        build_policy_mfa_all_users(exclude_user_id),
        build_policy_block_legacy_auth(),
        build_policy_mfa_admins(),
    ]

    banner("Aplicando as 3 políticas GG (report-only)")
    for definition in definitions:
        action, policy_id = ensure_policy(token, definition, existing, apply)
        suffix = f" (ID {policy_id})" if policy_id else ""
        ok(f"{definition['displayName']}: {action}{suffix}")

    banner("CONCLUÍDO" if apply else "DRY-RUN CONCLUÍDO — nada foi alterado")
    if not apply:
        print("  Rode novamente com --apply para criar/atualizar de verdade.\n")


if __name__ == "__main__":
    main()
