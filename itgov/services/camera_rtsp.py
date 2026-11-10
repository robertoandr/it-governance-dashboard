"""Foto direto da câmera IP, pelo vídeo: ONVIF ``GetStreamUri`` + um quadro do RTSP.

Para os canais em que o gravador não entrega a foto: o NVD 1432 da Sede
devolve HTTP 500 no ``snapshot.cgi`` para câmera ONVIF (CIP-D1312-M /
CIP-B1312-M, Uniview OEM), e a própria câmera responde ``ActionNotSupported``
ao ``GetSnapshotUri``. O vídeo funciona: pega-se o primeiro quadro do fluxo
mais leve (último perfil ONVIF, normalmente o substream 640x360) e ele vira JPEG.

Uma tentativa por câmera e por rodada; credencial recusada não é repetida.
"""

from __future__ import annotations

import base64
import hashlib
import io
import os
import re
from datetime import UTC, datetime
from urllib.parse import quote, urlsplit

import av
import httpx
import structlog

log = structlog.get_logger(__name__)

TIMEOUT_ONVIF = 10.0
TIMEOUT_RTSP = 10.0
MAX_QUADROS = 300  # pacotes até desistir de achar um quadro decodificável (~10 s de vídeo)
_CABECALHO = {"Content-Type": "application/soap+xml; charset=utf-8"}
_NS_MEDIA = "http://www.onvif.org/ver10/media/wsdl"


class CredencialRecusadaError(Exception):
    """A câmera recusou usuário/senha (ONVIF ``NotAuthorized`` ou HTTP 401)."""


def _ws_security(usuario: str, senha: str) -> str:
    """Cabeçalho WS-UsernameToken (PasswordDigest), o mesmo do coletor de modelos."""
    nonce = os.urandom(16)
    criado = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    # SHA1 é exigido pelo padrão WS-Security UsernameToken (ONVIF), não é escolha nossa
    digest = base64.b64encode(
        hashlib.sha1(nonce + criado.encode() + senha.encode(), usedforsecurity=False).digest()
    ).decode()
    return (
        '<Security s:mustUnderstand="1" xmlns="http://docs.oasis-open.org/wss/2004/01/'
        'oasis-200401-wss-wssecurity-secext-1.0.xsd"><UsernameToken>'
        f"<Username>{usuario}</Username>"
        '<Password Type="http://docs.oasis-open.org/wss/2004/01/'
        f'oasis-200401-wss-username-token-profile-1.0#PasswordDigest">{digest}</Password>'
        '<Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/'
        f'oasis-200401-wss-soap-message-security-1.0#Base64Binary">{base64.b64encode(nonce).decode()}</Nonce>'
        '<Created xmlns="http://docs.oasis-open.org/wss/2004/01/'
        f'oasis-200401-wss-wssecurity-utility-1.0.xsd">{criado}</Created>'
        "</UsernameToken></Security>"
    )


def _soap(cliente: httpx.Client, url: str, corpo: str, usuario: str, senha: str) -> str:
    envelope = (
        '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">'
        f"<s:Header>{_ws_security(usuario, senha)}</s:Header><s:Body>{corpo}</s:Body></s:Envelope>"
    )
    resp = cliente.post(url, content=envelope.encode(), headers=_CABECALHO)
    if resp.status_code == 401 or "NotAuthorized" in resp.text:
        raise CredencialRecusadaError(urlsplit(url).hostname or "")
    resp.raise_for_status()
    return resp.text


def _primeiro(padrao: str, texto: str) -> str | None:
    achado = re.search(padrao, texto)
    return achado.group(1) if achado else None


