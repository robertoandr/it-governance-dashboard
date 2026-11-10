"""CFTV: foto direto da câmera IP pelo vídeo (ONVIF GetStreamUri + quadro RTSP)."""

from __future__ import annotations

import io
from pathlib import Path

import av
import httpx
import pytest
from PIL import Image

from itgov.services import camera_rtsp

_CAPACIDADES = (
    "<env:Envelope><env:Body><tds:GetCapabilitiesResponse><tds:Capabilities><tt:Media>"
    "<tt:XAddr>http://10.0.0.5:80/onvif/media</tt:XAddr></tt:Media></tds:Capabilities>"
    "</tds:GetCapabilitiesResponse></env:Body></env:Envelope>"
)
_PERFIS = (
    '<env:Envelope><env:Body><trt:GetProfilesResponse><trt:Profiles fixed="true" token="media_profile1">'
    '</trt:Profiles><trt:Profiles fixed="true" token="media_profile2"></trt:Profiles>'
    "</trt:GetProfilesResponse></env:Body></env:Envelope>"
)
_URI = (
    "<env:Envelope><env:Body><trt:GetStreamUriResponse><trt:MediaUri>"
    "<tt:Uri>rtsp://10.0.0.5:554/media/video2?a=1&amp;b=2</tt:Uri>"
    "</trt:MediaUri></trt:GetStreamUriResponse></env:Body></env:Envelope>"
)


def _cliente(pedidos: list[tuple[str, str]], recusa: bool = False) -> httpx.Client:
    def responder(req: httpx.Request) -> httpx.Response:
        corpo = req.content.decode()
        pedidos.append((str(req.url), corpo))
        if recusa:
            return httpx.Response(400, text="<env:Fault><env:Subcode>ter:NotAuthorized</env:Subcode></env:Fault>")
        if "GetCapabilities" in corpo:
            return httpx.Response(200, text=_CAPACIDADES)
        if "GetProfiles" in corpo:
            return httpx.Response(200, text=_PERFIS)
        if "GetStreamUri" in corpo:
            return httpx.Response(200, text=_URI)
        return httpx.Response(500)

    return httpx.Client(transport=httpx.MockTransport(responder))


def test_uri_rtsp_usa_o_perfil_mais_leve() -> None:
    pedidos: list[tuple[str, str]] = []
    with _cliente(pedidos) as cliente:
        uri = camera_rtsp.uri_rtsp("10.0.0.5", "u", "p", cliente)
    assert uri == "rtsp://10.0.0.5:554/media/video2?a=1&b=2"
    assert [url for url, _ in pedidos] == [
        "http://10.0.0.5/onvif/device_service",
        "http://10.0.0.5/onvif/media",
        "http://10.0.0.5/onvif/media",
    ]
    assert "<ProfileToken>media_profile2</ProfileToken>" in pedidos[-1][1]
    # Senha nunca vai em texto: só o digest do WS-UsernameToken
    assert all("PasswordDigest" in corpo and ">p<" not in corpo for _, corpo in pedidos)


def test_uri_rtsp_credencial_recusada() -> None:
    with _cliente([], recusa=True) as cliente, pytest.raises(camera_rtsp.CredencialRecusadaError):
        camera_rtsp.uri_rtsp("10.0.0.5", "u", "p", cliente)


def test_com_credencial_escapa_e_troca_a_que_vier_na_uri() -> None:
    assert (
        camera_rtsp.com_credencial("rtsp://velho:x@10.0.0.5:554/v2", "adm in", "s@n#a:1")
        == "rtsp://adm%20in:s%40n%23a%3A1@10.0.0.5:554/v2"
    )


def _video(caminho: Path) -> Path:
    with av.open(str(caminho), "w") as saida:
        fluxo = saida.add_stream("mpeg4", rate=5)
        fluxo.width, fluxo.height, fluxo.pix_fmt = 64, 48, "yuv420p"
        for i in range(3):
            quadro = av.VideoFrame.from_image(Image.new("RGB", (64, 48), (40 * i, 40 * i, 40 * i)))
            for pacote in fluxo.encode(quadro):
                saida.mux(pacote)
        for pacote in fluxo.encode():
            saida.mux(pacote)
    return caminho


def test_quadro_jpeg_do_video(tmp_path: Path) -> None:
    jpeg = camera_rtsp.quadro_jpeg(str(_video(tmp_path / "v.mp4")))
    assert jpeg is not None
    img = Image.open(io.BytesIO(jpeg))
    assert (img.format, img.size) == ("JPEG", (64, 48))


def test_foto_sem_resposta_vira_none(monkeypatch: pytest.MonkeyPatch) -> None:
    def fora(*_: object) -> str:
        raise httpx.ConnectTimeout("fora")

    monkeypatch.setattr(camera_rtsp, "uri_rtsp", fora)
    assert camera_rtsp.foto("10.0.0.5", "u", "p") is None


def test_foto_video_que_nao_abre_vira_none(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(camera_rtsp, "uri_rtsp", lambda *_: "rtsp://10.0.0.5/x")
    monkeypatch.setattr(camera_rtsp, "com_credencial", lambda *_: str(tmp_path / "nao-existe.mp4"))
    assert camera_rtsp.foto("10.0.0.5", "u", "p") is None


def test_foto_repassa_credencial_recusada(monkeypatch: pytest.MonkeyPatch) -> None:
    def recusa(*_: object) -> str:
        raise camera_rtsp.CredencialRecusadaError("10.0.0.5")

    monkeypatch.setattr(camera_rtsp, "uri_rtsp", recusa)
    with pytest.raises(camera_rtsp.CredencialRecusadaError):
        camera_rtsp.foto("10.0.0.5", "u", "p")
