"""CFTV: verificação automática de gravação e imagem dos canais (gravador Intelbras)."""

from __future__ import annotations

import io
import json
import random
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from PIL import Image

from app.extensions import db
from app.models.unidade import CanalCftv, GravadorAcesso, VerificacaoCanal
from app.services import cftv_gravadores as cg
from app.services import cftv_verificacao as cv
from itgov.api.v1 import cftv_monitoring
from itgov.services import cftv_imagem, intelbras_api
from itgov.services.intelbras_api import AcessoNegadoError, CameraGravador, LeituraGravador, SessaoGravador

_AGORA = datetime(2026, 10, 15, 14, 10, tzinfo=UTC)  # 11:10 em Brasília
_IP = "10.77.0.9"


def _jpeg(cena: bool) -> bytes:
    """Foto de teste: cena com ruído ou tela preta chapada (como a do DVR sem sinal)."""
    if cena:
        rnd = random.Random(7)
        img = Image.new("L", (176, 120))
        img.putdata([rnd.randrange(256) for _ in range(176 * 120)])
    else:
        img = Image.new("L", (704, 480), 19)
    saida = io.BytesIO()
    img.convert("RGB").save(saida, format="JPEG")
    return saida.getvalue()


# ── cftv_imagem ───────────────────────────────────────────────────────────────


def test_analise_separa_tela_chapada_de_cena() -> None:
    preta = cftv_imagem.analisar(_jpeg(cena=False))
    cena = cftv_imagem.analisar(_jpeg(cena=True))
    assert preta.chapado >= 0.99 and cftv_imagem.sem_imagem(preta)
    assert cena.chapado < 0.3 and not cftv_imagem.sem_imagem(cena)
    assert preta.brilho == pytest.approx(19, abs=2)


def test_analise_recusa_o_que_nao_e_imagem() -> None:
    with pytest.raises(ValueError, match="não é uma imagem"):
        cftv_imagem.analisar(b"Error\r\nBad Request!")


def test_miniatura_reduz() -> None:
    mini = Image.open(io.BytesIO(cftv_imagem.miniatura(_jpeg(cena=False))))
    assert mini.size == (320, 218) and mini.format == "JPEG"


# ── SessaoGravador (API Intelbras) ────────────────────────────────────────────


