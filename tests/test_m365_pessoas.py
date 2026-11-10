"""Último usuário do computador (Acronis) cruzado com a conta do M365."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from itgov.services import m365_pessoas as mp

_USUARIOS = [
    {"userPrincipalName": "ana.souza@empresa.com.br", "displayName": "Ana Souza", "accountEnabled": True,
     "onPremisesSamAccountName": "ana.souza", "mail": "ana.souza@empresa.com.br"},
    # Saiu da empresa: conta desativada
    {"userPrincipalName": "beto.lima@empresa.com.br", "displayName": "Beto Lima", "accountEnabled": False},
    # UPN "carla@" é de outra pessoa; o sAMAccountName "carla" é desta
    {"userPrincipalName": "carla@empresa.com.br", "displayName": "Carla (recepção)", "accountEnabled": True},
    {"userPrincipalName": "carla.reis@empresa.com.br", "displayName": "Carla Reis", "accountEnabled": True,
     "onPremisesSamAccountName": "carla"},
    {"displayName": "sem UPN"},
]  # fmt: skip


@pytest.mark.parametrize(
    ("usuario", "login"),
    [
        ("Ana.Souza (GRUPOGADENS)", "ana.souza"),
        ("ana.souza@empresa.com.br (GRUPOGADENS.COM.BR)", "ana.souza"),
        ("GRUPOGADENS\\ana.souza", "ana.souza"),
        ("ana.souza", "ana.souza"),
        ("", ""),
    ],
)
def test_login_do_acronis(usuario: str, login: str) -> None:
    assert mp.login_do_acronis(usuario) == login


def test_indexar_prefere_sam_ao_upn() -> None:
    pessoas = mp.indexar(_USUARIOS)
    ana = pessoas.de("ana.souza (GRUPOGADENS)")
    assert ana is not None and (ana.nome, ana.email, ana.ativa) == ("Ana Souza", "ana.souza@empresa.com.br", True)
    beto = pessoas.de("Beto.Lima (GRUPOGADENS)")
    assert beto is not None and not beto.ativa and beto.email == "beto.lima@empresa.com.br"
    carla = pessoas.de("carla (GRUPOGADENS)")
    assert carla is not None and carla.nome == "Carla Reis"
    assert pessoas.de("ninguem (DESKTOP-1)") is None and pessoas.de("") is None


def test_link_dos_chamados(monkeypatch: pytest.MonkeyPatch) -> None:
    pessoa = mp.Pessoa(nome="Ana", email="ana.souza@empresa.com.br", ativa=True)
    monkeypatch.delenv("ZENDESK_SUBDOMAIN", raising=False)
    assert pessoa.chamados_url == ""
    monkeypatch.setenv("ZENDESK_SUBDOMAIN", "acme")
    assert pessoa.chamados_url == "https://acme.zendesk.com/agent/search/1?q=requester%3Aana.souza%40empresa.com.br"


def test_obter_sem_graph_configurado() -> None:
    with patch.object(mp, "_get_credentials", return_value=("", "", "")):
        assert not mp.configurado() and mp.obter() is None


def test_obter_nao_bloqueia_na_primeira_leitura() -> None:
    with (
        patch.object(mp, "_get_credentials", return_value=("t", "c", "s")),
        patch.object(mp, "_cache") as cache,
    ):
        cache.carregado = False
        assert mp.obter() is None
        cache.aquecer.assert_called_once()


def test_carregar_tolera_falha_do_graph() -> None:
    with patch.object(mp, "_buscar", side_effect=mp.GraphAuthError("HTTP 400")):
        assert mp._carregar() is None
