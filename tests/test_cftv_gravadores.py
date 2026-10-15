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


# ── Ajustes da 1ª leitura em produção ─────────────────────────────────────────


def test_nvr_com_get_collect_zero_usa_nomes_dos_canais(monkeypatch: pytest.MonkeyPatch) -> None:
    _cliente(
        monkeypatch,
        {
            "magicBox.cgi?action=getSerialNo": (200, "sn=NVR1\r\n"),
            "magicBox.cgi?action=getDeviceType": (200, "type=NVD 1432\r\n"),
            "devVideoInput.cgi?action=getCollect": (200, "result=0\r\n"),
            "configManager.cgi?action=getConfig&name=ChannelTitle": (
                200,
                "".join(f"table.ChannelTitle[{i}].Name=CAM {i + 1}\r\n" for i in range(32)),
            ),
        },
    )
    assert intelbras_api.ler_gravador("10.0.0.9", "u", "p").canais == 32


def test_nvr_sem_canais_nem_nomes_usa_modelo(monkeypatch: pytest.MonkeyPatch) -> None:
    _cliente(
        monkeypatch,
        {
            "magicBox.cgi?action=getSerialNo": (200, "sn=NVR1\r\n"),
            "magicBox.cgi?action=getDeviceType": (200, "type=NVD 3316\r\n"),
            "devVideoInput.cgi?action=getCollect": (200, "result=0\r\n"),
        },
    )
    assert intelbras_api.ler_gravador("10.0.0.9", "u", "p").canais == 16


def test_atualizar_ignora_corrida_entre_workers(factory_app, padrao_e_principal: None) -> None:
    from sqlalchemy.exc import IntegrityError

    from app.models.unidade import GravadorAcesso

    with factory_app.app_context():
        with (
            patch.object(cg, "ler_gravador", return_value=LeituraGravador()),
            patch.object(db.session, "commit", side_effect=IntegrityError("x", {}, Exception("dup"))),
        ):
            assert cg.atualizar([cg.Alvo("10.9.9.9", False)]) == 0
        assert db.session.get(GravadorAcesso, "10.9.9.9") is None


def test_pagina_nao_le_gravador_de_outro_fabricante(authed_client, com_zabbix: None) -> None:
    def dev(host: str, gravador: str, vendor: str) -> dict:
        base = {"host": host, "name": host, "ip": "10.0.0.7", "subcat": "nvr", "andar": "?", "gravador": gravador}
        base |= {"is_gravador": True, "canal": "", "loja": "", "vendor": vendor, "model": "", "status": "up"}
        return base | {"problems": 0}

    dados = {
        "enabled": True,
        "devices": [dev("nvr-hik", "nvr-hik", "Hikvision"), dev("dvr-ib", "dvr-ib", "Intelbras")],
    }
    with (
        patch.object(cftv_monitoring, "get_cached_cftv_summary", return_value=dados),
        patch.object(cg, "disparar") as disparar,
    ):
        authed_client.get("/gov/cftv?ver=gravadores")
    alvos = disparar.call_args.args[1]
    assert [a.ip for a in alvos] == ["10.0.0.7"]
    assert len(alvos) == 1


# ── Inventário das câmeras (getCameraAll) ─────────────────────────────────────

