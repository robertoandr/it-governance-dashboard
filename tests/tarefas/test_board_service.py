"""Board Kanban: criação no topo, movimentação com vizinhos, renumeração, conflitos e revisão."""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from flask import Flask
from pydantic import ValidationError
from sqlalchemy import update

from app.extensions import db
from app.models.tarefas import Board, Card, CardActivity, CardComment, CardDocument
from app.services.tarefas import board_service as svc
from app.services.tarefas import workspace_service as ws_svc


@pytest.fixture
def ctx(factory_app: Flask) -> Iterator[None]:
    with factory_app.app_context():
        yield


@pytest.fixture
def admin_id(usuario_id: Callable[..., int]) -> int:
    return usuario_id("admin")


@pytest.fixture
def board_id(ctx: None, admin_id: int) -> int:
    ws = ws_svc.criar(ws_svc.WorkspaceIn(name="Infraestrutura"), user_id=admin_id)
    return ws.boards_ativos[0].id


def _criar(board_id: int, user_id: int, titulo: str, status: str = "backlog") -> Card:
    return svc.criar_card(board_id, svc.CardIn(title=titulo, status=status), user_id=user_id)


def _titulos(board_id: int, status: str) -> list[str]:
    return [c["title"] for c in svc.listar_cards(board_id)[1][status]]


def _mover(card: Card, status: str, user_id: int, before: Card | None = None, after: Card | None = None) -> Card:
    db.session.refresh(card)
    dados = svc.MoverIn(
        status=status,
        before_id=before.id if before else None,
        after_id=after.id if after else None,
        version=card.version,
    )
    return svc.mover_card(card.id, dados, user_id=user_id).card


# ── Validação de entrada ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "payload", [{"title": ""}, {"title": "   "}, {"title": "x" * 201}, {"title": "ok", "status": "feito"}]
)
def test_card_in_invalido(payload: dict) -> None:
    with pytest.raises(ValidationError):
        svc.CardIn.model_validate(payload)


def test_mover_in_exige_versao_e_status_valido() -> None:
    with pytest.raises(ValidationError):
        svc.MoverIn.model_validate({"status": "doing"})
    with pytest.raises(ValidationError):
        svc.MoverIn.model_validate({"status": "arquivado", "version": 1})


# ── Criação e listagem ───────────────────────────────────────────────────────


def test_criar_poe_card_no_topo_e_registra_historico(board_id: int, admin_id: int) -> None:
    primeiro = _criar(board_id, admin_id, "Primeiro")
    _criar(board_id, admin_id, "Segundo")
    assert _titulos(board_id, "backlog") == ["Segundo", "Primeiro"]
    atividade = CardActivity.query.filter_by(card_id=primeiro.id).one()
    assert (atividade.action, atividade.to_status, atividade.actor_id) == ("created", "backlog", admin_id)


def test_criar_em_outra_coluna(board_id: int, admin_id: int) -> None:
    _criar(board_id, admin_id, "Em andamento", status="doing")
    _, colunas = svc.listar_cards(board_id)
    assert list(colunas) == ["backlog", "todo", "doing", "done"]
    assert [c["title"] for c in colunas["doing"]] == ["Em andamento"]


def test_listar_traz_responsavel_documento_e_comentarios(board_id: int, admin_id: int) -> None:
    card = _criar(board_id, admin_id, "Com extras")
    vazio = _criar(board_id, admin_id, "Documento vazio")
    card.assignee_id = admin_id
    db.session.add_all(
        [
            CardDocument(card_id=card.id, content_md="## Decisão", updated_by=admin_id),
            CardDocument(card_id=vazio.id, content_md="", updated_by=admin_id),
            CardComment(card_id=card.id, author_id=admin_id, body_md="ok"),
            CardComment(card_id=card.id, author_id=admin_id, body_md="ok 2"),
        ]
    )
    db.session.commit()
    por_titulo = {c["title"]: c for c in svc.listar_cards(board_id)[1]["backlog"]}
    assert por_titulo["Com extras"]["assignee"] == {"id": admin_id, "name": "Pytest admin"}
    assert por_titulo["Com extras"]["has_document"] is True
    assert por_titulo["Com extras"]["comment_count"] == 2
    assert por_titulo["Documento vazio"]["has_document"] is False
    assert por_titulo["Documento vazio"]["assignee"] is None


def test_card_excluido_nao_aparece(board_id: int, admin_id: int) -> None:
    card = _criar(board_id, admin_id, "Some")
    card.deleted_at = card.created_at
    db.session.commit()
    assert _titulos(board_id, "backlog") == []


def test_board_de_workspace_excluido_nao_e_encontrado(board_id: int, admin_id: int) -> None:
    ws_id = db.session.get(Board, board_id).workspace_id
    ws_svc.excluir(ws_id, user_id=admin_id)
    with pytest.raises(svc.BoardNaoEncontradoError):
        svc.listar_cards(board_id)
    with pytest.raises(svc.BoardNaoEncontradoError):
        svc.revisao(board_id)
    with pytest.raises(svc.BoardNaoEncontradoError):
        _criar(board_id, admin_id, "Não entra")


