"""Visão Geral: botão Atualizar de verdade e MTTR real do Zendesk."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from app.services.influxdb_provider import _componente_mttr
from itgov.models.zendesk import SLATargets, Ticket, TicketMetricSet
from itgov.services.sla_evaluator import ITIL_DEFAULT_TARGETS, SOURCE_ITIL_DEFAULT, summarize
from itgov.utils.cache_swr import CacheSWR

DEFAULT_TARGETS = SLATargets(by_priority=ITIL_DEFAULT_TARGETS, source=SOURCE_ITIL_DEFAULT)


def _resolvido(horas: float | None) -> Ticket:
    criado = datetime(2026, 9, 1, 8, tzinfo=UTC)
    return Ticket(
        id=1,
        subject="x",
        status="solved",
        created_at=criado,
        updated_at=criado,
        requester_id=1,
        metric_set=TicketMetricSet(solved_at=criado + timedelta(hours=horas) if horas is not None else None),
    )


def test_mttr_e_a_media_de_horas_entre_abertura_e_solucao() -> None:
    m = summarize([_resolvido(2), _resolvido(10), _resolvido(None)], DEFAULT_TARGETS)
    assert m.avg_resolution_hours == 6.0


def test_mttr_sem_chamados_resolvidos_fica_none() -> None:
    assert summarize([], DEFAULT_TARGETS).avg_resolution_hours is None


def test_componente_mttr_nota_pela_meta_de_24h() -> None:
    assert _componente_mttr(12.0)["value"] == 100.0
    c = _componente_mttr(48.0)
    assert c["value"] == 50.0 and c["raw_value"] == 48.0 and c["source"] == "zendesk"
    assert _componente_mttr(None)["source"] == "coming_soon"  # sai da média do pilar


def test_cache_atualizar_agora_busca_na_hora_e_mantem_valor_se_falhar() -> None:
    cache: CacheSWR[int] = CacheSWR("teste", ttl=3600)
    assert cache.get(lambda: 1) == 1
    assert cache.atualizar_agora(lambda: 2) == 2
    assert cache.get(lambda: 3) == 2  # ainda dentro do ttl

    def quebra() -> int:
        raise RuntimeError("fora do ar")

    assert cache.atualizar_agora(quebra) == 2


def test_botao_atualizar_confere_fontes_e_volta_para_a_pagina(authed_client) -> None:
    with patch("app.services.fontes_status.atualizar_agora", return_value=[]) as forcar:
        resp = authed_client.get("/gov/?atualizar=1")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/gov/")
    forcar.assert_called_once()


def test_visao_geral_mostra_hora_da_pagina_e_da_coleta(authed_client) -> None:
    with patch("app.services.fontes_status.status_fontes", return_value=[]):
        html = authed_client.get("/gov/").get_data(as_text=True)
    assert "Última coleta dos dados" in html
    assert datetime.now().strftime("%Y") in html
    assert "atualizar=1" in html
