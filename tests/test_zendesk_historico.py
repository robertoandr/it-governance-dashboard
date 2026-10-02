"""Histórico do grupo Zendesk: meses, volume por usuário e total real (z3, z4, sl1)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import patch

import httpx
import pytest

from itgov.models.zendesk import Ticket
from itgov.services.zendesk_historico import SEM_RESPONSAVEL, data_criacao, meses_desde, resumir
from itgov.services.zendesk_service import ZendeskService

HOJE = date(2026, 10, 2)


def _t(tid: int, criado: str, status: str = "closed", agente: int | None = 10, solicitante: int = 100) -> Ticket:
    quando = datetime.fromisoformat(criado).replace(tzinfo=UTC)
    return Ticket(
        id=tid,
        subject="x",
        status=status,
        created_at=quando,
        updated_at=quando,
        assignee_id=agente,
        requester_id=solicitante,
    )


TICKETS = [
    _t(1, "2026-06-25"),
    _t(2, "2026-07-10"),
    _t(3, "2026-07-20", agente=None),
    _t(4, "2026-08-05", agente=11),
    _t(5, "2026-09-28", status="open", solicitante=101),
    _t(6, "2026-10-01", status="new", agente=11, solicitante=101),
]
NOMES = {10: "Allan", 11: "Roberto", 100: "Loja Centro", 101: "Shopping 1"}


def test_meses_desde_a_criacao_ate_hoje() -> None:
    meses = meses_desde(date(2026, 6, 23), HOJE)
    assert [i.isoformat() for i, _ in meses] == ["2026-06-01", "2026-07-01", "2026-08-01", "2026-09-01", "2026-10-01"]
    assert meses[-1][1] == date(2026, 11, 1)


def test_resumo_conta_por_mes_e_media_so_de_meses_completos() -> None:
    h = resumir(TICKETS, NOMES, {"2026-07": 2, "2026-09": 1}, "TI / Infra", date(2026, 6, 23), HOJE)
    assert h.total == 6
    assert [(m.rotulo, m.abertos, m.resolvidos) for m in h.meses] == [
        ("06/2026", 1, 0),
        ("07/2026", 2, 2),
        ("08/2026", 1, 0),
        ("09/2026", 1, 1),
        ("10/2026", 1, 0),
    ]
    # jun (criação) e out (corrente) são parciais: (2 + 1 + 1) / 3
    assert h.media_mensal == 1.3


def test_volume_por_responsavel_e_solicitante() -> None:
    h = resumir(TICKETS, NOMES, {}, "TI / Infra", date(2026, 6, 23), HOJE)
    resp = {v.nome: v for v in h.por_responsavel}
    assert (resp["Allan"].total, resp["Allan"].resolvidos) == (3, 2)
    assert (resp["Roberto"].total, resp["Roberto"].em_aberto, resp["Roberto"].ultimos_30d) == (2, 1, 1)
    assert resp[SEM_RESPONSAVEL].total == 1
    assert h.por_responsavel[0].nome == "Allan"  # maior volume primeiro
    sol = {v.nome: v for v in h.por_solicitante}
    assert (sol["Loja Centro"].total, sol["Shopping 1"].em_aberto) == (4, 2)


def test_sem_data_do_grupo_usa_o_ticket_mais_antigo() -> None:
    assert resumir(TICKETS, NOMES, {}, "TI / Infra", None, HOJE).desde == date(2026, 6, 25)
    assert data_criacao({"created_at": "2026-06-23T13:01:02Z"}) == date(2026, 6, 23)
    assert data_criacao(None) is None


def _svc(group_id: int | None = 50589558960788) -> ZendeskService:
    return ZendeskService(subdomain="t", email="a@b.c", api_token="x", group_id=group_id)


def test_tickets_do_grupo_vem_do_export_sem_teto_de_1000() -> None:
    svc = _svc()
    with patch.object(svc, "_paginate", return_value=[]) as pag:
        svc.get_tickets()
    path, chave = pag.call_args.args
    assert path == "/api/v2/search/export.json" and chave == "results"
    assert pag.call_args.kwargs["filter[type]"] == "ticket"
    assert pag.call_args.kwargs["query"] == "group_id:50589558960788"
    assert "cursor" not in pag.call_args.kwargs  # export pagina por cursor (padrão)


def test_contagem_e_nomes_por_lote() -> None:
    svc = _svc()
    with patch.object(svc, "_get_json", return_value={"count": 7}) as gj:
        assert svc.count_tickets("solved>=2026-09-01 solved<2026-10-01") == 7
    assert gj.call_args.kwargs["query"] == "type:ticket solved>=2026-09-01 solved<2026-10-01 group_id:50589558960788"

    lotes: list[str] = []

    def responder(_path: str, ids: str) -> dict:
        lotes.append(ids)
        return {"users": [{"id": int(i), "name": f"U{i}"} for i in ids.split(",")]}

    with patch.object(svc, "_get_json", side_effect=responder):
        nomes = svc.get_user_names([*range(1, 151), 1, 2])
    assert len(lotes) == 2 and nomes[150] == "U150"


def test_historico_indisponivel_nao_derruba_a_pagina() -> None:
    from itgov.api.v1 import zendesk as api

    api._cache_hist.limpar()
    with patch.object(api, "_carregar_historico", side_effect=httpx.ConnectError("fora")):
        assert api.get_cached_historico() == {}


@pytest.fixture
def com_zendesk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZENDESK_SUBDOMAIN", "test-corp")


def test_paginas_mostram_historico_e_volume_por_usuario(authed_client, com_zendesk: None) -> None:
    hist = resumir(TICKETS, NOMES, {"2026-07": 2}, "TI / Infra", date(2026, 6, 23), HOJE).model_dump(mode="json")
    mttr = {
        "total_open": 2, "breached": 1, "compliance_pct": 50.0, "first_reply_compliance_pct": None,
        "resolution_compliance_pct": None, "avg_first_reply_minutes": None, "period_total": 4,
        "period_breached": 2, "period_unknown": 0, "window_days": 30, "avg_age_hours": 10.0,
        "sla_source": "zendesk_policy", "sla_policy": "SLA Geral", "csat_pct": None, "csat_sample": 0,
        "csat_good": 0, "csat_bad": 0,
    }  # fmt: skip
    with (
        patch("itgov.api.v1.zendesk.get_cached_historico", return_value=hist),
        patch("itgov.api.v1.zendesk.get_cached_mttr_summary", return_value=mttr),
        patch("itgov.api.v1.zendesk.get_cached_volume_by_status", return_value={}),
    ):
        html = authed_client.get("/gov/zendesk").get_data(as_text=True)
    assert "Volume por responsável" in html and "Allan" in html
    assert "Volume por solicitante" in html and "Loja Centro" in html
    assert "Histórico mês a mês — TI / Infra" in html
    assert "6 chamados desde 23/06/2026" in html