def _transporte(rotas: dict[str, Any], chamadas: list[str]) -> httpx.MockTransport:
    def responder(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        chamadas.append(url)
        if "getSerialNo" in url:
            return httpx.Response(200, text="sn=ABC123\r\n")
        for trecho, resp in rotas.items():
            if trecho in url:
                if isinstance(resp, Exception):
                    raise resp
                return resp
        return httpx.Response(404)

    return httpx.MockTransport(responder)


@pytest.fixture
def gravador_falso(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[dict[str, Any], list[str]]]:
    rotas: dict[str, Any] = {}
    chamadas: list[str] = []
    original = httpx.Client

    def cliente(**kw: Any) -> httpx.Client:
        return original(transport=_transporte(rotas, chamadas), **kw)

    monkeypatch.setattr(intelbras_api.httpx, "Client", cliente)
    yield rotas, chamadas


def test_sessao_le_hora_modos_gravacao_e_foto(gravador_falso: tuple[dict[str, Any], list[str]]) -> None:
    rotas, chamadas = gravador_falso
    rotas["getCurrentTime"] = httpx.Response(200, text="result=2026-10-15 11:10:00\r\n")
    rotas["name=RecordMode"] = httpx.Response(200, text="table.RecordMode[0].Mode=0\r\ntable.RecordMode[1].Mode=2\r\n")
    rotas["factory.create"] = httpx.Response(200, text="result=777\r\n")
    rotas["condition.Channel=1&"] = httpx.Response(200, text="OK\r\n")
    rotas["condition.Channel=2&"] = httpx.Response(400, text="Error\r\n")
    rotas["findNextFile"] = httpx.Response(
        200,
        text="found=2\r\nitems[0].EndTime=2026-10-15 10:40:00\r\nitems[1].EndTime=2026-10-15 11:09:58\r\n",
    )
    rotas["snapshot.cgi?channel=1"] = httpx.Response(
        200, content=b"\xff\xd8jpeg", headers={"content-type": "image/jpeg"}
    )
    rotas["snapshot.cgi?channel=2"] = httpx.TimeoutException("lento")
    rotas["snapshot.cgi?channel=3"] = httpx.Response(400, text="Error")
    with SessaoGravador(_IP, "u", "p") as g:
        hora = g.hora()
        assert hora == datetime(2026, 10, 15, 11, 10)
        assert g.modos_gravacao() == {1: 0, 2: 2}
        assert g.ultima_gravacao(1, hora - timedelta(hours=2), hora) == datetime(2026, 10, 15, 11, 9, 58)
        # 400 = nenhum arquivo no período
        assert g.ultima_gravacao(2, hora - timedelta(hours=2), hora) is None
        assert g.foto(1) == b"\xff\xd8jpeg"
        assert g.foto(2) is None and g.foto(3) is None
    busca = next(u for u in chamadas if "condition.Channel=1&" in u)
    # O aparelho só aceita a data com %20 (com "+" responde 400)
    assert "StartTime=2026-10-15%2009:10:00" in busca and "+" not in busca
    assert any("action=destroy&object=777" in u for u in chamadas)


def test_sessao_credencial_recusada(gravador_falso: tuple[dict[str, Any], list[str]], monkeypatch) -> None:
    def recusa(c: httpx.Client, ip: str, usuario: str, senha: str) -> None:
        raise AcessoNegadoError(ip)

    monkeypatch.setattr(intelbras_api, "_autenticar", recusa)
    with pytest.raises(AcessoNegadoError), SessaoGravador(_IP, "u", "errada"):
        pass


def test_sessao_fora_do_with() -> None:
    with pytest.raises(RuntimeError, match="with"):
        SessaoGravador(_IP, "u", "p").hora()


# ── Verificação (gravação e imagem) ───────────────────────────────────────────


class _GravadorFalso:
    """Substitui SessaoGravador: canal → fim da última gravação e foto."""

    def __init__(self, gravacoes: dict[int, datetime | None], fotos: dict[int, list[bytes | None]], modos=None):
        self.gravacoes, self.fotos, self.modos = gravacoes, fotos, modos or {}

    def __call__(self, ip: str, usuario: str, senha: str) -> _GravadorFalso:
        return self

    def __enter__(self) -> _GravadorFalso:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def hora(self) -> datetime:
        return datetime(2026, 10, 15, 11, 10)

    def modos_gravacao(self) -> dict[int, int]:
        return self.modos

    def ultima_gravacao(self, canal: int, inicio: datetime, fim: datetime) -> datetime | None:
        return self.gravacoes.get(canal)

    def foto(self, canal: int) -> bytes | None:
        return self.fotos[canal].pop(0)


@pytest.fixture
def banco(factory_app) -> Iterator[Any]:
    with factory_app.app_context():
        yield factory_app
        VerificacaoCanal.query.delete()
        CanalCftv.query.delete()
        GravadorAcesso.query.filter(GravadorAcesso.ip.like("10.77.0.%")).delete(synchronize_session=False)
        db.session.commit()


def _alvo(*canais: int) -> list[cv.Alvo]:
    return [cv.Alvo(_IP, "u", "p", list(canais))]


def test_verificar_gravacao(banco, monkeypatch: pytest.MonkeyPatch) -> None:
    falso = _GravadorFalso(
        {1: datetime(2026, 10, 15, 11, 9), 2: datetime(2026, 10, 15, 10, 30), 3: None, 4: None}, {}, modos={4: 2}
    )
    monkeypatch.setattr(cv, "SessaoGravador", falso)
    assert cv.verificar_gravacao(_alvo(1, 2, 3, 4), _AGORA) == {_IP}
    estado = cv.por_gravador()[_IP]
    assert {c: v["gravacao"] for c, v in estado.items()} == {1: "ok", 2: "parada", 3: "parada", 4: "desligada"}
    assert estado[2]["ultima_gravacao"] == datetime(2026, 10, 15, 10, 30)


def test_verificar_imagens(banco, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cv, "ESPERA_NOVA_FOTO", 0)
    monkeypatch.setattr(cv, "PAUSA_ENTRE_FOTOS", 0)
    falso = _GravadorFalso(
        {},
        {
            1: [_jpeg(cena=True)],
            2: [_jpeg(cena=False)],
            3: [None, None],  # não entrega nem na segunda tentativa
            4: [None, _jpeg(cena=True)],  # a segunda tentativa vem
            5: [b"Error"],
        },
    )
    monkeypatch.setattr(cv, "SessaoGravador", falso)
    cv.verificar_imagens(_alvo(1, 2, 3, 4, 5), _AGORA)
    estado = cv.por_gravador()[_IP]
    assert {c: v["imagem"] for c, v in estado.items()} == {
        1: "ok",
        2: "chapada",
        3: "sem_resposta",
        4: "ok",
        5: "sem_resposta",
    }
    # Miniatura só de quem tem foto com problema
    assert [c for c, v in estado.items() if v["miniatura"]] == [2]
    # Voltou ao normal: a miniatura é apagada
    falso.fotos[2] = [_jpeg(cena=True)]
    cv.verificar_imagens(_alvo(2), _AGORA)
    depois = cv.por_gravador()[_IP][2]
    assert (depois["imagem"], depois["miniatura"]) == ("ok", False)


def test_gravador_fora_do_ar_nao_entra_como_verificado(banco, monkeypatch: pytest.MonkeyPatch) -> None:
    class Fora(_GravadorFalso):
        def __enter__(self) -> _GravadorFalso:
            raise httpx.ConnectTimeout("fora")

    monkeypatch.setattr(cv, "SessaoGravador", Fora({}, {}))
    assert cv.verificar_gravacao(_alvo(1), _AGORA) == set()
    assert cv.por_gravador() == {}


def test_credencial_recusada_e_anotada(banco, monkeypatch: pytest.MonkeyPatch) -> None:
    class Recusa(_GravadorFalso):
        def __enter__(self) -> _GravadorFalso:
            raise AcessoNegadoError(_IP)

    db.session.add(GravadorAcesso(ip=_IP, credencial="padrao", leitura="{}", falhas="{}"))
    db.session.commit()
    monkeypatch.setattr(cv, "SessaoGravador", Recusa({}, {}))
    assert cv.verificar_imagens(_alvo(1), _AGORA) == set()
    acesso = db.session.get(GravadorAcesso, _IP)
    assert (acesso.credencial, acesso.erro) == ("", "credencial recusada")
    assert "padrao" in json.loads(acesso.falhas)


def test_alvos_so_com_senha_aceita_e_canais_ativos(banco) -> None:
    leitura = LeituraGravador(canais=4).model_dump_json()
    db.session.add_all(
        [
            GravadorAcesso(ip=_IP, credencial="padrao", leitura=leitura, falhas="{}"),
            GravadorAcesso(ip="10.77.0.8", credencial="", leitura="", falhas="{}", erro="credencial recusada"),
            CanalCftv(ip_gravador=_IP, canal=2, situacao="ativo"),
            CanalCftv(ip_gravador=_IP, canal=1, situacao="ativo", analogica=True),
            CanalCftv(ip_gravador=_IP, canal=3, situacao="vazio"),
            CanalCftv(ip_gravador="10.77.0.8", canal=1, situacao="ativo"),
        ]
    )
    db.session.commit()
    with (
        patch.dict("os.environ", {"CFTV_CAM_PADRAO_USER": "u", "CFTV_CAM_PADRAO_PASS": "p"}),
        patch.object(cftv_monitoring, "get_cached_cftv_summary", return_value={"devices": []}),
    ):
        assert cv.alvos() == [cv.Alvo(_IP, "u", "p", [1, 2])]


def test_textos_e_envio_para_o_zabbix(banco, monkeypatch: pytest.MonkeyPatch) -> None:
    db.session.add_all(
        [
            VerificacaoCanal(ip_gravador=_IP, canal=3, gravacao="parada", imagem="chapada"),
            VerificacaoCanal(ip_gravador=_IP, canal=12, gravacao="desligada", imagem="ok"),
            VerificacaoCanal(ip_gravador=_IP, canal=5, gravacao="ok", imagem="sem_resposta"),
            VerificacaoCanal(ip_gravador="10.77.0.7", canal=1, gravacao="ok", imagem="ok"),
        ]
    )
    db.session.commit()
    textos = cv.textos_para_zabbix({_IP, "10.77.0.7"})
    assert textos[_IP] == {cv.CHAVE_SEM_GRAVAR: "canais 3 e 12", cv.CHAVE_SEM_IMAGEM: "canais 3 e 5"}
    assert textos["10.77.0.7"] == {cv.CHAVE_SEM_GRAVAR: "", cv.CHAVE_SEM_IMAGEM: ""}

    chamadas: list[tuple[str, Any]] = []

    def fake_zbx(method: str, params: Any) -> Any:
        chamadas.append((method, params))
        if method == "item.get":
            # Item trapper: o Zabbix não devolve interface no item
            return [{"itemid": "91", "hostid": "1"}, {"itemid": "92", "hostid": "2"}]
        if method == "host.get":
            return [
                # Gravador com duas interfaces (ICMP e SNMP) no mesmo IP: um valor só
                {"hostid": "1", "interfaces": [{"ip": _IP}, {"ip": _IP}]},
                {"hostid": "2", "interfaces": [{"ip": "10.77.0.6"}]},  # gravador não verificado
            ]
        return {}

    monkeypatch.setenv("ZABBIX_URL", "https://zabbix.test")
    monkeypatch.setattr(cftv_monitoring, "_zbx", fake_zbx)
    assert cv.enviar_zabbix({_IP}, cv.CHAVE_SEM_IMAGEM) == 1
    assert chamadas[-1] == ("history.push", [{"itemid": "91", "value": "canais 3 e 5"}])


def test_envio_sem_zabbix_configurado(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ZABBIX_URL", raising=False)
    assert cv.enviar_zabbix({_IP}, cv.CHAVE_SEM_GRAVAR) == 0


@pytest.mark.parametrize(
    ("agora_brt", "ultima_brt", "esperado"),
    [
        ((11, 10), None, True),
        ((11, 10), (11, 2), False),  # já tirou neste horário
        ((11, 10), (8, 5), True),
        ((11, 40), None, False),  # passou da meia hora
        ((13, 10), None, False),  # fora dos horários
        ((20, 5), None, False),  # noite
    ],
)
def test_horario_de_foto(agora_brt, ultima_brt, esperado) -> None:
    def brt(h: int, m: int) -> datetime:
        return datetime(2026, 10, 15, h + 3, m, tzinfo=UTC)

    assert cv.horario_de_foto(brt(*agora_brt), brt(*ultima_brt) if ultima_brt else None) is esperado


def test_rodada_respeita_intervalos(banco, monkeypatch: pytest.MonkeyPatch) -> None:
    feitos: list[str] = []
    monkeypatch.setattr(cv, "_tentativa", {"gravacao": None, "imagem": None, "conferencia": None})
    monkeypatch.setattr(cv, "alvos", lambda: _alvo(1))
    monkeypatch.setattr(cv, "verificar_gravacao", lambda lista, agora: feitos.append("gravacao") or {_IP})
    monkeypatch.setattr(cv, "verificar_imagens", lambda lista, agora: feitos.append("imagem") or {_IP})
    monkeypatch.setattr(cv, "verificar_conferencia", lambda agora: feitos.append("conferencia") or {_IP: ""})
    monkeypatch.setattr(cv, "enviar_zabbix", lambda ips, chave, textos=None: 1)
    # 11:10 em Brasília: grava, tira foto e confere
    assert cv.rodada(_AGORA) == {"gravacao": 1, "imagem": 1, "conferencia": 1}
    # 10 min depois: nada vencido (mesmo sem nada gravado no banco — gravador fora do ar)
    assert cv.rodada(_AGORA + timedelta(minutes=10)) == {"gravacao": 0, "imagem": 0, "conferencia": 0}
    # 30 min depois: só a gravação
    assert cv.rodada(_AGORA + timedelta(minutes=30)) == {"gravacao": 1, "imagem": 0, "conferencia": 0}
    # 60 min depois: gravação e conferência
    assert cv.rodada(_AGORA + timedelta(minutes=60)) == {"gravacao": 1, "imagem": 0, "conferencia": 1}
    assert feitos == ["gravacao", "imagem", "conferencia", "gravacao", "gravacao", "conferencia"]


def _conf(situacao: str = "ativo", ip: str = "", analogica: bool = False) -> dict[str, Any]:
    return {"situacao": situacao, "analogica": analogica, "ip_camera": ip, "equipamento": "", "imagem": ""}


def test_divergencias_conferencia() -> None:
    leitura = LeituraGravador(
        cameras=[
            CameraGravador(canal=1, tipo="analogica"),
            CameraGravador(canal=2, tipo="analogica"),
            CameraGravador(canal=17, tipo="ip", ip="10.41.0.49"),
            CameraGravador(canal=18, tipo="ip", ip="10.41.0.84"),
            CameraGravador(canal=19, tipo="ip", ip="10.41.0.9"),
            CameraGravador(canal=20, tipo="ip", ip="10.41.0.10"),
            CameraGravador(canal=21, tipo="ip", ip="10.41.0.11"),
            CameraGravador(canal=22, tipo="ip", ip="10.41.0.12"),
        ]
    )
    cadastro = {
        1: _conf(analogica=True),  # bate
        2: _conf("vazio"),  # analógica sem câmera aparece como "Local": não diverge
        3: _conf(analogica=True),  # gravador sem nada no canal
        17: _conf(ip="10.41.0.49"),  # bate
        18: _conf(ip="10.41.100.77"),  # IP diferente
        19: _conf("vazio"),  # câmera onde a conferência diz vazio
        20: _conf(analogica=True),  # IP onde a conferência diz analógica
        21: _conf("outro"),  # outro equipamento: não entra
        23: _conf(ip="10.41.0.13"),  # câmera que o gravador não tem
        # 22: não conferido
    }
    assert cv.divergencias_conferencia(cadastro, leitura) == [
        "canal 3: conferência analógica, gravador sem câmera",
        "canal 18: conferência 10.41.100.77, gravador 10.41.0.84",
        "canal 19: conferência vazio, gravador 10.41.0.9",
        "canal 20: conferência analógica, gravador 10.41.0.10",
        "canal 22: sem conferência, gravador 10.41.0.12",
        "canal 23: conferência 10.41.0.13, gravador sem câmera",
    ]
    assert cv.divergencias_conferencia({17: _conf(ip="10.41.0.49")}, LeituraGravador()) == [
        "canal 17: conferência 10.41.0.49, gravador sem câmera"
    ]


def test_verificar_conferencia_rele_e_compara(banco, monkeypatch: pytest.MonkeyPatch) -> None:
    db.session.add_all(
        [
            GravadorAcesso(ip=_IP, credencial="padrao", leitura="", falhas="{}"),
            # Senha recusada: não é relido
            GravadorAcesso(ip="10.77.0.8", credencial="", leitura="", falhas="{}", erro="credencial recusada"),
            CanalCftv(ip_gravador=_IP, canal=17, situacao="ativo", ip_camera="10.41.0.49"),
            CanalCftv(ip_gravador=_IP, canal=18, situacao="ativo", ip_camera="10.41.0.84"),
            CanalCftv(ip_gravador="10.77.0.8", canal=1, situacao="ativo", analogica=True),
        ]
    )
    db.session.commit()
    lidos: list[str] = []

    def fake_ler_um(ip: str, centro: bool, acesso: Any, agora: datetime, nome: str = "") -> None:
        lidos.append(ip)
        cams = [
            CameraGravador(canal=17, tipo="ip", ip="10.41.0.49"),
            CameraGravador(canal=18, tipo="ip", ip="10.41.0.5"),
        ]
        acesso.leitura, acesso.lido_em = LeituraGravador(cameras=cams).model_dump_json(), agora

    monkeypatch.setattr(cg, "ler_um", fake_ler_um)
    monkeypatch.setattr(cv, "_nomes_gravadores", dict)
    assert cv.verificar_conferencia(_AGORA) == {_IP: "canal 18: conferência 10.41.0.84, gravador 10.41.0.5"}
    assert lidos == [_IP]
    assert db.session.get(GravadorAcesso, _IP).leitura  # a leitura nova fica guardada


def test_envio_com_textos_prontos(monkeypatch: pytest.MonkeyPatch) -> None:
    chamadas: list[tuple[str, Any]] = []

    def fake_zbx(method: str, params: Any) -> Any:
        chamadas.append((method, params))
        if method == "item.get":
            return [{"itemid": "93", "hostid": "1"}]
        if method == "host.get":
            return [{"hostid": "1", "interfaces": [{"ip": _IP}]}]
        return {}

    monkeypatch.setenv("ZABBIX_URL", "https://zabbix.test")
    monkeypatch.setattr(cftv_monitoring, "_zbx", fake_zbx)
    assert cv.enviar_zabbix({_IP}, cv.CHAVE_CONFERENCIA, {_IP: "canal 18: x"}) == 1
    assert chamadas[0] == ("item.get", {"output": ["itemid", "hostid"], "filter": {"key_": [cv.CHAVE_CONFERENCIA]}})
    assert chamadas[-1] == ("history.push", [{"itemid": "93", "value": "canal 18: x"}])


def test_agendador_nao_liga_em_teste(factory_app) -> None:
    assert cv.iniciar_agendador(factory_app) is False


# ── Página ────────────────────────────────────────────────────────────────────


def _card_verificado() -> dict:
    return {
        "dispositivos": [],
        "gravador_host": {"model": "MHDX 3104", "ip": _IP, "canais_snmp": {}},
    }


def test_inventario_traz_a_verificacao() -> None:
    cadastro = {
        1: {"situacao": "ativo", "analogica": True, "ip_camera": "", "equipamento": "", "imagem": "sem"},
        2: {"situacao": "ativo", "analogica": True, "ip_camera": "", "equipamento": "", "imagem": ""},
    }
    verificacao = {
        1: {
            "gravacao": "ok",
            "ultima_gravacao": datetime(2026, 10, 15, 11, 9),
            "gravacao_em": _AGORA,
            "imagem": "chapada",
            "imagem_em": _AGORA - timedelta(hours=1),
            "miniatura": True,
        }
    }
    por_canal = {x["canal"]: x for x in cg.inventario_do_card(_card_verificado(), None, cadastro, verificacao)}
    assert (por_canal[1]["gravacao"], por_canal[1]["imagem"], por_canal[1]["miniatura"]) == ("ok", "chapada", True)
    assert por_canal[1]["verificado_em"] == _AGORA
    assert (por_canal[2]["gravacao"], por_canal[2]["imagem"], por_canal[2]["verificado_em"]) == ("", "", None)


def test_miniatura_e_pagina(authed_client, banco, monkeypatch: pytest.MonkeyPatch) -> None:
    db.session.add_all(
        [
            CanalCftv(ip_gravador=_IP, canal=1, situacao="ativo", analogica=True),
            CanalCftv(ip_gravador=_IP, canal=2, situacao="ativo", analogica=True),
            VerificacaoCanal(
                ip_gravador=_IP,
                canal=1,
                gravacao="parada",
                imagem="chapada",
                miniatura=cftv_imagem.miniatura(_jpeg(cena=False)),
                imagem_em=_AGORA,
            ),
            VerificacaoCanal(ip_gravador=_IP, canal=2, gravacao="ok", imagem="ok", gravacao_em=_AGORA),
        ]
    )
    db.session.commit()
    resp = authed_client.get(f"/gov/cftv/miniatura/{_IP}/1")
    assert resp.status_code == 200 and resp.mimetype == "image/jpeg"
    assert authed_client.get(f"/gov/cftv/miniatura/{_IP}/2").status_code == 404

    dev = {"host": "dvr-v", "name": "DVR V", "ip": _IP, "subcat": "dvr", "andar": "?", "gravador": "dvr-v"}
    dev |= {"is_gravador": True, "canal": "", "loja": "", "vendor": "Intelbras", "model": "MHDX 3104"}
    dev |= {"status": "up", "problems": 0, "canais_snmp": {}, "status_canal_confiavel": True}
    monkeypatch.setenv("ZABBIX_URL", "https://zabbix.test")
    with (
        patch.object(cftv_monitoring, "get_cached_cftv_summary", return_value={"enabled": True, "devices": [dev]}),
        patch.object(cg, "disparar"),
    ):
        html = authed_client.get("/gov/cftv?ver=gravadores").get_data(as_text=True)
    assert "1 canal sem gravar:" in html
    assert "1 câmera sem imagem agora:" in html
    assert f"/gov/cftv/miniatura/{_IP}/1" in html
    assert "gravando" in html


def test_miniatura_exige_login(factory_app) -> None:
    resp = factory_app.test_client().get(f"/gov/cftv/miniatura/{_IP}/1")
    assert resp.status_code in (302, 401)
