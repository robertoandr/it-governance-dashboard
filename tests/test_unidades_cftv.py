"""Unidades (sites), vínculo gravador → unidade e página /cftv por gravador."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import pytest

from app.extensions import db
from app.models.unidade import DVRS_PADRAO, UNIDADES_PADRAO, DvrUnidade, Unidade, parse_faixas
from itgov.api.v1 import cftv_monitoring
from itgov.api.v1.cftv_monitoring import SEM_UNIDADE, gravador_do_dispositivo, montar_visao


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
        _dev("DVR-1", "DVR-1", subcat="dvr", is_gravador=True, name="DVR-1 · 9º/8º", ip="172.29.11.17"),
        _dev("cam-dvr1-02", "DVR-1", canal="2"),
        _dev("cam-dvr1-10", "DVR-1", status="down", canal="10"),
        _dev("cam-dvr1-01", "DVR-1", canal="1", vendor="Intelbras", model="VIP 3230"),
        _dev("cam-hauer-01", "ADM HAUER", status="nodata", canal="1", vendor="Intelbras"),
        _dev("facial-01", "", subcat="facial", loja="Sede Centro"),
    ],
}


@pytest.fixture
def unidades_ids(factory_app) -> dict[str, int]:
    with factory_app.app_context():
        return {u.nome: u.id for u in Unidade.query.filter_by(parent_id=None)}


@pytest.fixture
def com_zabbix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZABBIX_URL", "https://zabbix.test")


@pytest.fixture
def operador_client(factory_app) -> Iterator:
    from app.models.user import User

    with factory_app.app_context():
        user = User.query.filter_by(email="pytest-operador@test.local").first()
        if user is None:
            user = User(name="Pytest Operador", email="pytest-operador@test.local", role="operador")
            user.set_password("pytest-only-not-real")
            db.session.add(user)
            db.session.commit()
        uid = user.id
    with factory_app.test_client() as c:
        with c.session_transaction() as sess:
            sess["_user_id"] = str(uid)
            sess["_fresh"] = True
        yield c


# ── Model ─────────────────────────────────────────────────────────────────────


def test_parse_faixas_normaliza_e_deduplica() -> None:
    assert parse_faixas("10.41.0.5/16, 172.29.0.0/22\n10.41.0.0/16") == ["10.41.0.0/16", "172.29.0.0/22"]
    assert parse_faixas("") == []


def test_parse_faixas_rejeita_invalida() -> None:
    with pytest.raises(ValueError, match=r"10\.999\.0\.0/16"):
        parse_faixas("10.999.0.0/16")


def test_seed_cria_unidades_e_gravadores_padrao(factory_app) -> None:
    with factory_app.app_context():
        nomes = {u.nome for u in Unidade.query.filter_by(parent_id=None)}
        assert set(UNIDADES_PADRAO) <= nomes
        vinculo = DvrUnidade.query.filter_by(dvr="Triunfo 2").one()
        assert vinculo.unidade.nome == DVRS_PADRAO["Triunfo 2"]


def test_seed_nao_repete_quando_ja_existe(factory_app) -> None:
    from app.models.unidade import seed_unidades

    with factory_app.app_context():
        antes = Unidade.query.count()
        seed_unidades()
        assert Unidade.query.count() == antes


def test_caminho_e_subarvore(factory_app, unidades_ids: dict[str, int]) -> None:
    with factory_app.app_context():
        obra = Unidade(nome="Obra Teste Subarvore", parent_id=unidades_ids["Obras"])
        db.session.add(obra)
        db.session.commit()
        try:
            assert obra.caminho == "Obras / Obra Teste Subarvore"
            obras = db.session.get(Unidade, unidades_ids["Obras"])
            assert obras.ids_subarvore() >= {obras.id, obra.id}
        finally:
            db.session.delete(obra)
            db.session.commit()


# ── Agrupamento por gravador ──────────────────────────────────────────────────


def test_gravador_do_dispositivo() -> None:
    assert gravador_do_dispositivo("DVR-1", "dvr", {}) == "DVR-1"
    assert gravador_do_dispositivo("cam-x", "camera", {"dvr": "Triunfo 2"}) == "Triunfo 2"
    assert gravador_do_dispositivo("cam-hik", "camera", {"parent_nvr": "nvr-centro-01"}) == "nvr-centro-01"
    assert gravador_do_dispositivo("facial-1", "facial", {"loja": "Sede Centro"}) == ""


def _visao(filtro: set[int] | str | None = None) -> dict:
    return montar_visao(
        _DADOS,
        unidade_por_gravador={"DVR-1": 1},
        unidades={1: "Sede Centro", 2: "Obras / Hauer"},
        unidade_por_loja={"sede centro": 1},
        filtro=filtro,
    )


def test_montar_visao_um_card_por_gravador() -> None:
    v = _visao()
    por_gravador = {c["gravador"]: c for c in v["cards"]}
    dvr1 = por_gravador["DVR-1"]

    assert dvr1["titulo"] == "DVR-1 · 9º/8º"
    assert dvr1["gravador_host"]["ip"] == "172.29.11.17"
    assert dvr1["unidade"] == "Sede Centro"
    # O próprio DVR não conta como câmera do card; canais em ordem numérica
    assert [d["canal"] for d in dvr1["dispositivos"]] == ["1", "2", "10"]
    assert (dvr1["total"], dvr1["up"], dvr1["down"]) == (3, 2, 1)
    assert dvr1["vendors"] == ["Intelbras"]

    hauer = por_gravador["ADM HAUER"]
    assert hauer["unidade_id"] is None and hauer["gravador_host"] is None
    assert hauer["titulo"] == "ADM HAUER"

    # Sem gravador: unidade cai para a tag loja
    sem = por_gravador[""]
    assert sem["titulo"] == "Sem gravador" and sem["unidade_id"] == 1

    # Card com câmera offline vem primeiro
    assert v["cards"][0]["gravador"] == "DVR-1"
    assert (v["total"], v["down"], v["nodata"]) == (6, 1, 1)
    assert [d["name"] for d in v["down_list"]] == ["cam-dvr1-10"]


def test_montar_visao_filtra_por_unidade() -> None:
    v = _visao(filtro={1})
    assert {c["gravador"] for c in v["cards"]} == {"DVR-1", ""}
    assert v["total"] == 5


def test_montar_visao_filtra_sem_unidade() -> None:
    v = _visao(filtro=SEM_UNIDADE)
    assert [c["gravador"] for c in v["cards"]] == ["ADM HAUER"]
    assert v["total"] == 1


def test_montar_visao_sem_dados() -> None:
    v = montar_visao({"enabled": False, "devices": []}, {}, {}, {})
    assert v["cards"] == [] and v["total"] == 0 and v["up_pct"] == 0.0


# ── Página /cftv ──────────────────────────────────────────────────────────────


def test_cftv_page_renderiza_cards_e_filtro(authed_client, com_zabbix: None) -> None:
    with patch.object(cftv_monitoring, "get_cached_cftv_summary", return_value=_DADOS):
        html = authed_client.get("/gov/cftv").get_data(as_text=True)
    for trecho in (
        "DVR-1 · 9º/8º",
        "ADM HAUER",
        "Sem unidade",
        "Intelbras VIP 3230",
        "Triunfo Fábrica",
    ):
        assert trecho in html, trecho
    assert "Ver 3 dispositivos" in html
    assert 'name="gravador" value="ADM HAUER"' in html


def test_cftv_page_filtro_sem_unidade(authed_client, com_zabbix: None) -> None:
    with patch.object(cftv_monitoring, "get_cached_cftv_summary", return_value=_DADOS):
        html = authed_client.get("/gov/cftv?unidade=sem").get_data(as_text=True)
    assert "ADM HAUER" in html
    assert "DVR-1 · 9º/8º" not in html


def test_cftv_page_filtro_unidade_pai_inclui_filhas(authed_client, com_zabbix: None, factory_app, unidades_ids) -> None:
    with factory_app.app_context():
        obra = Unidade(nome="Hauer Teste Filtro", parent_id=unidades_ids["Obras"])
        db.session.add(obra)
        db.session.commit()
        db.session.add(DvrUnidade(dvr="ADM HAUER", unidade_id=obra.id))
        db.session.commit()
        obra_id = obra.id
    try:
        with patch.object(cftv_monitoring, "get_cached_cftv_summary", return_value=_DADOS):
            html = authed_client.get(f"/gov/cftv?unidade={unidades_ids['Obras']}").get_data(as_text=True)
        assert "Obras / Hauer Teste Filtro" in html
        assert "DVR-1 · 9º/8º" not in html
    finally:
        with factory_app.app_context():
            DvrUnidade.query.filter_by(dvr="ADM HAUER").delete()
            db.session.delete(db.session.get(Unidade, obra_id))
            db.session.commit()


def test_cftv_page_operador_nao_ve_form_de_unidade(operador_client, com_zabbix: None) -> None:
    with patch.object(cftv_monitoring, "get_cached_cftv_summary", return_value=_DADOS):
        html = operador_client.get("/gov/cftv").get_data(as_text=True)
    assert "DVR-1 · 9º/8º" in html
    assert 'name="gravador"' not in html


def test_cftv_page_404_sem_zabbix(authed_client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ZABBIX_URL", raising=False)
    assert authed_client.get("/gov/cftv").status_code == 404


def test_definir_unidade_do_gravador(authed_client, factory_app, unidades_ids) -> None:
    resp = authed_client.post(
        "/gov/cftv/gravador",
        data={"gravador": "Loja Útil Teste", "unidade_id": str(unidades_ids["Autoshop"]), "filtro": "sem"},
    )
    assert resp.status_code == 302
    assert "unidade=sem" in resp.headers["Location"]
    try:
        with factory_app.app_context():
            assert DvrUnidade.query.filter_by(dvr="Loja Útil Teste").one().unidade_id == unidades_ids["Autoshop"]
        authed_client.post("/gov/cftv/gravador", data={"gravador": "Loja Útil Teste", "unidade_id": ""})
        with factory_app.app_context():
            assert DvrUnidade.query.filter_by(dvr="Loja Útil Teste").one().unidade_id is None
    finally:
        with factory_app.app_context():
            DvrUnidade.query.filter_by(dvr="Loja Útil Teste").delete()
            db.session.commit()


def test_definir_unidade_invalida_400(authed_client) -> None:
    assert authed_client.post("/gov/cftv/gravador", data={"gravador": "X", "unidade_id": "99999"}).status_code == 400
    assert authed_client.post("/gov/cftv/gravador", data={"gravador": "", "unidade_id": ""}).status_code == 400


def test_operador_nao_define_unidade(operador_client) -> None:
    assert operador_client.post("/gov/cftv/gravador", data={"gravador": "X"}).status_code == 403


# ── Cadastro de unidades ──────────────────────────────────────────────────────


def test_lista_unidades_mostra_arvore_e_gravadores(authed_client) -> None:
    html = authed_client.get("/gov/unidades").get_data(as_text=True)
    for nome in UNIDADES_PADRAO:
        assert nome in html
    assert "Shopping 1" in html


def test_criar_editar_remover_obra(authed_client, factory_app, unidades_ids) -> None:
    resp = authed_client.post(
        "/gov/unidades/nova",
        data={"nome": "Residencial Teste", "parent_id": str(unidades_ids["Obras"]), "faixas_ip": "10.50.1.0/24"},
    )
    assert resp.status_code == 302
    with factory_app.app_context():
        obra = Unidade.query.filter_by(nome="Residencial Teste").one()
        assert obra.caminho == "Obras / Residencial Teste"
        assert obra.faixas == ["10.50.1.0/24"]
        assert obra.ativo is True
        obra_id = obra.id

    authed_client.post(
        f"/gov/unidades/{obra_id}/editar",
        data={"nome": "Residencial Teste", "parent_id": str(unidades_ids["Obras"]), "faixas_ip": ""},
    )
    with factory_app.app_context():
        obra = db.session.get(Unidade, obra_id)
        assert obra.faixas == [] and obra.ativo is False  # checkbox desmarcado

    resp = authed_client.post(f"/gov/unidades/{obra_id}/editar", data={"action": "delete"})
    assert resp.status_code == 302
    with factory_app.app_context():
        assert db.session.get(Unidade, obra_id) is None


def test_criar_unidade_faixa_invalida_mostra_erro(authed_client, factory_app) -> None:
    resp = authed_client.post("/gov/unidades/nova", data={"nome": "Faixa Ruim", "faixas_ip": "300.1.1.0/24"})
    assert resp.status_code == 200
    assert "faixa de IP inválida" in resp.get_data(as_text=True)
    with factory_app.app_context():
        assert Unidade.query.filter_by(nome="Faixa Ruim").first() is None


def test_criar_unidade_duplicada_no_mesmo_nivel(authed_client) -> None:
    resp = authed_client.post("/gov/unidades/nova", data={"nome": "Shopping"})
    assert resp.status_code == 200
    assert "Já existe a unidade Shopping" in resp.get_data(as_text=True)


def test_nao_remove_unidade_com_filhas(authed_client, factory_app, unidades_ids) -> None:
    with factory_app.app_context():
        filha = Unidade(nome="Filha Bloqueio", parent_id=unidades_ids["Obras"])
        db.session.add(filha)
        db.session.commit()
        filha_id = filha.id
    try:
        resp = authed_client.post(f"/gov/unidades/{unidades_ids['Obras']}/editar", data={"action": "delete"})
        assert "Remova ou mova as unidades filhas" in resp.get_data(as_text=True)
        with factory_app.app_context():
            assert db.session.get(Unidade, unidades_ids["Obras"]) is not None
    finally:
        with factory_app.app_context():
            db.session.delete(db.session.get(Unidade, filha_id))
            db.session.commit()


def test_remover_unidade_desvincula_gravadores(authed_client, factory_app) -> None:
    with factory_app.app_context():
        u = Unidade(nome="Temporaria Gravador")
        db.session.add(u)
        db.session.commit()
        db.session.add(DvrUnidade(dvr="DVR Temporario", unidade_id=u.id))
        db.session.commit()
        uid = u.id
    authed_client.post(f"/gov/unidades/{uid}/editar", data={"action": "delete"})
    with factory_app.app_context():
        vinculo = DvrUnidade.query.filter_by(dvr="DVR Temporario").one()
        assert vinculo.unidade_id is None
        db.session.delete(vinculo)
        db.session.commit()


def test_operador_nao_acessa_formulario(operador_client) -> None:
    assert operador_client.get("/gov/unidades").status_code == 200
    assert operador_client.get("/gov/unidades/nova").status_code == 403