_CAMERAS = "\r\n".join(
    [
        # Canal 1 analógico; senha da câmera vem na resposta e não pode ir para a leitura
        "camera[0].Type=Local",
        "camera[0].UniqueChannel=0",
        "camera[0].DeviceInfo.Password=segredo-da-camera",
        # Canal 2 IP, com MAC e modelo
        "camera[1].Type=Remote",
        "camera[1].Enable=true",
        "camera[1].UniqueChannel=1",
        "camera[1].DeviceInfo.Address=10.41.100.210",
        "camera[1].DeviceInfo.Mac=80:85:44:36:AD:BF",
        "camera[1].DeviceInfo.DeviceType=VIP-1230-D-G4",
        "camera[1].DeviceInfo.Password=segredo-da-camera",
        # Canal 3: posição IP vazia (endereço de fábrica, MAC nulo)
        "camera[2].Type=Remote",
        "camera[2].Enable=true",
        "camera[2].UniqueChannel=2",
        "camera[2].DeviceInfo.Address=192.168.0.0",
        "camera[2].DeviceInfo.Mac=ff:ff:ff:ff:ff:ff",
        # Canal 4: desligada
        "camera[3].Type=Remote",
        "camera[3].Enable=false",
        "camera[3].UniqueChannel=3",
        "camera[3].DeviceInfo.Address=10.41.100.211",
        # Canal 5: IP sem MAC conhecido
        "camera[4].Type=Remote",
        "camera[4].Enable=true",
        "camera[4].UniqueChannel=4",
        "camera[4].DeviceInfo.Address=10.41.100.212",
        "camera[4].DeviceInfo.Mac=ff:ff:ff:ff:ff:ff",
        # Câmera virtual (mosaico)
        "camera[5].Type=Compose",
        "camera[5].UniqueChannel=5",
    ]
)


def test_cameras_do_gravador() -> None:
    cameras = intelbras_api.cameras_do_gravador(_CAMERAS)
    assert [(c.canal, c.tipo, c.ip, c.mac, c.modelo) for c in cameras] == [
        (1, "analogica", "", "", ""),
        (2, "ip", "10.41.100.210", "80:85:44:36:ad:bf", "VIP-1230-D-G4"),
        (5, "ip", "10.41.100.212", "", ""),
    ]


def test_cameras_do_gravador_sem_resposta() -> None:
    assert intelbras_api.cameras_do_gravador("") == []


def test_ler_gravador_traz_cameras_sem_senha(monkeypatch: pytest.MonkeyPatch) -> None:
    _cliente(
        monkeypatch,
        {
            "magicBox.cgi?action=getSerialNo": (200, "sn=ABC123\r\n"),
            "magicBox.cgi?action=getDeviceType": (200, "type=MHDX 1108\r\n"),
            "devVideoInput.cgi?action=getCollect": (200, "result=8\r\n"),
            "LogicDeviceManager.cgi?action=getCameraAll": (200, _CAMERAS),
        },
    )
    leitura = intelbras_api.ler_gravador("10.0.0.9", "u", "p")
    assert [c.canal for c in leitura.cameras] == [1, 2, 5]
    assert "segredo" not in leitura.model_dump_json()


def test_leitura_antiga_sem_cameras_ainda_valida() -> None:
    antiga = '{"modelo": "MHDX 1108", "serie": "X", "canais": 8, "sem_video": [], "titulos": {}}'
    assert LeituraGravador.model_validate_json(antiga).cameras == []


def _card_inventario() -> dict:
    return {
        "dispositivos": [
            {"canal": "2", "name": "Câmera Portão", "ip": "10.41.100.210", "model": "", "status": "up"},
            {"canal": "7", "name": "Câmera Doca", "ip": "10.41.100.230", "model": "VIP 3230", "status": "down"},
        ]
    }