# ── Movimentação ─────────────────────────────────────────────────────────────


def test_mover_para_coluna_vazia(board_id: int, admin_id: int) -> None:
    card = _criar(board_id, admin_id, "A")
    movido = _mover(card, "doing", admin_id)
    assert movido.status == "doing"
    assert _titulos(board_id, "doing") == ["A"]
    assert _titulos(board_id, "backlog") == []
    historico = CardActivity.query.filter_by(card_id=card.id, action="moved").one()
    assert (historico.from_status, historico.to_status) == ("backlog", "doing")


def test_mover_para_topo_meio_e_fim(board_id: int, admin_id: int) -> None:
    c = _criar(board_id, admin_id, "C")
    b = _criar(board_id, admin_id, "B")
    a = _criar(board_id, admin_id, "A")
    assert _titulos(board_id, "backlog") == ["A", "B", "C"]

    _mover(a, "backlog", admin_id, before=c)  # fim
    assert _titulos(board_id, "backlog") == ["B", "C", "A"]
    _mover(a, "backlog", admin_id, after=b)  # topo
    assert _titulos(board_id, "backlog") == ["A", "B", "C"]
    _mover(c, "backlog", admin_id, before=a, after=b)  # meio
    assert _titulos(board_id, "backlog") == ["A", "C", "B"]


def test_mover_entre_colunas_no_meio(board_id: int, admin_id: int) -> None:
    y = _criar(board_id, admin_id, "Y", status="todo")
    x = _criar(board_id, admin_id, "X", status="todo")
    novo = _criar(board_id, admin_id, "Novo")
    _mover(novo, "todo", admin_id, before=x, after=y)
    assert _titulos(board_id, "todo") == ["X", "Novo", "Y"]


def test_renumera_quando_acaba_o_espaco(board_id: int, admin_id: int) -> None:
    a = _criar(board_id, admin_id, "A")
    b = _criar(board_id, admin_id, "B")
    c = _criar(board_id, admin_id, "C", status="todo")
    a.position, b.position = 10, 11  # sem inteiro livre entre eles
    db.session.commit()
    db.session.refresh(c)
    movimento = svc.mover_card(
        c.id, svc.MoverIn(status="backlog", before_id=a.id, after_id=b.id, version=c.version), user_id=admin_id
    )
    assert movimento.recarregar is True  # vizinhos mudaram de versão: cliente recarrega
    assert _titulos(board_id, "backlog") == ["A", "C", "B"]
    posicoes = [card["position"] for card in svc.listar_cards(board_id)[1]["backlog"]]
    assert posicoes == sorted(set(posicoes))


def test_versao_desatualizada_e_conflito(board_id: int, admin_id: int) -> None:
    card = _criar(board_id, admin_id, "A")
    versao_velha = card.version
    _mover(card, "todo", admin_id)
    with pytest.raises(svc.ConflitoError):
        svc.mover_card(card.id, svc.MoverIn(status="doing", version=versao_velha), user_id=admin_id)


@pytest.mark.parametrize(
    "caso", ["vizinho_em_outra_coluna", "nao_adjacentes", "topo_errado", "fim_errado", "coluna_nao_vazia"]
)
def test_vizinhos_defasados_sao_conflito(board_id: int, admin_id: int, caso: str) -> None:
    c = _criar(board_id, admin_id, "C", status="todo")
    b = _criar(board_id, admin_id, "B", status="todo")
    a = _criar(board_id, admin_id, "A", status="todo")  # todo: A, B, C
    outro = _criar(board_id, admin_id, "Outro")
    fora = _criar(board_id, admin_id, "Fora", status="done")
    vizinhos = {
        "vizinho_em_outra_coluna": {"before_id": fora.id},
        "nao_adjacentes": {"before_id": a.id, "after_id": c.id},
        "topo_errado": {"after_id": b.id},
        "fim_errado": {"before_id": b.id},
        "coluna_nao_vazia": {},
    }[caso]
    db.session.refresh(outro)
    with pytest.raises(svc.ConflitoError):
        svc.mover_card(outro.id, svc.MoverIn(status="todo", version=outro.version, **vizinhos), user_id=admin_id)


def test_card_nao_pode_ser_vizinho_de_si_mesmo(board_id: int, admin_id: int) -> None:
    card = _criar(board_id, admin_id, "A")
    with pytest.raises(svc.ConflitoError):
        svc.mover_card(
            card.id, svc.MoverIn(status="backlog", before_id=card.id, version=card.version), user_id=admin_id
        )


def test_card_inexistente_excluido_ou_de_board_excluido(board_id: int, admin_id: int) -> None:
    with pytest.raises(svc.CardNaoEncontradoError):
        svc.mover_card(999_999, svc.MoverIn(status="todo", version=1), user_id=admin_id)
    card = _criar(board_id, admin_id, "A")
    ws_svc.excluir(db.session.get(Board, board_id).workspace_id, user_id=admin_id)
    with pytest.raises(svc.CardNaoEncontradoError):
        svc.mover_card(card.id, svc.MoverIn(status="todo", version=card.version), user_id=admin_id)


