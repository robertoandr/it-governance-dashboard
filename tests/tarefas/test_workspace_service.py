"""Casos de uso de workspaces: criação com board padrão, nome único, soft delete."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from flask import Flask
from pydantic import ValidationError

from app.extensions import db
from app.models.tarefas import BOARD_PADRAO, Board, Workspace
from app.services.tarefas import workspace_service as svc


def _in(nome: str) -> svc.WorkspaceIn:
    return svc.WorkspaceIn(name=nome)


@pytest.fixture
def admin_id(usuario_id: Callable[..., int]) -> int:
    return usuario_id("admin")


def test_criar_cria_board_padrao(factory_app: Flask, admin_id: int) -> None:
    with factory_app.app_context():
        ws = svc.criar(_in("Infraestrutura"), user_id=admin_id)
        assert ws.id is not None
        assert ws.created_by == admin_id
        assert [b.name for b in ws.boards_ativos] == [BOARD_PADRAO]
        assert ws.boards_ativos[0].revision == 0


def test_nome_e_normalizado_com_strip() -> None:
    assert _in("  Redes  ").name == "Redes"


@pytest.mark.parametrize("nome", ["ab", "   ab   ", "x" * 61, ""])
def test_nome_fora_dos_limites_e_rejeitado(nome: str) -> None:
    with pytest.raises(ValidationError):
        _in(nome)


def test_campo_extra_e_rejeitado() -> None:
    with pytest.raises(ValidationError):
        svc.WorkspaceIn.model_validate({"name": "Redes", "owner": 1})


def test_nome_duplicado_ignora_maiusculas(factory_app: Flask, admin_id: int) -> None:
    with factory_app.app_context():
        svc.criar(_in("CFTV"), user_id=admin_id)
        with pytest.raises(svc.WorkspaceDuplicadoError):
            svc.criar(_in("cftv"), user_id=admin_id)


def test_indice_unico_cobre_corrida_entre_criacoes(
    factory_app: Flask, admin_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Se duas criações passam pela checagem prévia, o índice único barra a segunda."""
    with factory_app.app_context():
        svc.criar(_in("Diretoria"), user_id=admin_id)
        monkeypatch.setattr(svc, "_nome_em_uso", lambda *_a, **_k: False)
        with pytest.raises(svc.WorkspaceDuplicadoError):
            svc.criar(_in("DIRETORIA"), user_id=admin_id)
        # a sessão continua utilizável depois do rollback
        assert svc.listar()[1] == 1


def test_listar_ordena_por_nome_e_pagina(factory_app: Flask, admin_id: int) -> None:
    with factory_app.app_context():
        for nome in ("Redes", "cftv", "Infra"):
            svc.criar(_in(nome), user_id=admin_id)
        itens, total = svc.listar()
        assert total == 3
        assert [w.name for w in itens] == ["cftv", "Infra", "Redes"]
        pagina, total = svc.listar(limit=1, offset=1)
        assert total == 3
        assert [w.name for w in pagina] == ["Infra"]


def test_listar_limita_parametros(factory_app: Flask, admin_id: int) -> None:
    with factory_app.app_context():
        svc.criar(_in("Infra"), user_id=admin_id)
        itens, _ = svc.listar(limit=0, offset=-5)
        assert len(itens) == 1


def test_renomear(factory_app: Flask, admin_id: int) -> None:
    with factory_app.app_context():
        ws = svc.criar(_in("Infra"), user_id=admin_id)
        atualizado = svc.renomear(ws.id, _in("Infraestrutura"), user_id=admin_id)
        assert atualizado.name == "Infraestrutura"


def test_renomear_para_o_proprio_nome_com_outra_caixa(factory_app: Flask, admin_id: int) -> None:
    with factory_app.app_context():
        ws = svc.criar(_in("Infra"), user_id=admin_id)
        assert svc.renomear(ws.id, _in("INFRA"), user_id=admin_id).name == "INFRA"


def test_renomear_para_nome_de_outro_workspace(factory_app: Flask, admin_id: int) -> None:
    with factory_app.app_context():
        svc.criar(_in("Infra"), user_id=admin_id)
        redes = svc.criar(_in("Redes"), user_id=admin_id)
        with pytest.raises(svc.WorkspaceDuplicadoError):
            svc.renomear(redes.id, _in("infra"), user_id=admin_id)


def test_renomear_inexistente(factory_app: Flask, admin_id: int) -> None:
    with factory_app.app_context(), pytest.raises(svc.WorkspaceNaoEncontradoError):
        svc.renomear(999_999, _in("Qualquer"), user_id=admin_id)


def test_excluir_e_soft_delete_com_boards(factory_app: Flask, admin_id: int) -> None:
    with factory_app.app_context():
        ws = svc.criar(_in("Infra"), user_id=admin_id)
        board_id = ws.boards_ativos[0].id
        svc.excluir(ws.id, user_id=admin_id)

        assert svc.listar() == ([], 0)
        guardado = db.session.get(Workspace, ws.id)
        assert guardado is not None and guardado.deleted_at is not None
        board = db.session.get(Board, board_id)
        assert board is not None and board.deleted_at is not None


def test_excluir_duas_vezes_da_nao_encontrado(factory_app: Flask, admin_id: int) -> None:
    with factory_app.app_context():
        ws = svc.criar(_in("Infra"), user_id=admin_id)
        svc.excluir(ws.id, user_id=admin_id)
        with pytest.raises(svc.WorkspaceNaoEncontradoError):
            svc.excluir(ws.id, user_id=admin_id)


def test_nome_de_workspace_excluido_pode_ser_reusado(factory_app: Flask, admin_id: int) -> None:
    with factory_app.app_context():
        ws = svc.criar(_in("Infra"), user_id=admin_id)
        svc.excluir(ws.id, user_id=admin_id)
        novo = svc.criar(_in("Infra"), user_id=admin_id)
        assert novo.id != ws.id