def test_inventario_junta_gravador_e_zabbix() -> None:
    leitura = LeituraGravador(
        canais=8,
        sem_video=[3, 4, 5, 6, 8],
        titulos={1: "Recepção", 3: "CAM 3", 4: "Estoque"},
        cameras=intelbras_api.cameras_do_gravador(_CAMERAS),
    )
    linhas = cg.inventario_do_card(_card_inventario(), {"leitura": leitura})
    por_canal = {x["canal"]: x for x in linhas}
    # 1: analógica com vídeo; 2: IP + Zabbix; 4: nome dado e sem vídeo; 5: câmera IP; 7: só no Zabbix
    assert sorted(por_canal) == [1, 2, 4, 5, 7]
    assert por_canal[1] | {} == {
        "canal": 1,
        "nome": "Recepção",
        "tipo": "analogica",
        "modelo": "",
        "ip": "",
        "mac": "",
        "sem_video": False,
        "video": "ok",
        "status": "",
        "situacao": "",
        "sem_imagem": False,
        "gravacao": "",
        "ultima_gravacao": None,
        "imagem": "",
        "verificado_em": None,
        "miniatura": False,
    }
    assert (por_canal[2]["nome"], por_canal[2]["mac"], por_canal[2]["modelo"]) == (
        "Câmera Portão",
        "80:85:44:36:ad:bf",
        "VIP-1230-D-G4",
    )
    assert por_canal[4]["sem_video"] is True
    assert (por_canal[7]["modelo"], por_canal[7]["ip"], por_canal[7]["status"]) == ("VIP 3230", "10.41.100.230", "down")


def test_inventario_sem_leitura_usa_so_o_zabbix() -> None:
    linhas = cg.inventario_do_card(_card_inventario(), None)
    assert [(x["canal"], x["ip"], x["tipo"]) for x in linhas] == [(2, "10.41.100.210", ""), (7, "10.41.100.230", "")]


# ── Canais pelo SNMP do Zabbix e pela conferência no local ────────────────────


def _card_snmp(confiavel: bool = True) -> dict:
    """DVR de 6 canais: 1 IP no Zabbix, 2–3 analógicas, 4 vazio, 5 IP sumida, 6 outro equipamento."""
    return {
        "dispositivos": [{"canal": "1", "name": "Câmera Portão", "ip": "10.41.100.210", "model": "", "status": "up"}],
        "gravador_host": {
            "model": "MHDX 3106",
            "status_canal_confiavel": confiavel,
            "canais_snmp": {
                "1": {"status": "Connected", "nome": "VIP Intelbras", "ip": "10.41.100.210"},
                "2": {"status": "Connected", "nome": "BARREIRA", "ip": "192.168.0.0"},
                "3": {"status": "Empty", "nome": "CAM 3", "ip": "192.168.0.0"},
                "4": {"status": "Unconnect", "nome": "CAM 4", "ip": "192.168.0.0"},
                "5": {"status": "Empty", "nome": "Doca", "ip": "10.41.100.230"},
                "6": {"status": "Unconnect", "nome": "CAM 6", "ip": "192.168.0.0"},
            },
        },
    }


_CADASTRO = {
    2: {"situacao": "ativo", "analogica": True, "ip_camera": "", "equipamento": "", "imagem": "ok"},
    3: {"situacao": "ativo", "analogica": True, "ip_camera": "", "equipamento": "", "imagem": "sem"},
    4: {"situacao": "vazio", "analogica": False, "ip_camera": "", "equipamento": "", "imagem": ""},
    6: {"situacao": "outro", "analogica": False, "ip_camera": "", "equipamento": "Controlador de acesso", "imagem": ""},
}


def test_canais_snmp_agrupa_itens_por_host_e_canal() -> None:
    itens = [
        {"hostid": "9", "key_": "remoteCam[status1]", "lastvalue": "Connected"},
        {"hostid": "9", "key_": "remoteCam[name1]", "lastvalue": " Portão "},
        {"hostid": "9", "key_": "remoteCam[IpAddr12]", "lastvalue": "10.0.0.5"},
        {"hostid": "9", "key_": "remoteCam[outra1]", "lastvalue": "x"},
    ]
    assert cftv_monitoring.canais_snmp(itens) == {
        "9": {"1": {"status": "Connected", "nome": "Portão"}, "12": {"ip": "10.0.0.5"}}
    }


