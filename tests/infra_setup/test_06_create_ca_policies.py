"""Tests for infra-setup/06_create_ca_policies.py.

O módulo tem nome numérico (06_...), então é carregado via importlib, como o
conftest.py já faz para app.py. Variáveis AZURE_* são pré-setadas em
os.environ ANTES do import — o script só copia do .env quando a var ainda não
existe em os.environ (setdefault-style), então isso isola o teste do .env
real do projeto.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

os.environ["AZURE_TENANT_ID"] = "test-tenant-id"
os.environ["AZURE_CLIENT_ID"] = "test-client-id"
os.environ["AZURE_CLIENT_SECRET"] = "test-client-secret"
os.environ["EMERGENCY_ACCOUNT_UPN"] = "emergencia@test.onmicrosoft.com"

_MODULE_PATH = Path(__file__).resolve().parent.parent.parent / "infra-setup" / "06_create_ca_policies.py"
_spec = importlib.util.spec_from_file_location("ca_policies_script", _MODULE_PATH)
ca = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ca)  # type: ignore[union-attr]


def _fake_jwt(roles: list[str]) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"roles": roles}).encode()).decode().rstrip("=")
    return f"header.{payload}.signature"


# ── build_policy_* — estrutura obrigatória ────────────────────────────────────


def test_todas_as_policies_nascem_em_report_only():
    policies = [
        ca.build_policy_mfa_all_users("user-id-123"),
        ca.build_policy_block_legacy_auth(),
        ca.build_policy_mfa_admins(),
    ]
    for p in policies:
        assert p["state"] == "enabledForReportingButNotEnforced"
        assert "displayName" in p
        assert "conditions" in p
        assert "grantControls" in p


def test_policy_mfa_all_users_exclui_conta_emergencia():
    p = ca.build_policy_mfa_all_users("emergency-object-id")
    assert p["conditions"]["users"]["includeUsers"] == ["All"]
    assert p["conditions"]["users"]["excludeUsers"] == ["emergency-object-id"]
    assert p["grantControls"]["builtInControls"] == ["mfa"]


def test_policy_block_legacy_auth_mira_client_app_types_corretos():
    p = ca.build_policy_block_legacy_auth()
    assert set(p["conditions"]["clientAppTypes"]) == {"exchangeActiveSync", "other"}
    assert p["grantControls"]["builtInControls"] == ["block"]


def test_policy_mfa_admins_inclui_todas_as_roles_privilegiadas():
    p = ca.build_policy_mfa_admins()
    included = set(p["conditions"]["users"]["includeRoles"])
    assert included == set(ca.ROLE_TEMPLATE_IDS.values())
    assert len(ca.ROLE_TEMPLATE_IDS) == 6  # Global/Security/Exchange/SharePoint/User/Helpdesk Admin


# ── ensure_policy — idempotência ──────────────────────────────────────────────


def test_ensure_policy_dry_run_nao_chama_graph():
    definition = ca.build_policy_block_legacy_auth()

    with patch.object(ca, "graph_post") as mock_post, patch.object(ca, "graph_patch") as mock_patch:
        action, policy_id = ca.ensure_policy("tok", definition, existing_policies=[], apply=False)

    assert action == "criaria"
    assert policy_id is None
    mock_post.assert_not_called()
    mock_patch.assert_not_called()


def test_ensure_policy_apply_cria_quando_nao_existe():
    definition = ca.build_policy_block_legacy_auth()

    with patch.object(ca, "graph_post", return_value={"id": "new-id-1"}) as mock_post:
        action, policy_id = ca.ensure_policy("tok", definition, existing_policies=[], apply=True)

    assert action == "criada"
    assert policy_id == "new-id-1"
    mock_post.assert_called_once()


def test_ensure_policy_apply_atualiza_sem_duplicar_quando_ja_existe():
    """Regressão: rodar 2x não deve criar uma segunda política com o mesmo nome."""
    definition = ca.build_policy_block_legacy_auth()
    existing = [{"id": "existing-id-1", "displayName": "GG - Block Legacy Authentication"}]

    with (
        patch.object(ca, "graph_post") as mock_post,
        patch.object(ca, "graph_patch") as mock_patch,
    ):
        action, policy_id = ca.ensure_policy("tok", definition, existing_policies=existing, apply=True)

    assert action == "atualizada"
    assert policy_id == "existing-id-1"
    mock_post.assert_not_called()
    mock_patch.assert_called_once()


def test_ensure_policy_rodar_duas_vezes_nao_duplica():
    """Simula duas execuções sequenciais do script inteiro contra o mesmo estado."""
    definition = ca.build_policy_mfa_all_users("user-id-123")

    # 1ª execução: não existe ainda
    with patch.object(ca, "graph_post", return_value={"id": "created-id"}) as mock_post:
        action1, id1 = ca.ensure_policy("tok", definition, existing_policies=[], apply=True)
    assert action1 == "criada"
    mock_post.assert_called_once()

    # 2ª execução: a política já existe (como ficaria após a 1ª rodada)
    existing_after_first_run = [{"id": id1, "displayName": definition["displayName"]}]
    with (
        patch.object(ca, "graph_post") as mock_post_2,
        patch.object(ca, "graph_patch") as mock_patch_2,
    ):
        action2, id2 = ca.ensure_policy("tok", definition, existing_policies=existing_after_first_run, apply=True)

    assert action2 == "atualizada"
    assert id2 == id1
    mock_post_2.assert_not_called()  # nenhuma política nova criada na 2ª rodada
    mock_patch_2.assert_called_once()  # política existente foi atualizada


# ── check_write_permission ────────────────────────────────────────────────────


def test_check_write_permission_true_quando_role_presente():
    token = _fake_jwt(["Policy.ReadWrite.ConditionalAccess", "User.Read.All"])
    assert ca.check_write_permission(token) is True


def test_check_write_permission_false_quando_role_ausente():
    token = _fake_jwt(["Policy.Read.All", "User.Read.All"])
    assert ca.check_write_permission(token) is False


# ── main() — dry-run nunca escreve ────────────────────────────────────────────


def test_main_sem_apply_nunca_chama_post_ou_patch(monkeypatch, capsys):
    monkeypatch.setattr(ca.sys, "argv", ["06_create_ca_policies.py"])  # sem --apply

    mock_token_response = MagicMock()
    mock_token_response.status_code = 200
    mock_token_response.json.return_value = {"id": "emergency-object-id"}
    mock_token_response.raise_for_status.return_value = None

    with (
        patch.object(ca, "get_token", return_value=_fake_jwt(["Policy.Read.All"])),
        patch.object(ca, "resolve_user_id", return_value="emergency-object-id"),
        patch.object(ca, "list_ca_policies", return_value=[]),
        patch.object(ca, "graph_post") as mock_post,
        patch.object(ca, "graph_patch") as mock_patch,
    ):
        ca.main()

    mock_post.assert_not_called()
    mock_patch.assert_not_called()
    out = capsys.readouterr().out
    assert "DRY-RUN" in out


def test_main_sem_credenciais_aborta(monkeypatch):
    monkeypatch.setattr(ca, "AZURE_TENANT_ID", "")
    with pytest.raises(SystemExit):
        ca.main()
    monkeypatch.setattr(ca, "AZURE_TENANT_ID", "test-tenant-id")
