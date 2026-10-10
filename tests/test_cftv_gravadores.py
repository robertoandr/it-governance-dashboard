"""CFTV: leitura dos gravadores pela API Intelbras, credencial por DVR e gravadores pela nuvem."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy import create_engine, inspect, text

from app.extensions import db
from app.models.unidade import DvrUnidade, Unidade, garantir_colunas
from app.services import cftv_gravadores as cg
from itgov.api.v1 import cftv_monitoring
from itgov.api.v1.cftv_monitoring import montar_visao
from itgov.services import intelbras_api
from itgov.services.intelbras_api import AcessoNegadoError, LeituraGravador

_AGORA = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
_ENV_CREDENCIAIS = [v for par in cg.CREDENCIAIS.values() for v in par]


@pytest.fixture
def sem_credenciais(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for var in _ENV_CREDENCIAIS:
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


def _acesso(**kw: object) -> SimpleNamespace:
    base = {"credencial": "", "falhas": "{}", "leitura": "", "erro": "", "lido_em": None}
    base.update(kw)
    return SimpleNamespace(**base)


# ── Credenciais ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("nome", "esperado"),
    [
        ("Triunfo_2_Folha2", "CFTV_DVR_TRIUNFO_2_FOLHA2"),
        ("Shopping _R3", "CFTV_DVR_SHOPPING_R3"),
        ("Shopping_Corredor_Técnico", "CFTV_DVR_SHOPPING_CORREDOR_TECNICO"),
        ("Sede_5_-1", "CFTV_DVR_SEDE_5_1"),
    ],
)
def test_chave_env_normaliza_nome(nome: str, esperado: str) -> None:
    assert cg.chave_env(nome) == esperado


def test_credencial_propria_completa_com_a_padrao(sem_credenciais: pytest.MonkeyPatch) -> None:
    m = sem_credenciais
    m.setenv("CFTV_CAM_PADRAO_USER", "padrao")
    m.setenv("CFTV_CAM_PADRAO_PASS", "senha-padrao")
    m.setenv("CFTV_DVR_TRIUNFO_1_FOLHA_USER", "folha")
    m.setenv("CFTV_DVR_TRIUNFO_2_FOLHA2_PASS", "senha-folha2")

    assert cg.credencial_propria("Triunfo_1_Folha") == ("folha", "senha-padrao")
    assert cg.credencial_propria("Triunfo_2_Folha2") == ("padrao", "senha-folha2")
    assert cg.credencial_propria("Shopping_1") is None
    assert cg.credencial_propria("") is None


def test_credencial_propria_sem_padrao_para_completar(sem_credenciais: pytest.MonkeyPatch) -> None:
    sem_credenciais.setenv("CFTV_DVR_TRIUNFO_1_FOLHA_USER", "folha")
    assert cg.credencial_propria("Triunfo_1_Folha") is None


def test_ordem_credenciais(sem_credenciais: pytest.MonkeyPatch) -> None:
    m = sem_credenciais
    for nome in ("centro", "padrao", "principal"):
        u, p = cg.CREDENCIAIS[nome]
        m.setenv(u, nome)
        m.setenv(p, nome)
    m.setenv("CFTV_DVR_TRIUNFO_4_EXTRATO_PASS", "x")

    assert cg.ordem_credenciais(centro=True) == ["centro", "padrao", "principal"]
    assert cg.ordem_credenciais(centro=False, nome="Shopping_1") == ["padrao", "principal"]
    # Com credencial própria, só ela é tentada (não gasta tentativas com outras)
    assert cg.ordem_credenciais(centro=False, nome="Triunfo_4_Extrato") == [cg.PROPRIA]
    assert cg._usuario_senha(cg.PROPRIA, "Triunfo_4_Extrato") == ("padrao", "x")


# ── ler_um ────────────────────────────────────────────────────────────────────


@pytest.fixture
def padrao_e_principal(sem_credenciais: pytest.MonkeyPatch) -> None:
    for nome in ("padrao", "principal"):
        u, p = cg.CREDENCIAIS[nome]
        sem_credenciais.setenv(u, f"u-{nome}")
        sem_credenciais.setenv(p, f"p-{nome}")


def test_ler_um_guarda_leitura_e_credencial(padrao_e_principal: None) -> None:
    acesso = _acesso()
    leitura = LeituraGravador(modelo="MHDX 3132", canais=32)
    with patch.object(cg, "ler_gravador", return_value=leitura) as ler:
        cg.ler_um("10.0.0.1", False, acesso, _AGORA)
    ler.assert_called_once_with("10.0.0.1", "u-padrao", "p-padrao")
    assert acesso.credencial == "padrao"
    assert LeituraGravador.model_validate_json(acesso.leitura).canais == 32
    assert acesso.lido_em == _AGORA


def test_ler_um_para_na_primeira_recusa(padrao_e_principal: None) -> None:
    acesso = _acesso()
    with patch.object(cg, "ler_gravador", side_effect=AcessoNegadoError("x")) as ler:
        cg.ler_um("10.0.0.1", False, acesso, _AGORA)
    assert ler.call_count == 1
    assert set(json.loads(acesso.falhas)) == {"padrao"}
    assert acesso.erro == "credencial recusada"

    # Logo depois: espera ESPERA_RECUSA antes de tentar outra credencial
    with patch.object(cg, "ler_gravador") as ler:
        cg.ler_um("10.0.0.1", False, acesso, _AGORA + timedelta(minutes=10))
    ler.assert_not_called()

    # Passada a espera, tenta a próxima (a recusada fica de fora por ESPERA_FALHA)
    with patch.object(cg, "ler_gravador", return_value=LeituraGravador()) as ler:
        cg.ler_um("10.0.0.1", False, acesso, _AGORA + cg.ESPERA_RECUSA + timedelta(minutes=1))
    ler.assert_called_once_with("10.0.0.1", "u-principal", "p-principal")
    assert acesso.credencial == "principal"


def test_ler_um_recusa_da_credencial_que_funcionava_limpa(padrao_e_principal: None) -> None:
    acesso = _acesso(credencial="principal")
    with patch.object(cg, "ler_gravador", side_effect=AcessoNegadoError("x")) as ler:
        cg.ler_um("10.0.0.1", False, acesso, _AGORA)
    ler.assert_called_once_with("10.0.0.1", "u-principal", "p-principal")
    assert acesso.credencial == ""


def test_ler_um_sem_resposta(padrao_e_principal: None) -> None:
    acesso = _acesso()
    with patch.object(cg, "ler_gravador", side_effect=httpx.ConnectTimeout("t")):
        cg.ler_um("10.0.0.1", False, acesso, _AGORA)
    assert acesso.erro == "sem resposta: ConnectTimeout"
    assert json.loads(acesso.falhas) == {}


def test_ler_um_sem_credencial(sem_credenciais: pytest.MonkeyPatch) -> None:
    acesso = _acesso()
    cg.ler_um("10.0.0.1", False, acesso, _AGORA)
    assert acesso.erro == "nenhuma credencial configurada"


def test_ler_um_todas_recusadas_recentemente(padrao_e_principal: None) -> None:
    antes = (_AGORA - cg.ESPERA_RECUSA - timedelta(minutes=1)).isoformat()
    acesso = _acesso(falhas=json.dumps({"padrao": antes, "principal": antes}))
    with patch.object(cg, "ler_gravador") as ler:
        cg.ler_um("10.0.0.1", False, acesso, _AGORA)
    ler.assert_not_called()
    assert acesso.erro == "nenhuma credencial aceita"


def test_ler_um_usa_credencial_propria(sem_credenciais: pytest.MonkeyPatch) -> None:
    sem_credenciais.setenv("CFTV_CAM_PADRAO_USER", "padrao")
    sem_credenciais.setenv("CFTV_CAM_PADRAO_PASS", "senha-padrao")
    sem_credenciais.setenv("CFTV_DVR_TRIUNFO_2_FOLHA2_PASS", "propria")
    acesso = _acesso()
    with patch.object(cg, "ler_gravador", return_value=LeituraGravador()) as ler:
        cg.ler_um("172.17.1.206", False, acesso, _AGORA, "Triunfo_2_Folha2")
    ler.assert_called_once_with("172.17.1.206", "padrao", "propria")
    assert acesso.credencial == cg.PROPRIA


# ── canais_do_card ────────────────────────────────────────────────────────────


def _card(*canais: str, model: str = "") -> dict:
    return {"dispositivos": [{"canal": c} for c in canais], "gravador_host": {"model": model}}


def test_canais_pela_leitura_do_gravador() -> None:
    leitura = LeituraGravador(modelo="MHDX 1108", canais=8, sem_video=[2, 7], titulos={2: "Portão", 7: "CAM 7"})
    k = cg.canais_do_card(_card("1"), {"leitura": leitura}, None)
    assert (k["total"], k["fonte"]) == (8, "gravador")
    # Canal 7 tem nome de fábrica e está sem vídeo: livre, não alerta
    assert k["sem_video"] == [{"canal": 2, "nome": "Portão"}]
    assert k["usados"] == 7 and k["livres"] == 1


def test_canais_manual_e_pelo_modelo() -> None:
    assert cg.canais_do_card(_card("1", "2"), None, 16)["fonte"] == "informado"
    k = cg.canais_do_card(_card("1", "2", model="NVD 3316"), None, None)
    assert (k["total"], k["livres"], k["fonte"]) == (16, 14, "modelo")
    assert cg.canais_do_card(_card("1"), None, None) is None


# ── intelbras_api ─────────────────────────────────────────────────────────────


def test_capacidade_e_titulo_padrao() -> None:
    assert intelbras_api.capacidade_do_modelo("MHDX 3132") == 32
    assert intelbras_api.capacidade_do_modelo("DS-7632NXI-K2") == 32
    assert intelbras_api.capacidade_do_modelo("iMHDX 31") == 0
    assert intelbras_api.titulo_padrao("CAM 04")
    assert intelbras_api.titulo_padrao("")
    assert not intelbras_api.titulo_padrao("Portaria")


def _transporte(rotas: dict[str, tuple[int, str]]) -> httpx.MockTransport:
    def handler(req: httpx.Request) -> httpx.Response:
        chave = req.url.path.rsplit("/", 1)[-1] + "?" + req.url.query.decode()
        status, corpo = rotas.get(chave, (404, ""))
        return httpx.Response(status, text=corpo, headers={"www-authenticate": 'Basic realm="x"'})

    return httpx.MockTransport(handler)


def _cliente(monkeypatch: pytest.MonkeyPatch, rotas: dict[str, tuple[int, str]]) -> None:
    real = httpx.Client
    monkeypatch.setattr(intelbras_api.httpx, "Client", lambda timeout: real(transport=_transporte(rotas)))


def test_ler_gravador_monta_leitura(monkeypatch: pytest.MonkeyPatch) -> None:
    _cliente(
        monkeypatch,
        {
            "magicBox.cgi?action=getSerialNo": (200, "sn=ABC123\r\n"),
            "magicBox.cgi?action=getDeviceType": (200, "type=MHDX 1108\r\n"),
            "devVideoInput.cgi?action=getCollect": (200, "result=8\r\n"),
            "eventManager.cgi?action=getEventIndexes&code=VideoLoss": (200, "channels[0]=1\r\nchannels[1]=6\r\n"),
            "configManager.cgi?action=getConfig&name=ChannelTitle": (
                200,
                "table.ChannelTitle[0].Name=CAM 1\r\ntable.ChannelTitle[1].Name=Portão\r\n",
            ),
        },
    )
    leitura = intelbras_api.ler_gravador("10.0.0.9", "u", "p")
    assert leitura.modelo == "MHDX 1108"
    assert leitura.serie == "ABC123"
    assert leitura.canais == 8
    assert leitura.sem_video == [2, 7]
    assert leitura.titulos == {1: "CAM 1", 2: "Portão"}


def test_ler_gravador_recusa(monkeypatch: pytest.MonkeyPatch) -> None:
    _cliente(monkeypatch, {"magicBox.cgi?action=getSerialNo": (401, "")})
    with pytest.raises(AcessoNegadoError):
        intelbras_api.ler_gravador("10.0.0.9", "u", "errada")


# ── Card: host do gravador pela tag dvr ───────────────────────────────────────


def test_host_do_gravador_achado_pela_tag_dvr() -> None:
    def dev(host: str, gravador: str, **kw: object) -> dict:
        base = {"host": host, "name": host, "ip": "10.0.0.1", "subcat": "camera", "andar": "?", "gravador": gravador}
        base |= {"is_gravador": False, "canal": "", "loja": "", "vendor": "", "model": "", "status": "up"}
        return base | {"problems": 0, **kw}

    dados = {
        "enabled": True,
        "devices": [
            dev("dvr-adm_hauer", "ADM HAUER", subcat="dvr", is_gravador=True, ip="10.41.100.251", name="Adm_Hauer"),
            dev("cam-1", "ADM HAUER", canal="1"),
        ],
    }
    (card,) = montar_visao(dados, {}, {}, {})["cards"]
    assert card["gravador_host"]["ip"] == "10.41.100.251"
    assert card["titulo"] == "Adm_Hauer"
    assert [d["host"] for d in card["dispositivos"]] == ["cam-1"]


# ── Migração ──────────────────────────────────────────────────────────────────


def test_garantir_colunas_cria_acesso_modelo_serie(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'antigo.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE unidades (id INTEGER PRIMARY KEY, nome VARCHAR(120) NOT NULL)"))
        conn.execute(text("CREATE TABLE dvr_unidades (id INTEGER PRIMARY KEY, dvr VARCHAR(120), unidade_id INTEGER)"))
        conn.execute(text("INSERT INTO dvr_unidades (dvr) VALUES ('DVR-1')"))

    garantir_colunas(engine)

    colunas = {c["name"] for c in inspect(engine).get_columns("dvr_unidades")}
    assert {"canais", "acesso", "modelo", "serie"} <= colunas
    with engine.connect() as conn:
        assert conn.execute(text("SELECT acesso, modelo, serie FROM dvr_unidades")).one() == ("", "", "")


# ── Rotas: gravadores pela nuvem ──────────────────────────────────────────────


@pytest.fixture
def com_zabbix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZABBIX_URL", "https://zabbix.test")


@pytest.fixture
def limpa(factory_app) -> Iterator[None]:
    yield
    with factory_app.app_context():
        DvrUnidade.query.filter(DvrUnidade.dvr.like("%Nuvem%")).delete(synchronize_session=False)
        db.session.commit()


def _vinculo(factory_app, dvr: str) -> DvrUnidade | None:
    with factory_app.app_context():
        v = DvrUnidade.query.filter_by(dvr=dvr).first()
        if v is not None:
            db.session.expunge(v)
        return v


def test_cadastrar_alterar_e_remover_gravador_nuvem(authed_client, factory_app, limpa: None) -> None:
    with factory_app.app_context():
        shopping = Unidade.query.filter_by(nome="Shopping", parent_id=None).one().id

    dados = {"nome": " Casa  Nuvem ", "unidade_id": str(shopping), "modelo": "MHDX 1108", "serie": "V2YH1600077VH"}
    assert authed_client.post("/gov/cftv/gravador/nuvem", data=dados).status_code == 302
    v = _vinculo(factory_app, "Casa Nuvem")
    assert (v.acesso, v.modelo, v.serie, v.unidade_id) == ("nuvem", "MHDX 1108", "V2YH1600077VH", shopping)

    authed_client.post("/gov/cftv/gravador/nuvem", data={"nome": "Casa Nuvem", "modelo": "MHDX 1116"})
    v = _vinculo(factory_app, "Casa Nuvem")
    assert (v.modelo, v.serie, v.unidade_id) == ("MHDX 1116", "", None)

    authed_client.post("/gov/cftv/gravador/nuvem", data={"nome": "Casa Nuvem", "remover": "1"})
    assert _vinculo(factory_app, "Casa Nuvem") is None


def test_nuvem_nao_sobrescreve_gravador_da_rede(authed_client, factory_app, limpa: None) -> None:
    with factory_app.app_context():
        db.session.add(DvrUnidade(dvr="Rede Nuvem"))
        db.session.commit()
    authed_client.post("/gov/cftv/gravador/nuvem", data={"nome": "Rede Nuvem", "modelo": "X"})
    assert _vinculo(factory_app, "Rede Nuvem").acesso == ""
    # Remover pela rota da nuvem também não apaga gravador da rede
    assert (
        authed_client.post("/gov/cftv/gravador/nuvem", data={"nome": "Rede Nuvem", "remover": "1"}).status_code == 404
    )


def test_nuvem_entrada_invalida(authed_client) -> None:
    assert authed_client.post("/gov/cftv/gravador/nuvem", data={"nome": " "}).status_code == 400
    assert authed_client.post("/gov/cftv/gravador/nuvem", data={"nome": "x" * 121}).status_code == 400
    assert (
        authed_client.post("/gov/cftv/gravador/nuvem", data={"nome": "A Nuvem", "unidade_id": "999999"}).status_code
        == 400
    )


def test_pagina_lista_gravadores_nuvem(authed_client, com_zabbix: None, factory_app, limpa: None) -> None:
    with factory_app.app_context():
        db.session.add(DvrUnidade(dvr="Praia Nuvem", acesso="nuvem", modelo="MHDX 1108", serie="SERIE-NUVEM"))
        db.session.commit()
    with patch.object(cftv_monitoring, "get_cached_cftv_summary", return_value={"enabled": True, "devices": []}):
        html = authed_client.get("/gov/cftv").get_data(as_text=True)
    assert "Gravadores pela nuvem (Intelbras Cloud)" in html
    assert "SERIE-NUVEM" in html
    assert 'action="/gov/cftv/gravador/nuvem"' in html


def test_gestor_nao_cadastra_nuvem(factory_app) -> None:
    from app.models.user import User

    with factory_app.app_context():
        user = User.query.filter_by(email="pytest-gestor-nuvem@test.local").first()
        if user is None:
            user = User(name="Pytest Gestor", email="pytest-gestor-nuvem@test.local", role="gestor")
            user.set_password("pytest-only-not-real")
            db.session.add(user)
            db.session.commit()
        uid = user.id
    with factory_app.test_client() as c:
        with c.session_transaction() as sess:
            sess["_user_id"] = str(uid)
            sess["_fresh"] = True
        assert c.post("/gov/cftv/gravador/nuvem", data={"nome": "X Nuvem"}).status_code == 403


# ── Rota: canais informados à mão ─────────────────────────────────────────────


def test_canais_informados_e_volta_a_ler(authed_client, factory_app, limpa: None) -> None:
    authed_client.post("/gov/cftv/gravador/canais", data={"gravador": "Canais Nuvem", "canais": "16"})
    assert _vinculo(factory_app, "Canais Nuvem").canais == 16
    authed_client.post("/gov/cftv/gravador/canais", data={"gravador": "Canais Nuvem", "canais": ""})
    assert _vinculo(factory_app, "Canais Nuvem").canais is None
    assert authed_client.post("/gov/cftv/gravador/canais", data={"gravador": "X", "canais": "999"}).status_code == 400
