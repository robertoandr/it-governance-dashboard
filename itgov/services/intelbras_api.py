"""Leitura de gravadores Intelbras (DVR/NVR) pela API HTTP (CGI, padrão Dahua).

Endpoints usados (todos de leitura, autenticação Basic ou Digest):

- ``magicBox.cgi?action=getSerialNo`` — testa a credencial (nº de série);
- ``magicBox.cgi?action=getDeviceType`` — modelo (ex.: ``MHDX 3132``);
- ``devVideoInput.cgi?action=getCollect`` — quantidade de canais;
- ``eventManager.cgi?action=getEventIndexes&code=VideoLoss`` — canais sem vídeo
  (índices a partir de 0; "sem eventos" volta como erro e vira lista vazia);
- ``configManager.cgi?action=getConfig&name=ChannelTitle`` — nome de cada canal;
- ``LogicDeviceManager.cgi?action=getCameraAll`` — câmera de cada canal: analógica
  (``Local``) ou IP (``Remote``, com IP, MAC e modelo). A resposta traz também a
  senha de cada câmera IP, que é descartada aqui e nunca guardada.

O aparelho bloqueia o usuário depois de algumas senhas erradas seguidas: quem
chama deve guardar a credencial que falhou e não insistir (``AcessoNegadoError``).
"""

from __future__ import annotations

import re

import httpx
from pydantic import BaseModel

CAPACIDADES = (4, 8, 16, 32, 64, 128)
# Nome de fábrica de canal sem câmera: "CAM 4", "Canal 12", "CH05", "D7"
_TITULO_PADRAO = re.compile(r"^(?:cam(?:era)?|c[aâ]mera|canal|ch|channel|d|ip ?cam)\s*0*\d+$", re.I)


class AcessoNegadoError(Exception):
    """O gravador recusou a credencial (não tentar de novo tão cedo)."""


class CameraGravador(BaseModel):
    """Câmera ligada a um canal do gravador."""

    canal: int
    tipo: str  # "analogica" ou "ip"
    ip: str = ""
    mac: str = ""
    modelo: str = ""


class LeituraGravador(BaseModel):
    """O que o gravador informou."""

    modelo: str = ""
    serie: str = ""
    canais: int = 0
    sem_video: list[int] = []
    titulos: dict[int, str] = {}
    cameras: list[CameraGravador] = []


def capacidade_do_modelo(modelo: str) -> int:
    """Canais pelo modelo: últimos dois dígitos do número (MHDX 3132 → 32, DS-7632NXI → 32).

    Returns:
        A capacidade, ou 0 se o modelo não disser.
    """
    for numero in re.findall(r"\d{4}", modelo or ""):
        canais = int(numero[-2:])
        if canais in CAPACIDADES:
            return canais
    return 0


def titulo_padrao(titulo: str) -> bool:
    """O nome do canal é o de fábrica (canal provavelmente sem câmera)?"""
    return not titulo.strip() or bool(_TITULO_PADRAO.match(titulo.strip()))


# Posição de câmera IP sem câmera: endereço de fábrica ou vazio
SEM_ENDERECO = frozenset({"", "0.0.0.0", "192.168.0.0", "192.168.1.108"})
_MAC_NULO = {"", "ff:ff:ff:ff:ff:ff", "00:00:00:00:00:00"}
_CAMPO_CAMERA = re.compile(
    r"^camera\[(\d+)\]\.(Type|Enable|UniqueChannel|DeviceInfo\.Address|DeviceInfo\.Mac|DeviceInfo\.DeviceType)=(.*)$"
)


