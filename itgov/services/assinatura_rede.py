"""Reconhece câmeras e gravadores pela resposta HTTP, sem senha.

A descoberta de rede não vê o MAC de quem está em outra sub-rede, e muita
câmera não deixa a porta RTSP aberta para o servidor: na página Rede ela caía
como "outro — só responde a ping". Mas o próprio aparelho diz o que é quando
recebe um pedido sem login:

- Hikvision: ``/ISAPI/System/deviceInfo`` responde 401 com ``realm`` igual ao
  tipo do aparelho (``IP Camera(GR165)``, ``DS-2CD1021…``; gravador:
  ``DS-7632…``, ``NVR``, ``DVR``);
- Intelbras/Dahua: ``/cgi-bin/magicBox.cgi`` responde 401 com ``realm``
  ``Login to <série>`` ou ``Device_CGI``. Câmera, leitor facial e gravador
  respondem igual; sem senha não dá para separar.

Nenhuma senha é enviada: o 401 é o desafio de login, não uma tentativa
recusada, então não conta para o bloqueio de usuário do aparelho.
"""

from __future__ import annotations

import re

import httpx
from pydantic import BaseModel

_REALM = re.compile(r'realm="([^"]*)"', re.I)
_HIK_CAMERA = re.compile(r"ip ?camera|^ds-2|^ipc", re.I)
_HIK_GRAVADOR = re.compile(r"nvr|dvr|^ds-[789]|^ids-[789]", re.I)
_INTELBRAS = re.compile(r"^login to [0-9a-z]{8,}$|^device_cgi$", re.I)

ISAPI = "/ISAPI/System/deviceInfo"
CGI = "/cgi-bin/magicBox.cgi?action=getDeviceType"


class Assinatura(BaseModel):
    """O que o aparelho revelou.

    Attributes:
        marca: ``Hikvision`` ou ``Intelbras/Dahua``.
        tipo: ``camera``, ``gravador`` ou ``cftv`` (câmera, facial ou gravador:
            Intelbras sem senha não diz qual).
        evidencia: O ``realm`` que o aparelho mandou.
    """

    marca: str
    tipo: str
    evidencia: str

    @property
    def motivo(self) -> str:
        """Texto para a coluna "motivo" da página Rede."""
        rotulo = " (NVR/DVR)" if self.tipo == "gravador" else ""
        return f"{self.marca}{rotulo}: {self.evidencia}"


def realm(cabecalho: str) -> str:
    """O ``realm`` de um ``WWW-Authenticate`` (vazio se não houver)."""
    m = _REALM.search(cabecalho or "")
    return m.group(1).strip() if m else ""


def de_hikvision(status: int, cabecalho: str) -> Assinatura | None:
    """Assinatura pela resposta do ISAPI (Hikvision), ou None se não for um."""
    r = realm(cabecalho)
    if status != 401 or not r:
        return None
    if _HIK_GRAVADOR.search(r):
        return Assinatura(marca="Hikvision", tipo="gravador", evidencia=r[:60])
    if _HIK_CAMERA.search(r):
        return Assinatura(marca="Hikvision", tipo="camera", evidencia=r[:60])
    return None


def de_intelbras(status: int, cabecalho: str) -> Assinatura | None:
    """Assinatura pela resposta do CGI (Intelbras/Dahua), ou None se não for um."""
    r = realm(cabecalho)
    if status != 401 or not _INTELBRAS.match(r):
        return None
    evidencia = "Device_CGI" if r.lower() == "device_cgi" else "Login to <série>"
    return Assinatura(marca="Intelbras/Dahua", tipo="cftv", evidencia=evidencia)


def sondar(ip: str, timeout: float = 4.0) -> Assinatura | None:
    """Pergunta ao IP, sem senha, se ele é Hikvision ou Intelbras/Dahua.

    Args:
        ip: Endereço a testar (HTTP na porta 80).
        timeout: Segundos por pedido.

    Returns:
        A assinatura, ou None se o aparelho não respondeu como câmera/gravador.
    """
    with httpx.Client(timeout=timeout, follow_redirects=False) as c:
        try:
            r = c.get(f"http://{ip}{ISAPI}")
        except httpx.HTTPError:
            return None  # sem HTTP: não adianta tentar o CGI
        achado = de_hikvision(r.status_code, r.headers.get("www-authenticate", ""))
        if achado:
            return achado
        try:
            r = c.get(f"http://{ip}{CGI}")
        except httpx.HTTPError:
            return None
        return de_intelbras(r.status_code, r.headers.get("www-authenticate", ""))