def test_inventario_mostra_analogicas_pela_conferencia_e_snmp() -> None:
    por_canal = {x["canal"]: x for x in cg.inventario_do_card(_card_snmp(), None, _CADASTRO)}
    # 4 é vazio na conferência; 5 entra pelo IP de câmera no SNMP
    assert sorted(por_canal) == [1, 2, 3, 5, 6]
    assert (por_canal[2]["tipo"], por_canal[2]["nome"], por_canal[2]["video"]) == ("analogica", "BARREIRA", "ok")
    # "Empty" em canal analógico: o gravador não diz com certeza
    assert (por_canal[3]["tipo"], por_canal[3]["video"], por_canal[3]["sem_imagem"]) == ("analogica", "", True)
    # "Empty" com IP de câmera em gravador confiável = câmera sumiu
    assert (por_canal[5]["tipo"], por_canal[5]["ip"], por_canal[5]["sem_video"]) == ("ip", "10.41.100.230", True)
    assert (por_canal[6]["tipo"], por_canal[6]["nome"], por_canal[6]["sem_video"]) == (
        "outro",
        "Controlador de acesso",
        True,
    )
    assert por_canal[1]["status"] == "up" and por_canal[1]["situacao"] == ""


def test_inventario_gravador_com_status_nao_confiavel() -> None:
    por_canal = {x["canal"]: x for x in cg.inventario_do_card(_card_snmp(confiavel=False), None, _CADASTRO)}
    assert (por_canal[5]["sem_video"], por_canal[5]["video"]) == (False, "")


def test_canal_unconnect_em_uso_fica_sem_video() -> None:
    cadastro = {4: {**_CADASTRO[4], "situacao": "ativo", "analogica": True}}
    por_canal = {x["canal"]: x for x in cg.inventario_do_card(_card_snmp(), None, cadastro)}
    assert (por_canal[4]["tipo"], por_canal[4]["video"]) == ("analogica", "sem")


def test_canais_do_card_pelo_snmp() -> None:
    k = cg.canais_do_card(_card_snmp(), None, None, _CADASTRO)
    assert (k["total"], k["fonte"], k["usados"], k["livres"], k["analogicas"]) == (6, "snmp", 5, 1, 2)
    assert [x["canal"] for x in k["sem_video"]] == [5, 6]


def test_importar_canais_cftv(factory_app, tmp_path) -> None:
    from app.models.unidade import CanalCftv

    arquivo = tmp_path / "marcas.json"
    marcas = [
        {
            "ip_gravador": "10.1.1.1",
            "canal": 1,
            "marca": "ativo",
            "analogica": True,
            "imagem": "sem",
            "em": "2026-10-14T16:58:41.129Z",
        },
        {"ip_gravador": "10.1.1.1", "canal": 2, "marca": "vazio", "analogica": True, "ip_real": "10.1.1.50"},
        {"ip_gravador": "10.1.1.1", "canal": 3, "marca": "outro", "equipamento": "Controlador"},
        {"ip_gravador": "10.1.1.1", "canal": "4", "marca": "ativo"},
        {"ip_gravador": "10.1.1.1", "canal": 5, "marca": ""},
    ]
    arquivo.write_text(json.dumps(marcas), encoding="utf-8")
    runner = factory_app.test_cli_runner()
    try:
        r = runner.invoke(args=["cftv-importar-canais", str(arquivo), "--origem", "teste"])
        assert "3 novos, 0 atualizados, 2 ignorados" in r.output
        marcas[0]["imagem"] = "ok"
        arquivo.write_text(json.dumps(marcas), encoding="utf-8")
        r = runner.invoke(args=["cftv-importar-canais", str(arquivo)])
        assert "0 novos, 3 atualizados" in r.output
        with factory_app.app_context():
            canais = {c.canal: c for c in CanalCftv.query.filter_by(ip_gravador="10.1.1.1")}
            assert (canais[1].analogica, canais[1].imagem, canais[1].conferido_em is not None) == (True, "ok", True)
            # vazio não guarda analógica nem IP de câmera
            assert (canais[2].analogica, canais[2].ip_camera) == (False, "")
            assert canais[3].equipamento == "Controlador"
            assert cg.cadastro_canais()["10.1.1.1"][1]["situacao"] == "ativo"
    finally:
        with factory_app.app_context():
            CanalCftv.query.delete()
            db.session.commit()