def cameras_do_gravador(texto: str) -> list[CameraGravador]:
    """Câmeras por canal a partir da resposta de ``getCameraAll``.

    Ignora a câmera virtual (``Compose``), as posições IP sem câmera (desligadas
    ou com endereço de fábrica) e qualquer campo que não seja dos listados,
    entre eles a senha da câmera.

    Args:
        texto: Corpo da resposta.

    Returns:
        Câmeras em ordem de canal (canal a partir de 1).
    """
    brutos: dict[int, dict[str, str]] = {}
    for linha in _linhas(texto):
        m = _CAMPO_CAMERA.match(linha)
        if m:
            brutos.setdefault(int(m.group(1)), {})[m.group(2)] = m.group(3).strip()
    cameras: dict[int, CameraGravador] = {}
    for indice, campos in sorted(brutos.items()):
        tipo = campos.get("Type", "")
        unico = campos.get("UniqueChannel", "")
        canal = int(unico) + 1 if unico.isdigit() else indice + 1
        if tipo == "Local":
            cameras.setdefault(canal, CameraGravador(canal=canal, tipo="analogica"))
        elif tipo == "Remote":
            ip = campos.get("DeviceInfo.Address", "")
            if campos.get("Enable", "true").lower() != "true" or ip in SEM_ENDERECO:
                continue
            mac = campos.get("DeviceInfo.Mac", "").lower()
            cameras[canal] = CameraGravador(
                canal=canal,
                tipo="ip",
                ip=ip,
                mac="" if mac in _MAC_NULO else mac,
                modelo=campos.get("DeviceInfo.DeviceType", ""),
            )
    return [cameras[c] for c in sorted(cameras)]


def _linhas(texto: str) -> list[str]:
    return [linha.strip() for linha in texto.replace("\r", "").split("\n") if linha.strip()]


def ler_gravador(ip: str, usuario: str, senha: str, timeout: float = 8.0) -> LeituraGravador:
    """Lê modelo, canais, canais sem vídeo, nomes dos canais e câmeras.

    Args:
        ip: IP do gravador.
        usuario: Usuário da interface web.
        senha: Senha.
        timeout: Segundos por requisição.

    Returns:
        A leitura.

    Raises:
        AcessoNegadoError: Credencial recusada (401 ou "Invalid Authority").
        httpx.HTTPError: Aparelho fora do ar ou resposta inválida.
    """
    base = f"http://{ip}/cgi-bin"
    with httpx.Client(timeout=timeout) as c:
        sonda = c.get(f"{base}/magicBox.cgi?action=getSerialNo")
        desafio = sonda.headers.get("www-authenticate", "")
        auth: httpx.Auth = (
            httpx.DigestAuth(usuario, senha)
            if desafio.lower().startswith("digest")
            else httpx.BasicAuth(usuario, senha)
        )
        serie = c.get(f"{base}/magicBox.cgi?action=getSerialNo", auth=auth)
        if serie.status_code == 401 or "invalid authority" in serie.text.lower() or "sn=" not in serie.text:
            raise AcessoNegadoError(ip)

        def _get(caminho: str) -> str:
            r = c.get(f"{base}/{caminho}", auth=auth)
            return r.text if r.status_code == 200 else ""

        modelo = _get("magicBox.cgi?action=getDeviceType").partition("=")[2].strip()
        coleta = _get("devVideoInput.cgi?action=getCollect").partition("=")[2].strip()
        perdas = _get("eventManager.cgi?action=getEventIndexes&code=VideoLoss")
        titulos_txt = _get("configManager.cgi?action=getConfig&name=ChannelTitle")
        cameras = cameras_do_gravador(_get("LogicDeviceManager.cgi?action=getCameraAll"))

    sem_video = sorted({int(m) + 1 for m in re.findall(r"channels\[\d+\]=(\d+)", perdas)})
    titulos = {
        int(m.group(1)) + 1: m.group(2).strip() for m in re.finditer(r"ChannelTitle\[(\d+)\]\.Name=(.*)", titulos_txt)
    }
    # NVR responde 0 em getCollect (canais IP): usa os nomes de canal ou o modelo
    canais = int(coleta) if coleta.isdigit() and int(coleta) > 0 else (len(titulos) or capacidade_do_modelo(modelo))
    return LeituraGravador(
        modelo=modelo,
        serie=serie.text.partition("=")[2].strip(),
        canais=canais,
        sem_video=sem_video,
        titulos=titulos,
        cameras=cameras,
    )
