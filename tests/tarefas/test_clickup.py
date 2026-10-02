"""Aba ClickUp de /gov/tarefas: conversão, agrupamento por pessoa, busca e página."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

import httpx
import pytest
from flask.testing import FlaskClient

from app.services import clickup_tarefas as ck
from app.services.tarefas.permissions import Acao, pode

HOJE = date(2026, 10, 2)
PAGINA = "/gov/tarefas/clickup"
Cliente = Callable[..., FlaskClient]


def _ms(d: datetime) -> str:
    return str(int(d.timestamp() * 1000))


def _bruta(
    id_: str = "t1",
    tipo: str = "open",
    due: datetime | None = None,
    closed: datetime | None = None,
    pessoas: tuple[tuple[str, str], ...] = (("Ana", "Ana@Empresa.com"),),
) -> dict[str, Any]:
    return {
        "id": id_,
        "name": f"Tarefa {id_}",
        "status": {"status": "em aberto", "type": tipo},
        "due_date": _ms(due) if due else None,
        "date_closed": _ms(closed) if closed else None,
        "priority": {"orderindex": "2"},
        "folder": {"name": "hidden"},
        "list": {"name": "Projetos"},
        "url": f"https://app.clickup.com/t/{id_}",
        "assignees": [{"username": n, "email": e} for n, e in pessoas],
    }


@pytest.mark.parametrize(
    ("tipo", "situacao"),
    [("open", "a_fazer"), ("custom", "andamento"), ("closed", "concluida"), ("done", "concluida")],
)
def test_situacao_vem_do_tipo_do_status(tipo: str, situacao: str) -> None:
    assert ck.converter(_bruta(tipo=tipo), HOJE).situacao == situacao


def test_status_cancelado_conta_como_encerrada() -> None:
    bruta = _bruta(tipo="custom")
    bruta["status"]["status"] = "Cancelado"
    assert ck.converter(bruta, HOJE).situacao == "concluida"


def test_converter_campos_e_pasta_oculta() -> None:
    t = ck.converter(_bruta(), HOJE)
    assert t.local == "Projetos"  # pasta "hidden" do ClickUp não aparece
    assert t.prioridade == "Alta"
    assert t.responsaveis[0].email == "ana@empresa.com"
    assert t.vencimento is None and not t.atrasada


def test_vencimento_usa_fuso_de_brasilia() -> None:
    # 02/10 01:00 UTC ainda é 01/10 em Brasília → já venceu em 02/10.
    t = ck.converter(_bruta(due=datetime(2026, 10, 2, 1, 0, tzinfo=UTC)), HOJE)
    assert t.vencimento == date(2026, 10, 1)
    assert t.atrasada


def test_concluida_nunca_fica_atrasada() -> None:
    venceu = datetime(2026, 9, 1, 12, tzinfo=UTC)
    t = ck.converter(_bruta(tipo="closed", due=venceu, closed=venceu), HOJE)
    assert not t.atrasada
    assert t.concluida_em == date(2026, 9, 1)


def test_agrupar_por_pessoa() -> None:
    venceu = datetime(2026, 9, 1, 12, tzinfo=UTC)
    tarefas = [
        ck.converter(_bruta("a1", due=datetime(2026, 12, 1, 12, tzinfo=UTC)), HOJE),
        ck.converter(_bruta("a2", due=venceu), HOJE),
        ck.converter(_bruta("a3"), HOJE),
        ck.converter(_bruta("dupla", pessoas=(("Ana", "ana@empresa.com"), ("Bia", "bia@empresa.com"))), HOJE),
        *(
            ck.converter(_bruta(f"c{i}", tipo="closed", closed=datetime(2026, 9, i, 12, tzinfo=UTC)), HOJE)
            for i in range(1, 5)
        ),
        ck.converter(_bruta("ninguem", pessoas=()), HOJE),
    ]
    grupos = ck.agrupar_por_responsavel(tarefas, concluidas_max=2)

    assert [g.nome for g in grupos] == ["Ana", "Bia"]  # quem tem atraso vem primeiro
    ana = grupos[0]
    assert [t.id for t in ana.abertas] == ["a2", "a1", "a3", "dupla"]  # atrasada, por vencimento, sem data
    assert ana.atrasadas == 1
    assert [t.id for t in ana.concluidas] == ["c4", "c3"]
    assert ana.total_concluidas == 4
    assert [t.id for t in grupos[1].abertas] == ["dupla"]


def test_buscar_tarefas_para_na_ultima_pagina(monkeypatch: pytest.MonkeyPatch) -> None:
    pedidas: list[int] = []

    async def _pagina(_cliente: httpx.AsyncClient, p: int) -> tuple[list[dict[str, Any]], bool]:
        pedidas.append(p)
        if p < 7:
            return [_bruta(f"p{p}")], False
        return ([_bruta("p7")], True) if p == 7 else ([], True)

    monkeypatch.setattr(ck, "_pagina", _pagina)
    brutas = asyncio.run(ck.buscar_tarefas())
    assert [b["id"] for b in brutas] == [f"p{i}" for i in range(8)]
    assert sorted(pedidas) == list(range(10))  # dois lotes de 5


def test_carregar_sem_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLICKUP_TOKEN", raising=False)
    r = ck._carregar()
    assert not r.ok and r.motivo == "sem_token"


def test_carregar_com_erro_e_sem_duplicadas(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLICKUP_TOKEN", "pk_teste")

    async def _falha() -> list[dict[str, Any]]:
        raise httpx.ConnectError("sem rede")

    monkeypatch.setattr(ck, "buscar_tarefas", _falha)
    assert ck._carregar().motivo == "erro"

    async def _ok() -> list[dict[str, Any]]:
        return [_bruta("x"), _bruta("x"), _bruta("y", pessoas=())]

    monkeypatch.setattr(ck, "buscar_tarefas", _ok)
    r = ck._carregar()
    assert r.ok and [t.id for t in r.tarefas] == ["x", "y"]
    assert r.sem_responsavel == 1


def test_so_admin_ve_todos() -> None:
    assert pode("admin", Acao.VER_CLICKUP_TODOS)
    for role in ("gestor", "operador", "visualizador"):
        assert not pode(role, Acao.VER_CLICKUP_TODOS)


@pytest.fixture
def clickup_fake(monkeypatch: pytest.MonkeyPatch) -> None:
    tarefas = [
        ck.converter(_bruta("op", pessoas=(("Operador", "pytest-tarefas-operador@test.local"),)), HOJE),
        ck.converter(_bruta("outra", pessoas=(("Outra Pessoa", "outra@empresa.com"),)), HOJE),
    ]
    resultado = ck.ResultadoClickUp(ok=True, tarefas=tarefas, sem_responsavel=3, atualizado_em=datetime.now(UTC))
    monkeypatch.setattr(ck, "obter", lambda: resultado)


@pytest.mark.usefixtures("clickup_fake")
def test_pagina_admin_ve_todos_separados(cliente: Cliente) -> None:
    html = cliente("admin").get(PAGINA).get_data(as_text=True)
    assert "Tarefa op" in html and "Tarefa outra" in html
    assert "Outra Pessoa" in html and "Resumo por pessoa" in html
    assert "3 tarefas sem responsável" in html
    assert 'aria-current="page"' in html


@pytest.mark.usefixtures("clickup_fake")
@pytest.mark.parametrize("role", ["gestor", "operador", "visualizador"])
def test_pagina_demais_perfis_veem_so_as_proprias(cliente: Cliente, role: str) -> None:
    r = cliente(role).get(PAGINA)
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Tarefa outra" not in html
    assert ("Tarefa op" in html) is (role == "operador")
    if role != "operador":
        assert "Não há tarefas atribuídas" in html


def test_pagina_sem_token(cliente: Cliente, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ck, "obter", lambda: ck.ResultadoClickUp(ok=False, motivo="sem_token", atualizado_em=datetime.now(UTC))
    )
    html = cliente("admin").get(PAGINA).get_data(as_text=True)
    assert "CLICKUP_TOKEN" in html


def test_aba_clickup_na_pagina_de_workspaces(cliente: Cliente) -> None:
    html = cliente("visualizador").get("/gov/tarefas").get_data(as_text=True)
    assert 'href="/gov/tarefas/clickup"' in html


def test_busca_so_a_lista_de_projetos_de_ti(monkeypatch: pytest.MonkeyPatch) -> None:
    """Só a lista Projetos (TI) — o workspace inteiro trazia pessoas de fora da TI."""
    pedidos: list[httpx.Request] = []

    def responder(req: httpx.Request) -> httpx.Response:
        pedidos.append(req)
        return httpx.Response(200, json={"tasks": [], "last_page": True})

    monkeypatch.delenv("CLICKUP_LIST_ID", raising=False)
    transporte = httpx.MockTransport(responder)

    async def rodar() -> tuple[list[dict[str, Any]], bool]:
        async with httpx.AsyncClient(transport=transporte) as c:
            return await ck._pagina(c, 0)

    assert asyncio.run(rodar()) == ([], True)
    assert pedidos[0].url.params.get("list_ids[]") == "901321459571"
    assert pedidos[0].url.params.get("subtasks") == "true"
