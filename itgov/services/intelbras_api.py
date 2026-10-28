"""Leitura de gravadores Intelbras (DVR/NVR) pela API HTTP (CGI, padrão Dahua).

Endpoints usados (todos de leitura, autenticação Basic ou Digest):

- ``magicBox.cgi?action=getSerialNo`` — testa a credencial (nº de série);
- ``magicBox.cgi?action=getDeviceType`` — modelo (ex.: ``MHDX 3132``);
- ``devVideoInput.cgi?action=getCollect`` — quantidade de canais;
- ``eventManager.cgi?action=getEventIndexes&code=VideoLoss`` — canais sem vídeo
  (índices a partir de 0; "sem eventos" volta como erro e vira lista vazia);
- ``configManager.cgi?action=getConfig&name=ChannelTitle`` — nome de cada canal;
- ``LogicDeviceManager.cgi?action=getCameraAll`` — câmera de cada canal: analógica
  (``Local``) ou IP (``Remote``, com IP, MAC, modelo, nº de série e fabricante). A resposta traz também a
  senha de cada câmera IP, que é descartada aqui e nunca guardada.

O aparelho bloqueia o usuário depois de algumas senhas erradas seguidas: quem
chama deve guardar a credencial que falhou e não insistir (``AcessoNegadoError``).
"""

from __future__ import annotations

import ipaddress
import re
from datetime import datetime

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
    serie: str = ""
    fabricante: str = ""


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
    r"^camera\[(\d+)\]\.(Type|Enable|UniqueChannel|DeviceInfo\.(?:Address|Mac|DeviceType|SerialNo|Vendor))=(.*)$"
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
                serie=campos.get("DeviceInfo.SerialNo", ""),
                fabricante=campos.get("DeviceInfo.Vendor", ""),
            )
    return [cameras[c] for c in sorted(cameras)]


def _linhas(texto: str) -> list[str]:
    return [linha.strip() for linha in texto.replace("\r", "").split("\n") if linha.strip()]


def _autenticar(c: httpx.Client, ip: str, usuario: str, senha: str) -> tuple[httpx.Auth, httpx.Response]:
    """Descobre se o aparelho pede Digest ou Basic e confere a credencial pelo nº de série.

    Returns:
        A autenticação e a resposta do nº de série.

    Raises:
        AcessoNegadoError: Credencial recusada.
    """
    base = f"http://{ip}/cgi-bin"
    desafio = c.get(f"{base}/magicBox.cgi?action=getSerialNo").headers.get("www-authenticate", "")
    auth: httpx.Auth = (
        httpx.DigestAuth(usuario, senha) if desafio.lower().startswith("digest") else httpx.BasicAuth(usuario, senha)
    )
    serie = c.get(f"{base}/magicBox.cgi?action=getSerialNo", auth=auth)
    if serie.status_code == 401 or "invalid authority" in serie.text.lower() or "sn=" not in serie.text:
        raise AcessoNegadoError(ip)
    return auth, serie


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
        auth, serie = _autenticar(c, ip, usuario, senha)

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


def trocar_senha(ip: str, usuario: str, senha_atual: str, senha_nova: str, timeout: float = 8.0) -> None:
    """Troca a senha do usuário no gravador/câmera Intelbras/Dahua (CGI ``userManager``).

    Autentica com a credencial atual (conferida pelo nº de série em :func:`_autenticar`)
    e grava a nova senha no mesmo usuário — não cria usuário nem muda o nome, só a senha.

    Args:
        ip: IP do equipamento.
        usuario: Usuário atual, mantido.
        senha_atual: Senha que o equipamento aceita hoje.
        senha_nova: Nova senha a gravar.
        timeout: Segundos por requisição.

    Raises:
        AcessoNegadoError: Credencial atual recusada (401).
        httpx.HTTPError: Aparelho fora do ar ou recusou a troca.
        ValueError: ``ip`` não é um endereço IP válido.
    """
    # Valida e normaliza o IP antes de montar a URL (corta SSRF: o alvo nunca é
    # um valor arbitrário vindo do formulário, só um IP bem-formado).
    ip = str(ipaddress.ip_address(ip))
    base = f"http://{ip}/cgi-bin"
    with httpx.Client(timeout=timeout) as c:
        auth, _ = _autenticar(c, ip, usuario, senha_atual)
        r = c.get(
            f"{base}/userManager.cgi",
            params={"action": "modifyPassword", "name": usuario, "pwd": senha_nova, "pwdOld": senha_atual},
            auth=auth,
        )
        if r.status_code == 401:
            raise AcessoNegadoError(ip)
        r.raise_for_status()


