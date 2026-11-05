"""Câmera/gravador reconhecido pela resposta HTTP sem senha, e o uso disso na página Rede."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from app.services import assinatura_agente as ag
from app.services.inventario_rede import grupo
from itgov.services import assinatura_rede as ar

_AGORA = datetime(2026, 11, 5, 18, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("realm", "tipo"),
    [
        ('Digest qop="auth", realm="IP Camera(GR165)", nonce="x"', "camera"),
        ('Digest realm="DS-2CD1021G2-LIU"', "camera"),
        ('Digest realm="DS-7632NXI-K2"', "gravador"),
        ('Digest realm="NVR"', "gravador"),
        ('Digest realm="asyncesp"', None),  # outro aparelho com Digest: não é câmera
        ("", None),
    ],
)
def test_de_hikvision(realm: str, tipo: str | None) -> None:
    a = ar.de_hikvision(401, realm)
    assert (a.tipo if a else None) == tipo
    assert ar.de_hikvision(200, realm) is None


@pytest.mark.parametrize(
    ("realm", "ok"),
    [
        ('Digest realm="Login to 44626daa03092e2b7235663aae4065f"', True),
        ('Digest realm="Login to 630AE4EF3596DA94"', True),
        ('Basic realm="Device_CGI"', True),
        ('Digest realm="Login to router"', False),
        ('Basic realm="Restricted"', False),
    ],
)
def test_de_intelbras(realm: str, ok: bool) -> None:
    a = ar.de_intelbras(401, realm)
    assert (a is not None) is ok
    if a:
        assert a.marca == "Intelbras/Dahua" and a.tipo == "cftv" and "série" not in realm


def test_motivo_de_gravador_cai_na_aba_gravadores() -> None:
    cam = ar.Assinatura(marca="Hikvision", tipo="camera", evidencia="IP Camera(GR165)")
    grav = ar.Assinatura(marca="Hikvision", tipo="gravador", evidencia="DS-7632NXI-K2")
    assert grupo({"ip": "1.1.1.1", "tipo_sugerido": "camera", "motivo": cam.motivo}, None, set(), set()) == "cameras"
    assert grupo({"ip": "1.1.1.2", "tipo_sugerido": "camera", "motivo": grav.motivo}, None, set(), set()) == (
        "gravadores"
    )


def _transporte(rotas: dict[str, httpx.Response]) -> httpx.MockTransport:
    def responder(req: httpx.Request) -> httpx.Response:
        chave = f"{req.url.host}{req.url.raw_path.decode()}"
        if chave in rotas:
            return rotas[chave]
        raise httpx.ConnectError("sem rota", request=req)

    return httpx.MockTransport(responder)


def test_sondar_hikvision_intelbras_e_nada(monkeypatch: pytest.MonkeyPatch) -> None:
    rotas = {
        f"10.0.0.1{ar.ISAPI}": httpx.Response(401, headers={"WWW-Authenticate": 'Digest realm="IP Camera(GR165)"'}),
        f"10.0.0.2{ar.ISAPI}": httpx.Response(404),
        f"10.0.0.2{ar.CGI}": httpx.Response(
            401, headers={"WWW-Authenticate": 'Digest realm="Login to ABCDEF0123456789"'}
        ),
        f"10.0.0.3{ar.ISAPI}": httpx.Response(200, text="<html>roteador</html>"),
        f"10.0.0.3{ar.CGI}": httpx.Response(404),
    }
    original = httpx.Client

    def cliente(**kw: object) -> httpx.Client:
        return original(transport=_transporte(rotas), **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(ar.httpx, "Client", cliente)
    assert ar.sondar("10.0.0.1").marca == "Hikvision"  # type: ignore[union-attr]
    assert ar.sondar("10.0.0.2").marca == "Intelbras/Dahua"  # type: ignore[union-attr]
    assert ar.sondar("10.0.0.3") is None
    assert ar.sondar("10.0.0.4") is None  # sem HTTP


# ── agente ───────────────────────────────────────────────────────────────────


@pytest.fixture
def arquivo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    caminho = tmp_path / "assinaturas.json"
    monkeypatch.setenv("ASSINATURAS_PATH", str(caminho))
    return caminho


def test_a_sondar_respeita_tipo_online_e_espera() -> None:
    hosts = [
        {"ip": "1", "online": True, "tipo_sugerido": "outro"},
        {"ip": "2", "online": False, "tipo_sugerido": "outro"},
        {"ip": "3", "online": True, "tipo_sugerido": "camera"},
        {"ip": "4", "online": True, "tipo_sugerido": "outro"},
        {"ip": "5", "online": True, "tipo_sugerido": "outro"},
    ]
    cache = {
        "4": {"marca": "", "em": (_AGORA - timedelta(hours=2)).isoformat()},  # negativo recente: espera
        "5": {"marca": "", "em": (_AGORA - timedelta(days=2)).isoformat()},  # venceu
    }
    assert ag.a_sondar(hosts, cache, _AGORA) == ["1", "5"]


def test_rodada_grava_e_carregar_le(arquivo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from itgov.api.v1 import rede_monitoring

    hosts = [
        {"ip": "10.0.0.1", "online": True, "tipo_sugerido": "outro"},
        {"ip": "10.0.0.9", "online": True, "tipo_sugerido": "outro"},
    ]
    monkeypatch.setattr(rede_monitoring, "get_cached_rede_summary", lambda: {"hosts": hosts})
    sig = ar.Assinatura(marca="Hikvision", tipo="camera", evidencia="IP Camera(GR165)")
    monkeypatch.setattr(ar, "sondar", lambda ip: sig if ip == "10.0.0.1" else None)
    assert ag.rodada(_AGORA) == {"sondados": 2, "achados": 1}
    cache = ag.carregar()
    assert ag.assinatura("10.0.0.1", cache) == sig
    assert ag.assinatura("10.0.0.9", cache) is None and cache["10.0.0.9"]["marca"] == ""
    assert ag.rodada(_AGORA + timedelta(hours=1)) == {"sondados": 0, "achados": 0}  # ninguém venceu


def test_carregar_sem_arquivo_ou_ilegivel(arquivo: Path) -> None:
    assert ag.carregar() == {}
    arquivo.write_text("{quebrado", encoding="utf-8")
    assert ag.carregar() == {}


def test_pagina_rede_usa_a_assinatura(arquivo: Path, factory_app) -> None:
    from itgov.api.v1 import rede_monitoring

    arquivo.write_text(
        '{"172.29.11.149": {"marca": "Hikvision", "tipo": "camera", "evidencia": "IP Camera(GR165)", "em": "x"}}',
        encoding="utf-8",
    )
    hosts = [
        {"ip": "172.29.11.149", "tipo_sugerido": "outro", "motivo": "só responde a ping"},
        {"ip": "172.29.11.150", "tipo_sugerido": "endpoint", "motivo": "Windows"},
    ]
    with factory_app.app_context():
        rede_monitoring._enriquecer(hosts, {}, _AGORA)
    assert (hosts[0]["tipo_sugerido"], hosts[0]["motivo"]) == ("camera", "Hikvision: IP Camera(GR165)")
    assert hosts[1]["tipo_sugerido"] == "endpoint"


def test_agendador_nao_liga_em_teste(factory_app) -> None:
    assert ag.iniciar_agendador(factory_app) is False