# ── Rotas: inventário em CSV e link do MapaCameras ────────────────────────────

_IP_INV = "10.9.8.7"


@pytest.fixture
def gravador_inventario(factory_app, monkeypatch: pytest.MonkeyPatch) -> Iterator[int]:
    """Gravador ``INV Teste`` na unidade Shopping, já lido, com câmeras."""
    from app.models.unidade import GravadorAcesso

    monkeypatch.setenv("ZABBIX_URL", "https://zabbix.test")
    monkeypatch.setattr(cg, "disparar", lambda app, gravadores: None)
    leitura = LeituraGravador(
        modelo="NVD 1432",
        canais=4,
        titulos={1: "=HYPERLINK(1)"},
        cameras=intelbras_api.cameras_do_gravador(_CAMERAS),
    )
    with factory_app.app_context():
        shopping = Unidade.query.filter_by(nome="Shopping", parent_id=None).one()
        nome_mapa_antes = shopping.nome_mapa
        db.session.add(DvrUnidade(dvr="INV Teste", unidade_id=shopping.id))
        db.session.add(
            GravadorAcesso(ip=_IP_INV, credencial="padrao", leitura=leitura.model_dump_json(), lido_em=_AGORA)
        )
        db.session.commit()
        shopping_id = shopping.id
    dados = {
        "enabled": True,
        "devices": [
            {
                "host": "nvr-inv",
                "name": "INV Teste",
                "ip": _IP_INV,
                "subcat": "nvr",
                "andar": "?",
                "gravador": "INV Teste",
                "is_gravador": True,
                "canal": "",
                "loja": "",
                "vendor": "Intelbras",
                "model": "",
                "status": "up",
                "problems": 0,
            }
        ],
    }
    with patch.object(cftv_monitoring, "get_cached_cftv_summary", return_value=dados):
        yield shopping_id
    with factory_app.app_context():
        DvrUnidade.query.filter_by(dvr="INV Teste").delete()
        db.session.query(GravadorAcesso).filter_by(ip=_IP_INV).delete()
        db.session.get(Unidade, shopping_id).nome_mapa = nome_mapa_antes
        db.session.commit()


def test_csv_do_inventario(authed_client, gravador_inventario: int) -> None:
    resp = authed_client.get(f"/gov/cftv/inventario.csv?unidade={gravador_inventario}")
    assert resp.status_code == 200
    assert resp.mimetype == "text/csv"
    assert "attachment" in resp.headers["Content-Disposition"]
    texto = resp.get_data(as_text=True)
    assert texto.startswith("﻿Unidade;Gravador;Canal;Nome;Tipo;Modelo;IP;MAC;Vídeo;Imagem;Gravação;Status")
    assert "Shopping;INV Teste;2;;IP;VIP-1230-D-G4;10.41.100.210;80:85:44:36:ad:bf;ok;;;" in texto
    # Nome de canal vindo do aparelho não vira fórmula no Excel
    assert ";'=HYPERLINK(1);" in texto
    assert "segredo" not in texto


def test_csv_do_inventario_exige_login(factory_app, gravador_inventario: int) -> None:
    resp = factory_app.test_client().get("/gov/cftv/inventario.csv")
    assert resp.status_code in (302, 401)


def test_pagina_mostra_inventario_e_link_do_mapa(authed_client, factory_app, gravador_inventario: int) -> None:
    with factory_app.app_context():
        db.session.get(Unidade, gravador_inventario).nome_mapa = "Shopping Mueller"
        db.session.commit()
    html = authed_client.get(f"/gov/cftv?unidade={gravador_inventario}").get_data(as_text=True)
    assert "Canais: 5 câmeras (2 IP" in html
    assert "80:85:44:36:ad:bf" in html
    assert "/gov/cameras?unidade=Shopping+Mueller" in html
    assert "/gov/cftv/inventario.csv?unidade=" in html