_FORMATO_HORA = "%Y-%m-%d %H:%M:%S"


def _hora_url(quando: datetime) -> str:
    # O aparelho só aceita o espaço como %20 (com "+" responde 400)
    return quando.strftime(_FORMATO_HORA).replace(" ", "%20")


class SessaoGravador:
    """Conexão autenticada com um gravador Intelbras para checar gravação e imagem.

    Uso::

        with SessaoGravador(ip, usuario, senha) as g:
            agora = g.hora()
            fim = g.ultima_gravacao(3, agora - timedelta(hours=2), agora)
            jpeg = g.foto(3)

    Os horários do aparelho são do relógio dele (hora local, sem fuso).

    Raises:
        AcessoNegadoError: Na entrada, se a credencial for recusada.
        httpx.HTTPError: Na entrada, se o aparelho não responder.
    """

    def __init__(self, ip: str, usuario: str, senha: str, timeout: float = 15.0) -> None:
        self.ip = ip
        self._base = f"http://{ip}/cgi-bin"
        self._usuario, self._senha, self._timeout = usuario, senha, timeout
        self._c: httpx.Client | None = None
        self._auth: httpx.Auth | None = None
        self._busca = ""

    def __enter__(self) -> SessaoGravador:
        self._c = httpx.Client(timeout=self._timeout)
        try:
            self._auth, _ = _autenticar(self._c, self.ip, self._usuario, self._senha)
        except (AcessoNegadoError, httpx.HTTPError):
            self._c.close()
            raise
        return self

    def __exit__(self, *_: object) -> None:
        if self._c is None:
            return
        if self._busca:
            for acao in ("close", "destroy"):
                try:
                    self._get(f"mediaFileFind.cgi?action={acao}&object={self._busca}")
                except httpx.HTTPError:
                    break
        self._c.close()

    def _get(self, caminho: str, timeout: float | None = None) -> httpx.Response:
        if self._c is None:
            raise RuntimeError("SessaoGravador: use dentro de with")
        # httpx.URL mantém o %20 da data (params= trocaria por "+")
        return self._c.get(httpx.URL(f"{self._base}/{caminho}"), auth=self._auth, timeout=timeout or self._timeout)

    def hora(self) -> datetime:
        """Relógio do aparelho (``global.cgi?action=getCurrentTime``)."""
        texto = self._get("global.cgi?action=getCurrentTime").text.strip().partition("=")[2]
        return datetime.strptime(texto, _FORMATO_HORA)

    def modos_gravacao(self) -> dict[int, int]:
        """Canal (a partir de 1) → modo: 0 automático (agenda), 1 manual, 2 desligado."""
        texto = self._get("configManager.cgi?action=getConfig&name=RecordMode").text
        return {int(c) + 1: int(m) for c, m in re.findall(r"RecordMode\[(\d+)\]\.Mode=(\d+)", texto)}

    def ultima_gravacao(self, canal: int, inicio: datetime, fim: datetime) -> datetime | None:
        """Fim do arquivo gravado mais recente do canal no período (``mediaFileFind``).

        Args:
            canal: Canal a partir de 1.
            inicio: Início do período (relógio do aparelho).
            fim: Fim do período (relógio do aparelho).

        Returns:
            O horário, ou None se não há gravação no período (o aparelho
            responde 400 quando não acha nada).
        """
        if not self._busca:
            self._busca = self._get("mediaFileFind.cgi?action=factory.create").text.strip().partition("=")[2]
        achou = self._get(
            f"mediaFileFind.cgi?action=findFile&object={self._busca}&condition.Channel={canal}"
            f"&condition.StartTime={_hora_url(inicio)}&condition.EndTime={_hora_url(fim)}"
        )
        if achou.status_code != 200:
            return None
        texto = self._get(f"mediaFileFind.cgi?action=findNextFile&object={self._busca}&count=100").text
        fins = []
        for valor in re.findall(r"items\[\d+\]\.EndTime=(.+)", texto):
            try:
                fins.append(datetime.strptime(valor.strip(), _FORMATO_HORA))
            except ValueError:
                continue
        return max(fins, default=None)

    def foto(self, canal: int, timeout: float = 20.0) -> bytes | None:
        """Foto atual do canal (``snapshot.cgi``), ou None se a câmera não entregar imagem."""
        try:
            r = self._get(f"snapshot.cgi?channel={canal}", timeout=timeout)
        except httpx.TimeoutException:
            return None
        if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image"):
            return None
        return r.content