def uri_rtsp(ip: str, usuario: str, senha: str, cliente: httpx.Client) -> str:
    """Endereço RTSP do perfil mais leve da câmera (sem credencial embutida).

    Args:
        ip: IP da câmera.
        usuario: Usuário ONVIF.
        senha: Senha ONVIF.
        cliente: Cliente HTTP (com timeout).

    Returns:
        ``rtsp://...`` informado pela câmera.

    Raises:
        CredencialRecusadaError: A câmera recusou a credencial.
        ValueError: Resposta ONVIF sem perfil ou sem URI.
        httpx.HTTPError: Falha de rede ou HTTP.
    """
    capacidades = _soap(
        cliente,
        f"http://{ip}/onvif/device_service",
        '<GetCapabilities xmlns="http://www.onvif.org/ver10/device/wsdl"><Category>Media</Category></GetCapabilities>',
        usuario,
        senha,
    )
    media = _primeiro(r"<(?:\w+:)?XAddr>([^<]*[Mm]edia[^<]*)<", capacidades) or f"http://{ip}/onvif/media"
    perfis = re.findall(
        r'<(?:\w+:)?Profiles[^>]*\btoken="([^"]+)"',
        _soap(cliente, media, f'<GetProfiles xmlns="{_NS_MEDIA}"/>', usuario, senha),
    )
    if not perfis:
        raise ValueError("câmera sem perfil ONVIF")
    pedido = (
        f'<GetStreamUri xmlns="{_NS_MEDIA}"><StreamSetup>'
        '<Stream xmlns="http://www.onvif.org/ver10/schema">RTP-Unicast</Stream>'
        '<Transport xmlns="http://www.onvif.org/ver10/schema"><Protocol>RTSP</Protocol></Transport>'
        f"</StreamSetup><ProfileToken>{perfis[-1]}</ProfileToken></GetStreamUri>"
    )
    uri = _primeiro(r"<(?:\w+:)?Uri>([^<]+)<", _soap(cliente, media, pedido, usuario, senha))
    if not uri:
        raise ValueError("câmera sem URI RTSP")
    return uri.replace("&amp;", "&")


def com_credencial(uri: str, usuario: str, senha: str) -> str:
    """``rtsp://host/...`` → ``rtsp://usuario:senha@host/...`` (escapando os dois)."""
    esquema, resto = uri.split("://", 1)
    resto = resto.split("@", 1)[-1]  # câmera que já devolve credencial na URI
    return f"{esquema}://{quote(usuario, safe='')}:{quote(senha, safe='')}@{resto}"


def quadro_jpeg(origem: str, timeout: float = TIMEOUT_RTSP) -> bytes | None:
    """Primeiro quadro de vídeo de ``origem`` (RTSP ou arquivo) em JPEG.

    Args:
        origem: Endereço do vídeo (com credencial, se RTSP).
        timeout: Segundos para abrir e para cada leitura.

    Returns:
        JPEG do quadro, ou None se nenhum quadro foi decodificado.

    Raises:
        av.FFmpegError: Falha ao abrir ou ler o vídeo.
    """
    opcoes = {"rtsp_transport": "tcp"} if origem.startswith("rtsp://") else {}
    with av.open(origem, options=opcoes, timeout=timeout) as entrada:
        video = entrada.streams.video[0]
        for i, pacote in enumerate(entrada.demux(video)):
            if i >= MAX_QUADROS:
                break
            for quadro in pacote.decode():
                saida = io.BytesIO()
                quadro.to_image().save(saida, format="JPEG", quality=80)
                return saida.getvalue()
    return None


def foto(ip: str, usuario: str, senha: str) -> bytes | None:
    """Foto atual da câmera pelo vídeo, ou None se ela não entregar.

    Args:
        ip: IP da câmera.
        usuario: Usuário da câmera.
        senha: Senha da câmera.

    Returns:
        JPEG, ou None (fora do ar, sem ONVIF, vídeo que não abre).

    Raises:
        CredencialRecusadaError: A câmera recusou a credencial (não insistir).
    """
    try:
        with httpx.Client(timeout=TIMEOUT_ONVIF) as cliente:
            uri = uri_rtsp(ip, usuario, senha, cliente)
        return quadro_jpeg(com_credencial(uri, usuario, senha))
    except (httpx.HTTPError, ValueError, av.FFmpegError, OSError) as exc:
        log.info("camera_rtsp.sem_foto", ip=ip, erro=type(exc).__name__)
        return None