def test_escrita_concorrente_vira_conflito(board_id: int, admin_id: int, monkeypatch: pytest.MonkeyPatch) -> None:
    """Outra transação altera o card entre a leitura e o commit: o bloqueio otimista barra."""
    card = _criar(board_id, admin_id, "A")
    original = svc._nova_posicao

    def concorrente(bid: int, c: Card, dados: svc.MoverIn) -> int:
        posicao = original(bid, c, dados)
        # "outra pessoa" grava o card depois da leitura e antes do nosso flush
        # UPDATE direto na tabela (como outra conexão faria): o update(Card) do
        # ORM sincronizaria o objeto em memória e esconderia o conflito.
        tabela = Card.__table__
        db.session.execute(update(tabela).where(tabela.c.id == card.id).values(version=tabela.c.version + 1))
        return posicao

    monkeypatch.setattr(svc, "_nova_posicao", concorrente)
    with pytest.raises(svc.ConflitoError):
        _mover(card, "todo", admin_id)
    monkeypatch.undo()
    assert _titulos(board_id, "backlog") == ["A"]


def test_revisao_sobe_a_cada_escrita(board_id: int, admin_id: int) -> None:
    assert svc.revisao(board_id) == 0
    card = _criar(board_id, admin_id, "A")
    assert svc.revisao(board_id) == 1
    movimento = svc.mover_card(card.id, svc.MoverIn(status="done", version=card.version), user_id=admin_id)
    assert movimento.revisao == 2 == svc.revisao(board_id)
    assert movimento.recarregar is False


def _bump_revisao_por_fora(board_id: int) -> None:
    """Outra conexão grava no board (UPDATE direto na tabela, sem passar pelo ORM)."""
    tabela = Board.__table__
    db.session.execute(update(tabela).where(tabela.c.id == board_id).values(revision=tabela.c.revision + 1))


def test_corrida_no_board_e_repetida_e_revalida(board_id: int, admin_id: int, monkeypatch: pytest.MonkeyPatch) -> None:
    """Outra escrita entre a leitura dos vizinhos e a gravação: repete e dá certo."""
    card = _criar(board_id, admin_id, "A")
    original = svc._nova_posicao
    chamadas = {"n": 0}

    def com_corrida(bid: int, c: Card, dados: svc.MoverIn) -> tuple[int, bool]:
        chamadas["n"] += 1
        resultado = original(bid, c, dados)
        if chamadas["n"] == 1:
            _bump_revisao_por_fora(bid)
        return resultado

    monkeypatch.setattr(svc, "_nova_posicao", com_corrida)
    movido = _mover(card, "doing", admin_id)
    assert chamadas["n"] == 2
    assert movido.status == "doing"
    assert _titulos(board_id, "doing") == ["A"]


def test_corrida_persistente_vira_conflito(board_id: int, admin_id: int, monkeypatch: pytest.MonkeyPatch) -> None:
    card = _criar(board_id, admin_id, "A")
    original = svc._nova_posicao

    def sempre_corrida(bid: int, c: Card, dados: svc.MoverIn) -> tuple[int, bool]:
        resultado = original(bid, c, dados)
        _bump_revisao_por_fora(bid)
        return resultado

    monkeypatch.setattr(svc, "_nova_posicao", sempre_corrida)
    with pytest.raises(svc.ConflitoError, match="outras pessoas"):
        _mover(card, "doing", admin_id)
    monkeypatch.undo()
    assert _titulos(board_id, "backlog") == ["A"]


def test_banco_travado_e_tratado_como_corrida(board_id: int, admin_id: int, monkeypatch: pytest.MonkeyPatch) -> None:
    from sqlalchemy.exc import OperationalError

    card = _criar(board_id, admin_id, "A")
    original = db.session.commit
    falhas = {"n": 0}

    def commit_travado() -> None:
        if falhas["n"] == 0:
            falhas["n"] += 1
            raise OperationalError("COMMIT", {}, Exception("database is locked"))
        original()

    monkeypatch.setattr(db.session, "commit", commit_travado)
    assert _mover(card, "done", admin_id).status == "done"
    assert falhas["n"] == 1


def test_outro_erro_operacional_sobe(board_id: int, admin_id: int, monkeypatch: pytest.MonkeyPatch) -> None:
    from sqlalchemy.exc import OperationalError

    card = _criar(board_id, admin_id, "A")

    def disco_cheio() -> None:
        raise OperationalError("COMMIT", {}, Exception("disk I/O error"))

    monkeypatch.setattr(db.session, "commit", disco_cheio)
    with pytest.raises(OperationalError):
        _mover(card, "done", admin_id)
    monkeypatch.undo()
    db.session.rollback()
