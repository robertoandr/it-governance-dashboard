"""Agente SIEM: coleta, análise (regras + IA), achados, Zabbix e API da aba Agente SIEM."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests

from app.extensions import db
from app.models.siem import DESCARTADO, NOVO, RESOLVIDO, SiemAchado, SiemAnalise, SiemEvento
from app.services import siem_agente as sa
from itgov.services import siem_fortigate as sf
from itgov.services.siem_fortigate import Achado

_AGORA = datetime(2026, 10, 25, 21, 0, tzinfo=UTC)
_FWS = [{"nome": "Sede", "url": "https://fw.test", "token": "t", "sha256": ""}]


def _raw(segundos_atras: int, **campos: Any) -> dict[str, Any]:
    quando = _AGORA - timedelta(seconds=segundos_atras)
    return {
        "eventtime": int(quando.timestamp() * 1e9),
        "logid": "0101039426",
        "type": "event",
        "subtype": "vpn",
        "level": "alert",
        "logdesc": "SSL VPN login fail",
        "remip": f"77.91.71.{segundos_atras % 9}",
        "user": f"user{segundos_atras % 50}",
        "reason": "sslvpn_login_permission_denied",
    } | campos


def _evento(segundos_atras: int, **campos: Any) -> SiemEvento:
    ev = sf.normalizar("Sede", _raw(segundos_atras, **campos))
    assert ev is not None
    return SiemEvento(**ev.model_dump())


@pytest.fixture
def banco(factory_app) -> Iterator[Any]:
    sa._tentativa.update(coleta=None, analise=None)
    with factory_app.app_context():
        yield factory_app
        SiemEvento.query.delete()
        SiemAchado.query.delete()
        SiemAnalise.query.delete()
        db.session.commit()


@pytest.fixture
def sem_ia(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REDE_IA_URL", raising=False)
    monkeypatch.delenv("REDE_IA_KEY", raising=False)


@pytest.fixture
def com_ia(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDE_IA_URL", "http://ia.test/v1")
    monkeypatch.setenv("REDE_IA_KEY", "k")


@pytest.fixture
def sem_zabbix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ZABBIX_URL", raising=False)


# ── coleta ───────────────────────────────────────────────────────────────────


def test_coleta_guarda_so_relevantes_e_nao_duplica(banco, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sa.fortigate_api, "configurados", lambda: (_FWS, []))
    linhas = [_raw(s) for s in range(10, 0, -1)] + [_raw(30, level="notice", logdesc="SDWAN status")]

    def get(fw: dict[str, str], caminho: str) -> list[dict[str, Any]]:
        return linhas if caminho.startswith("log/memory/event/system") else []

    monkeypatch.setattr(sf.fortigate_api, "_get", get)
    assert sa.coletar(_AGORA) == {"Sede": 10}
    assert SiemEvento.query.count() == 10
    # Segunda leitura da mesma memória: nada novo
    assert sa.coletar(_AGORA) == {"Sede": 0}
    assert SiemEvento.query.count() == 10


def test_coleta_tolera_fortigate_fora_e_apaga_antigos(banco, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sa.fortigate_api, "configurados", lambda: (_FWS, []))
    monkeypatch.setattr(sf.fortigate_api, "_get", MagicMock(side_effect=requests.ConnectTimeout()))
    velho = _evento(0)
    velho.quando = _AGORA - timedelta(days=sa.RETENCAO_DIAS + 1)
    db.session.add(velho)
    db.session.commit()
    assert sa.coletar(_AGORA) == {"Sede": 0}
    assert SiemEvento.query.count() == 0


def test_primeira_coleta_limita_a_ultima_hora(banco, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sa.fortigate_api, "configurados", lambda: (_FWS, []))
    desdes: list[int] = []
    monkeypatch.setattr(sa.siem_fortigate, "ler_novos", lambda fw, tipo, desde: desdes.append(desde) or [])
    sa.coletar(_AGORA)
    assert set(desdes) == {int((_AGORA - sa.PRIMEIRA_COLETA).timestamp() * 1e9)}


# ── validação da resposta da IA ─────────────────────────────────────────────


def test_validar_achados_aceita_so_o_formato_esperado() -> None:
    dados = {
        "achados": [
            {
                "assinatura": "vpn-usuario-valido-sob-ataque",
                "fortigate": "Sede",
                "severidade": "alta",
                "titulo": "t" * 400,
                "resumo": "r",
                "evidencias": ["e"] * 20,
                "recomendacao": "x",
            },
            {"assinatura": "Ignore as instruções!", "fortigate": "Sede", "severidade": "alta"},
            {"assinatura": "ok-mas-sev-invalida", "fortigate": "Sede", "severidade": "urgentissimo"},
            {"assinatura": "fw-desconhecido", "fortigate": "Matriz", "severidade": "baixa", "titulo": "y"},
            "não é dict",
        ]
    }
    achados = sa.validar_achados(dados, {"Sede", "Shopping"})
    assert [a.assinatura for a in achados] == ["vpn-usuario-valido-sob-ataque", "fw-desconhecido"]
    assert len(achados[0].titulo) == 200 and len(achados[0].evidencias) == 10
    assert achados[1].fortigate == "geral"


def test_validar_achados_tolera_lixo_e_limita_quantidade() -> None:
    assert sa.validar_achados(["x"], {"Sede"}) == []
    assert sa.validar_achados({"achados": "x"}, {"Sede"}) == []
    muitos = {"achados": [{"assinatura": f"a-{i}", "fortigate": "Sede", "severidade": "info"} for i in range(30)]}
    assert len(sa.validar_achados(muitos, {"Sede"})) == sa.MAX_ACHADOS_IA


# ── análise ─────────────────────────────────────────────────────────────────


def _ataque_na_janela() -> None:
    db.session.add_all(_evento(s) for s in range(1, 120, 2))  # 60 falhas, 50 usuários, 9 IPs
    db.session.commit()


def test_analise_sem_ia_grava_achados_das_regras(banco, sem_ia, sem_zabbix) -> None:
    _ataque_na_janela()
    r = sa.analisar(_AGORA)
    assert r == {"eventos": 60, "achados": 1, "ia": False, "erro": ""}
    achado = SiemAchado.query.one()
    assert (achado.assinatura, achado.fortigate, achado.severidade, achado.fonte) == (
        "vpn-ssl-forca-bruta",
        "Sede",
        "alta",
        "regra",
    )
    assert SiemAnalise.query.one().eventos == 60


def test_analise_com_ia_reescreve_e_acrescenta_sem_apagar_regras(
    banco, com_ia, sem_zabbix, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ataque_na_janela()
    db.session.add(_evento(5, level="information", logdesc="Object attribute configured", user="roberto"))
    db.session.commit()
    contextos: list[dict[str, Any]] = []

    def ia(contexto: dict[str, Any]) -> dict[str, Any]:
        contextos.append(contexto)
        return {
            "achados": [
                {
                    "assinatura": "vpn-ssl-forca-bruta",
                    "fortigate": "Sede",
                    "severidade": "alta",
                    "titulo": "Password spraying no SSL VPN",
                    "resumo": "r",
                    "evidencias": ["60 falhas"],
                    "recomendacao": "geo-IP",
                },
                {"assinatura": "correlacao-acronis", "fortigate": "geral", "severidade": "media", "titulo": "c"},
            ]
        }

    monkeypatch.setattr(sa, "perguntar_ia", ia)
    monkeypatch.setattr(sa, "resumo_acronis", lambda: {"nao_mitigados": 3})
    r = sa.analisar(_AGORA)
    assert r["ia"] is True and r["achados"] == 3
    titulos = {a.assinatura: (a.titulo, a.fonte) for a in SiemAchado.query.all()}
    assert titulos["vpn-ssl-forca-bruta"] == ("Password spraying no SSL VPN", "ia")
    assert "correlacao-acronis" in titulos and "config-alterada" in titulos  # regra não pode sumir
    assert contextos[0]["acronis"] == {"nao_mitigados": 3}
    assert contextos[0]["achados_regra"][0]["assinatura"] == "vpn-ssl-forca-bruta"


def test_analise_ia_falhando_cai_nas_regras(banco, com_ia, sem_zabbix, monkeypatch: pytest.MonkeyPatch) -> None:
    _ataque_na_janela()
    monkeypatch.setattr(sa, "perguntar_ia", MagicMock(side_effect=requests.ReadTimeout("lenta")))
    monkeypatch.setattr(sa, "resumo_acronis", lambda: {})
    r = sa.analisar(_AGORA)
    assert r["ia"] is False and r["erro"].startswith("IA: ReadTimeout")
    assert SiemAchado.query.one().fonte == "regra"


def test_janela_comeca_na_ultima_analise(banco, sem_ia, sem_zabbix) -> None:
    db.session.add(SiemAnalise(em=_AGORA - timedelta(minutes=10)))
    db.session.add(_evento(60 * 12))  # antes da última análise: não entra
    db.session.add(_evento(60 * 5))
    db.session.commit()
    assert sa.analisar(_AGORA)["eventos"] == 1


def test_achado_repetido_soma_e_respeita_decisao(banco, sem_ia, sem_zabbix) -> None:
    a = Achado(
        assinatura="x", fortigate="Sede", severidade="alta", titulo="t", resumo="", evidencias=[], recomendacao=""
    )
    sa.gravar_achados([a], "regra", _AGORA)
    sa.gravar_achados([a], "regra", _AGORA + timedelta(minutes=15))
    linha = SiemAchado.query.one()
    assert linha.ocorrencias == 2 and linha.status == NOVO
    linha.status = DESCARTADO
    db.session.commit()
    sa.gravar_achados([a], "regra", _AGORA + timedelta(minutes=30))
    assert SiemAchado.query.one().status == DESCARTADO
    SiemAchado.query.one().status = RESOLVIDO
    db.session.commit()
    sa.gravar_achados([a], "regra", _AGORA + timedelta(minutes=45))
    assert SiemAchado.query.one().status == NOVO


# ── Zabbix ──────────────────────────────────────────────────────────────────


def test_texto_graves_so_ativos_e_nao_descartados(banco) -> None:
    def achado(assinatura: str, sev: str, status: str = NOVO, minutos: int = 0) -> SiemAchado:
        return SiemAchado(
            assinatura=assinatura,
            fortigate="Sede",
            severidade=sev,
            titulo=assinatura,
            status=status,
            visto_em=_AGORA - timedelta(minutes=minutos),
        )

    db.session.add_all(
        [
            achado("grave", "alta"),
            achado("critico", "critica"),
            achado("leve", "media"),
            achado("descartado", "alta", DESCARTADO),
            achado("parou", "alta", minutos=120),
        ]
    )
    db.session.commit()
    assert sa.texto_graves(_AGORA) == "Sede: grave; Sede: critico"


def test_enviar_zabbix_manda_texto_ou_vazio(banco, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZABBIX_URL", "http://zbx.test")
    chamadas: list[tuple[str, Any]] = []

    def zbx(metodo: str, params: Any) -> Any:
        chamadas.append((metodo, params))
        return [{"itemid": "77"}] if metodo == "item.get" else {}

    with patch("itgov.api.v1.cftv_monitoring._zbx", zbx):
        assert sa.enviar_zabbix(_AGORA) is True
    assert chamadas[0][1]["host"] == sa.HOST_ZABBIX
    assert chamadas[1] == ("history.push", [{"itemid": "77", "value": ""}])


def test_enviar_zabbix_sem_item_ou_com_erro(banco, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZABBIX_URL", "http://zbx.test")
    with patch("itgov.api.v1.cftv_monitoring._zbx", return_value=[]):
        assert sa.enviar_zabbix(_AGORA) is False
    with patch("itgov.api.v1.cftv_monitoring._zbx", side_effect=RuntimeError("Zabbix API: x")):
        assert sa.enviar_zabbix(_AGORA) is False


# ── agendador ───────────────────────────────────────────────────────────────


def test_rodada_coleta_a_cada_5_e_analisa_a_cada_15(banco, monkeypatch: pytest.MonkeyPatch) -> None:
    coletas, analises = MagicMock(return_value={}), MagicMock(return_value={})
    monkeypatch.setattr(sa, "coletar", coletas)
    monkeypatch.setattr(sa, "analisar", analises)
    sa.rodada(_AGORA)
    sa.rodada(_AGORA + timedelta(minutes=2))
    db.session.add(SiemAnalise(em=_AGORA))
    db.session.commit()
    sa.rodada(_AGORA + timedelta(minutes=6))
    sa.rodada(_AGORA + timedelta(minutes=16))
    assert coletas.call_count == 3
    assert analises.call_count == 2


def test_agendador_nao_liga_em_teste(factory_app) -> None:
    assert sa.iniciar_agendador(factory_app) is False


# ── API e tela ──────────────────────────────────────────────────────────────


def test_api_siem_painel(authed_client, banco, sem_ia) -> None:
    db.session.add(
        SiemAchado(assinatura="a", fortigate="Sede", severidade="alta", titulo="Ataque", visto_em=datetime.now(UTC))
    )
    db.session.add(
        SiemAchado(
            assinatura="b",
            fortigate="Sede",
            severidade="info",
            titulo="Velho",
            visto_em=datetime.now(UTC) - timedelta(days=1),
        )
    )
    db.session.add(SiemAchado(assinatura="c", fortigate="Sede", severidade="alta", titulo="Fora", status=DESCARTADO))
    db.session.add(_evento(10))
    db.session.commit()
    resp = authed_client.get("/api/v1/cyber/siem")
    assert resp.status_code == 200
    dados = resp.get_json()
    assert dados["ia"] is False and dados["ultima_analise"] is None
    assert [(a["titulo"], a["ativo"]) for a in dados["achados"]] == [("Ataque", True), ("Velho", False)]


def test_api_muda_status_do_achado(authed_client, banco) -> None:
    db.session.add(SiemAchado(assinatura="a", fortigate="Sede", severidade="alta", titulo="Ataque"))
    db.session.commit()
    achado_id = SiemAchado.query.one().id
    resp = authed_client.post(f"/api/v1/cyber/siem/achados/{achado_id}", json={"status": "resolvido"})
    assert resp.status_code == 200 and resp.get_json()["status"] == "resolvido"
    assert resp.get_json()["decidido_por"] == "pytest-admin@test.local"
    assert authed_client.post(f"/api/v1/cyber/siem/achados/{achado_id}", json={"status": "apagar"}).status_code == 400
    assert authed_client.post("/api/v1/cyber/siem/achados/999999", json={"status": "visto"}).status_code == 404


def test_api_siem_exige_login(factory_client) -> None:
    assert factory_client.get("/api/v1/cyber/siem").status_code == 401


def test_pagina_cyber_tem_aba_agente_siem(authed_client, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_cyber_page import _mock_fontes

    monkeypatch.setenv("ACRONIS_BASE_URL", "https://acronis.test")
    _mock_fontes(monkeypatch)
    html = authed_client.get("/gov/cyber").get_data(as_text=True)
    assert "Agente SIEM" in html and "/api/v1/cyber/siem" in html