def test_link_do_mapa_usa_nome_da_unidade_por_padrao(authed_client, gravador_inventario: int) -> None:
    html = authed_client.get(f"/gov/cftv?unidade={gravador_inventario}").get_data(as_text=True)
    assert "/gov/cameras?unidade=Shopping" in html


def test_garantir_colunas_cria_nome_mapa(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'antigo.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE unidades (id INTEGER PRIMARY KEY, nome VARCHAR(120) NOT NULL)"))
        conn.execute(text("INSERT INTO unidades (nome) VALUES ('Sede')"))
    garantir_colunas(engine)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT nome_mapa FROM unidades")).one() == ("",)


# ── Totais por tipo de dispositivo ────────────────────────────────────────────


def test_totais_por_tipo() -> None:
    def d(subcat: str, status: str = "up") -> dict:
        return {"subcat": subcat, "status": status}

    devs = [d("tela"), d("camera"), d("camera", "down"), d("nvr"), d("dvr", "nodata"), d("facial"), d("sirene"), d("")]
    totais = cftv_monitoring.totais_por_tipo(devs)
    assert [(t["tipo"], t["total"], t["up"], t["down"], t["nodata"]) for t in totais] == [
        ("Câmeras", 2, 1, 1, 0),
        ("Faciais", 1, 1, 0, 0),
        ("Gravadores (DVR/NVR)", 2, 1, 0, 1),
        ("Telas", 1, 1, 0, 0),
        ("Outros", 1, 1, 0, 0),
        ("Sirene", 1, 1, 0, 0),
    ]
    assert sum(t["total"] for t in totais) == len(devs)


def test_pagina_mostra_totais_por_tipo(authed_client, gravador_inventario: int) -> None:
    html = authed_client.get("/gov/cftv").get_data(as_text=True)
    assert 'aria-label="Dispositivos por tipo"' in html
    assert "Gravadores (DVR/NVR)" in html


def test_buscar_cftv_traz_canais_snmp_dos_gravadores() -> None:
    hosts = [
        {
            "hostid": "1",
            "host": "DVR-X",
            "name": "DVR X",
            "maintenance_status": "0",
            "interfaces": [],
            "tags": [{"tag": "subcategory", "value": "dvr"}],
            "macros": [{"macro": "{$CAM.STATUS.CONFIAVEL}", "value": "0"}],
        },
        {
            "hostid": "2",
            "host": "cam-x-ch01",
            "name": "Câmera X",
            "maintenance_status": "0",
            "interfaces": [],
            "tags": [{"tag": "subcategory", "value": "camera"}, {"tag": "dvr", "value": "DVR-X"}],
            "macros": [],
        },
    ]
    chamadas: list[dict] = []

    def fake_zbx(method: str, params: dict) -> list:
        if method == "hostgroup.get":
            return [{"groupid": "35", "name": "CFTV/Cameras"}]
        if method == "host.get":
            return hosts
        if method == "item.get" and params.get("search"):
            chamadas.append(params)
            return [{"hostid": "1", "key_": "remoteCam[status2]", "lastvalue": "Connected"}]
        return []

    with patch.object(cftv_monitoring, "_zbx", side_effect=fake_zbx):
        devices = {d["host"]: d for d in cftv_monitoring._buscar_cftv()["devices"]}
    # Só os gravadores entram na busca dos itens de canal
    assert chamadas[0]["hostids"] == ["1"]
    assert devices["DVR-X"]["canais_snmp"] == {"2": {"status": "Connected"}}
    assert devices["DVR-X"]["status_canal_confiavel"] is False
    assert devices["cam-x-ch01"]["canais_snmp"] == {} and devices["cam-x-ch01"]["status_canal_confiavel"] is True
