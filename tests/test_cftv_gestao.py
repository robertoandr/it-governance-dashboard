"""CFTV: drill-down por sede, renomear e unir gravador duplicado (só admin)."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, inspect, text

from app.extensions import db
from app.models.unidade import DvrUnidade, Unidade, garantir_colunas
from itgov.api.v1 import cftv_monitoring
from itgov.api.v1.cftv_monitoring import montar_visao, resolver_principal


def _dev(host: str, gravador: str, status: str = "up", **extra: object) -> dict:
    base = {
        "host": host,
        "name": host,
        "ip": "10.0.0.1",
        "subcat": "camera",
        "andar": "?",
        "gravador": gravador,
        "is_gravador": False,
        "canal": "",
        "loja": "",
        "vendor": "",
        "model": "",
        "status": status,
        "problems": 0,
    }
    base.update(extra)
    return base


_DADOS = {
    "enabled": True,
    "devices": [
        _dev("DVR-3", "DVR-3", subcat="dvr", is_gravador=True, name="DVR-3 · 5º/Térreo"),
        _dev("cam-dvr3-01", "DVR-3", canal="1"),
        _dev("cam-terreo-01", "Térreo Gestao", status="down", canal="1"),
        _dev("cam-terreo-02", "Térreo Gestao", canal="2"),
        _dev("cam-shop-01", "Shopping Gestao", canal="1"),
        _dev("cam-solta", "", status="down"),
    ],
}

# 1 = Sede Centro (raiz), 2 = Shopping (raiz), 3 = Shopping / Bloco B (filha)
_UNIDADES = {1: "Sede Centro", 2: "Shopping", 3: "Shopping / Bloco B"}
_SEDE = {1: 1, 2: 2, 3: 2}


def _visao(**kw: object) -> dict:
    return montar_visao(
        _DADOS,
        unidade_por_gravador={"DVR-3": 1, "Shopping Gestao": 3},
        unidades=_UNIDADES,
        unidade_por_loja={},
        sede_por_unidade=_SEDE,
        **kw,
    )


@pytest.fixture(autouse=True)
def _sem_historico_real(monkeypatch: pytest.MonkeyPatch) -> None:
    # A página busca o histórico de quedas no Zabbix; nos testes, vazio.
    monkeypatch.setattr(cftv_monitoring, "get_cached_historico_quedas", lambda: {})


# ── Serviço ───────────────────────────────────────────────────────────────────


def test_resolver_principal_segue_cadeia_e_para_em_ciclo() -> None:
    assert resolver_principal("A", {}) == "A"
    assert resolver_principal("A", {"A": "B", "B": "C"}) == "C"
    # Ciclo não trava: para antes de repetir
    assert resolver_principal("A", {"A": "B", "B": "A"}) == "B"


def test_totais_por_sede_somam_filhas_e_sem_unidade_por_ultimo() -> None:
    sedes = {s["nome"]: s for s in _visao()["sedes"]}
    assert set(sedes) == {"Sede Centro", "Shopping", "Sem unidade"}
    # Bloco B (filha) entra na sede Shopping
    assert sedes["Shopping"]["id"] == 2 and sedes["Shopping"]["total"] == 1
    assert (sedes["Sede Centro"]["total"], sedes["Sede Centro"]["gravadores"]) == (2, 1)
    # Térreo (sem vínculo) + câmera solta
    assert (sedes["Sem unidade"]["total"], sedes["Sem unidade"]["down"]) == (3, 2)
    assert _visao()["sedes"][-1]["id"] is None


def test_apelido_troca_titulo_e_guarda_nome_do_zabbix() -> None:
    card = next(c for c in _visao(apelidos={"DVR-3": "Garagem"})["cards"] if c["gravador"] == "DVR-3")
    assert card["titulo"] == "Garagem"
    assert card["nome_zabbix"] == "DVR-3 · 5º/Térreo"
    assert card["apelido"] == "Garagem"


def test_duplicado_entra_no_card_do_principal() -> None:
    v = _visao(duplicado_de={"Térreo Gestao": "DVR-3"})
    por_gravador = {c["gravador"]: c for c in v["cards"]}
    assert "Térreo Gestao" not in por_gravador
    dvr3 = por_gravador["DVR-3"]
    assert dvr3["unidos"] == ["Térreo Gestao"]
    # Câmeras do duplicado herdam a unidade do principal
    assert dvr3["unidade"] == "Sede Centro"
    assert {d["host"] for d in dvr3["dispositivos"]} == {"cam-dvr3-01", "cam-terreo-01", "cam-terreo-02"}
    assert (dvr3["total"], dvr3["down"]) == (4, 1)
    # O cache não pode ser alterado
    assert _DADOS["devices"][2]["gravador"] == "Térreo Gestao"


def test_duplicado_com_host_proprio_vira_dispositivo_do_card() -> None:
    dados = {
        "enabled": True,
        "devices": [
            _dev("DVR-A", "DVR-A", subcat="dvr", is_gravador=True),
            _dev("DVR-B", "DVR-B", subcat="dvr", is_gravador=True),
        ],
    }
    v = montar_visao(dados, {}, {}, {}, duplicado_de={"DVR-B": "DVR-A"})
    (card,) = v["cards"]
    assert card["gravador_host"]["host"] == "DVR-A"
    assert [d["host"] for d in card["dispositivos"]] == ["DVR-B"]


# ── Migração ──────────────────────────────────────────────────────────────────


def test_garantir_colunas_cria_apelido_e_duplicado(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'antigo.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE unidades (id INTEGER PRIMARY KEY, nome VARCHAR(120) NOT NULL)"))
        conn.execute(text("CREATE TABLE dvr_unidades (id INTEGER PRIMARY KEY, dvr VARCHAR(120), unidade_id INTEGER)"))
        conn.execute(text("INSERT INTO dvr_unidades (dvr) VALUES ('DVR-1')"))

    garantir_colunas(engine)
    garantir_colunas(engine)

    assert {"apelido", "duplicado_de"} <= {c["name"] for c in inspect(engine).get_columns("dvr_unidades")}
    with engine.connect() as conn:
        assert conn.execute(text("SELECT apelido, duplicado_de FROM dvr_unidades")).one() == ("", "")


# ── Rotas ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def com_zabbix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZABBIX_URL", "https://zabbix.test")


@pytest.fixture
def gestor_client(factory_app) -> Iterator:
    from app.models.user import User

    with factory_app.app_context():
        user = User.query.filter_by(email="pytest-gestor-cftv@test.local").first()
        if user is None:
            user = User(name="Pytest Gestor", email="pytest-gestor-cftv@test.local", role="gestor")
            user.set_password("pytest-only-not-real")
            db.session.add(user)
            db.session.commit()
        uid = user.id
    with factory_app.test_client() as c:
        with c.session_transaction() as sess:
            sess["_user_id"] = str(uid)
            sess["_fresh"] = True
        yield c


@pytest.fixture
def limpa_vinculos(factory_app) -> Iterator[None]:
    yield
    with factory_app.app_context():
        DvrUnidade.query.filter(DvrUnidade.dvr.like("%Gestao%")).delete(synchronize_session=False)
        db.session.commit()


def _vinculo(factory_app, dvr: str) -> DvrUnidade | None:
    with factory_app.app_context():
        v = DvrUnidade.query.filter_by(dvr=dvr).first()
        if v is not None:
            db.session.expunge(v)
        return v


def test_pagina_sem_filtro_mostra_sedes_e_nao_gravadores(authed_client, com_zabbix: None) -> None:
    with patch.object(cftv_monitoring, "get_cached_cftv_summary", return_value=_DADOS):
        html = authed_client.get("/gov/cftv").get_data(as_text=True)
    assert "Ver gravadores →" in html
    assert "Todas as sedes" in html
    assert "Ver todos os gravadores de uma vez" in html
    # Nível 2 não aparece sem escolher sede
    assert "Editar gravador (admin)" not in html


def test_pagina_sede_mostra_filhas_e_trilha(authed_client, com_zabbix: None, factory_app) -> None:
    with factory_app.app_context():
        shopping = Unidade.query.filter_by(nome="Shopping", parent_id=None).one()
        filha = Unidade(nome="Bloco Gestao", parent_id=shopping.id)
        db.session.add(filha)
        db.session.commit()
        sid, fid = shopping.id, filha.id
    try:
        with patch.object(cftv_monitoring, "get_cached_cftv_summary", return_value=_DADOS):
            html = authed_client.get(f"/gov/cftv?unidade={fid}").get_data(as_text=True)
        assert "Shopping inteira" in html
        assert f'href="/gov/cftv?unidade={sid}"' in html
        assert "› Bloco Gestao" in html
    finally:
        with factory_app.app_context():
            db.session.delete(db.session.get(Unidade, fid))
            db.session.commit()


def test_admin_ve_edicao_de_gravador(authed_client, com_zabbix: None) -> None:
    with patch.object(cftv_monitoring, "get_cached_cftv_summary", return_value=_DADOS):
        html = authed_client.get("/gov/cftv?ver=gravadores").get_data(as_text=True)
    assert "Editar gravador (admin)" in html
    assert 'action="/gov/cftv/gravador/nome"' in html


def test_gestor_nao_ve_nem_usa_edicao(gestor_client, com_zabbix: None) -> None:
    with patch.object(cftv_monitoring, "get_cached_cftv_summary", return_value=_DADOS):
        html = gestor_client.get("/gov/cftv?ver=gravadores").get_data(as_text=True)
    assert "Editar gravador (admin)" not in html
    # Gestor continua podendo definir a unidade
    assert 'action="/gov/cftv/gravador"' in html
    assert gestor_client.post("/gov/cftv/gravador/nome", data={"gravador": "X", "apelido": "Y"}).status_code == 403
    assert (
        gestor_client.post("/gov/cftv/gravador/duplicado", data={"gravador": "X", "duplicado_de": "Y"}).status_code
        == 403
    )


def test_renomear_e_voltar_ao_nome_do_zabbix(authed_client, factory_app, limpa_vinculos: None) -> None:
    resp = authed_client.post(
        "/gov/cftv/gravador/nome", data={"gravador": "Térreo Gestao", "apelido": "  Garagem   Térreo "}
    )
    assert resp.status_code == 302
    assert _vinculo(factory_app, "Térreo Gestao").apelido == "Garagem Térreo"

    authed_client.post("/gov/cftv/gravador/nome", data={"gravador": "Térreo Gestao", "apelido": ""})
    assert _vinculo(factory_app, "Térreo Gestao").apelido == ""


def test_renomear_invalido_400(authed_client) -> None:
    assert authed_client.post("/gov/cftv/gravador/nome", data={"gravador": "", "apelido": "x"}).status_code == 400
    assert (
        authed_client.post("/gov/cftv/gravador/nome", data={"gravador": "X", "apelido": "x" * 121}).status_code == 400
    )


def test_unir_separar_e_recusar_ciclo(authed_client, factory_app, limpa_vinculos: None) -> None:
    authed_client.post("/gov/cftv/gravador/duplicado", data={"gravador": "A Gestao", "duplicado_de": "B Gestao"})
    assert _vinculo(factory_app, "A Gestao").duplicado_de == "B Gestao"

    # B → A fecharia ciclo
    resp = authed_client.post(
        "/gov/cftv/gravador/duplicado", data={"gravador": "B Gestao", "duplicado_de": "A Gestao"}, follow_redirects=True
    )
    assert "Não dá para unir" in resp.get_data(as_text=True)
    assert _vinculo(factory_app, "B Gestao") is None

    # Unir a si mesmo também é recusado
    authed_client.post("/gov/cftv/gravador/duplicado", data={"gravador": "C Gestao", "duplicado_de": "C Gestao"})
    assert _vinculo(factory_app, "C Gestao") is None

    authed_client.post("/gov/cftv/gravador/duplicado", data={"gravador": "A Gestao", "duplicado_de": ""})
    assert _vinculo(factory_app, "A Gestao").duplicado_de == ""


def test_unir_preserva_unidade_do_vinculo(authed_client, factory_app, limpa_vinculos: None) -> None:
    with factory_app.app_context():
        uid = Unidade.query.filter_by(nome="Shopping", parent_id=None).one().id
        db.session.add(DvrUnidade(dvr="D Gestao", unidade_id=uid))
        db.session.commit()
    authed_client.post("/gov/cftv/gravador/duplicado", data={"gravador": "D Gestao", "duplicado_de": "E Gestao"})
    v = _vinculo(factory_app, "D Gestao")
    assert (v.unidade_id, v.duplicado_de) == (uid, "E Gestao")
