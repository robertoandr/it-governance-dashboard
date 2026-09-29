"""Coletor de modelos de câmeras/gravadores CFTV — grava a tag ``model`` no Zabbix.

Os hosts de CFTV no Zabbix (grupos "CFTV/*") só têm fabricante (tag ``vendor``).
Este job consulta cada equipamento sem a tag ``model`` e grava o modelo
encontrado, que a página /gov/cftv exibe no drill-down do gravador.

### Protocolos (um por equipamento)

- Intelbras (e Dahua): CGI ``/cgi-bin/magicBox.cgi?action=getDeviceType``, digest
- Hikvision: ISAPI ``/ISAPI/System/deviceInfo``, digest
- Demais / fabricante desconhecido: ONVIF ``GetDeviceInformation`` com
  WS-UsernameToken (PasswordDigest)

### Proteção contra bloqueio de conta

Câmeras Intelbras bloqueiam o usuário por ~30 min após algumas senhas erradas.
Por isso: um único protocolo por equipamento, sem nova tentativa após 401, e a
rodada inteira é abortada se as primeiras ``_LIMITE_AUTH_INICIAL`` respostas
forem todas 401 (credencial padrão errada). Só equipamentos sem modelo são
consultados, então depois da primeira rodada o job quase não faz login.

Credencial: ``CFTV_CAM_USER`` / ``CFTV_CAM_PASS`` (env, via ``config.settings``).
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import sys
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests
import structlog
from requests.auth import HTTPDigestAuth

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import settings

log = structlog.get_logger("cftv_model_collector")

_SUBCATS = {"camera", "dvr", "nvr"}
_TIMEOUT_S = 5
_WORKERS = 8
_LIMITE_AUTH_INICIAL = 5
_TAGS_GERENCIADAS = {"model", "model_source"}


class CredencialRecusadaError(Exception):
    """O equipamento respondeu 401 à credencial padrão."""


@dataclass(frozen=True)
class Equipamento:
    """Host CFTV do Zabbix candidato à coleta de modelo."""

    hostid: str
    host: str
    ip: str
    vendor: str
    tags: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Resultado:
    """Modelo coletado (ou motivo da falha) para um equipamento."""

    equipamento: Equipamento
    model: str = ""
    fonte: str = ""
    erro: str = ""


# ── Zabbix ────────────────────────────────────────────────────────────────────


def _zbx(method: str, params: dict[str, Any]) -> Any:
    """Chama a API JSON-RPC do Zabbix 7.0 com Bearer token no header."""
    url = settings.ZABBIX_URL
    if not url.endswith("/api_jsonrpc.php"):
        url = url.rstrip("/") + "/api_jsonrpc.php"
    resp = requests.post(
        url,
        json={"jsonrpc": "2.0", "method": method, "params": params, "id": 1},
        headers={"Content-Type": "application/json-rpc", "Authorization": f"Bearer {settings.ZABBIX_TOKEN}"},
        timeout=15,
        verify=settings.ZBX_VERIFY_TLS,
    )
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"Zabbix API: {data['error']}")
    return data["result"]


def listar_sem_modelo() -> list[Equipamento]:
    """Câmeras, DVRs e NVRs dos grupos CFTV que ainda não têm a tag ``model``."""
    grupos = _zbx("hostgroup.get", {"output": ["groupid", "name"], "search": {"name": "CFTV"}})
    gids = [g["groupid"] for g in grupos if g["name"].startswith("CFTV")]
    if not gids:
        return []
    hosts = _zbx(
        "host.get",
        {
            "output": ["hostid", "host"],
            "groupids": gids,
            "selectInterfaces": ["ip", "main"],
            "selectTags": ["tag", "value"],
        },
    )
    candidatos: list[Equipamento] = []
    for h in hosts:
        tags = {t["tag"]: t["value"] for t in h.get("tags", [])}
        if tags.get("subcategory") not in _SUBCATS or tags.get("model"):
            continue
        interfaces = h.get("interfaces") or []
        ip = next((i["ip"] for i in interfaces if i.get("main") == "1"), interfaces[0]["ip"] if interfaces else "")
        if not ip:
            continue
        candidatos.append(
            Equipamento(
                hostid=h["hostid"],
                host=h["host"],
                ip=ip,
                vendor=tags.get("vendor", ""),
                tags=tuple((t["tag"], t["value"]) for t in h.get("tags", [])),
            )
        )
    return candidatos


def gravar_modelo(resultado: Resultado) -> None:
    """Grava as tags ``model`` e ``model_source`` preservando as demais tags do host.

    ``host.update`` substitui a lista inteira de tags, então reenviamos todas.
    """
    eq = resultado.equipamento
    tags = [{"tag": t, "value": v} for t, v in eq.tags if t not in _TAGS_GERENCIADAS]
    tags.append({"tag": "model", "value": resultado.model})
    tags.append({"tag": "model_source", "value": resultado.fonte})
    _zbx("host.update", {"hostid": eq.hostid, "tags": tags})


# ── Protocolos ────────────────────────────────────────────────────────────────


def _checar_auth(resp: requests.Response) -> None:
    if resp.status_code == 401:
        raise CredencialRecusadaError
    resp.raise_for_status()


def modelo_intelbras(ip: str, usuario: str, senha: str) -> str:
    """Modelo via CGI Intelbras/Dahua (``type=VIP-3230-B``)."""
    resp = requests.get(
        f"http://{ip}/cgi-bin/magicBox.cgi",
        params={"action": "getDeviceType"},
        auth=HTTPDigestAuth(usuario, senha),
        timeout=_TIMEOUT_S,
    )
    _checar_auth(resp)
    m = re.search(r"^type=(.+)$", resp.text.strip(), flags=re.MULTILINE)
    return m.group(1).strip() if m else ""


def modelo_hikvision(ip: str, usuario: str, senha: str) -> str:
    """Modelo via ISAPI Hikvision (``<model>`` do deviceInfo)."""
    resp = requests.get(f"http://{ip}/ISAPI/System/deviceInfo", auth=HTTPDigestAuth(usuario, senha), timeout=_TIMEOUT_S)
    _checar_auth(resp)
    raiz = ET.fromstring(resp.text)  # noqa: S314 — resposta de equipamento interno
    no = next((el for el in raiz.iter() if el.tag.rsplit("}", 1)[-1] == "model"), None)
    return (no.text or "").strip() if no is not None else ""


def _ws_security(usuario: str, senha: str) -> str:
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


def modelo_onvif(ip: str, usuario: str, senha: str) -> str:
    """Modelo via ONVIF ``GetDeviceInformation`` (``Manufacturer Model``)."""
    envelope = (
        '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">'
        f"<s:Header>{_ws_security(usuario, senha)}</s:Header>"
        '<s:Body><GetDeviceInformation xmlns="http://www.onvif.org/ver10/device/wsdl"/></s:Body>'
        "</s:Envelope>"
    )
    resp = requests.post(
        f"http://{ip}/onvif/device_service",
        data=envelope.encode(),
        headers={"Content-Type": "application/soap+xml; charset=utf-8"},
        timeout=_TIMEOUT_S,
    )
    # ONVIF sinaliza credencial errada como 400/401 com fault NotAuthorized
    if resp.status_code == 401 or "NotAuthorized" in resp.text:
        raise CredencialRecusadaError
    resp.raise_for_status()
    raiz = ET.fromstring(resp.text)  # noqa: S314 — resposta de equipamento interno
    campos = {el.tag.rsplit("}", 1)[-1]: (el.text or "").strip() for el in raiz.iter()}
    return campos.get("Model", "")


def protocolo_para(vendor: str) -> tuple[str, Any]:
    """Escolhe um único protocolo pelo fabricante (evita logins repetidos)."""
    v = vendor.strip().lower()
    if v in {"intelbras", "dahua"}:
        return "intelbras_cgi", modelo_intelbras
    if v == "hikvision":
        return "hikvision_isapi", modelo_hikvision
    return "onvif", modelo_onvif


def consultar(eq: Equipamento, usuario: str, senha: str) -> Resultado:
    """Consulta o modelo de um equipamento com o protocolo do seu fabricante."""
    fonte, funcao = protocolo_para(eq.vendor)
    try:
        model = funcao(eq.ip, usuario, senha)
    except CredencialRecusadaError:
        return Resultado(eq, fonte=fonte, erro="auth")
    except (requests.RequestException, ET.ParseError) as exc:
        return Resultado(eq, fonte=fonte, erro=type(exc).__name__)
    return Resultado(eq, model=model, fonte=fonte, erro="" if model else "vazio")


# ── Orquestração ──────────────────────────────────────────────────────────────


def coletar(equipamentos: list[Equipamento], usuario: str, senha: str) -> list[Resultado]:
    """Consulta os equipamentos, abortando se a credencial for recusada logo no início.

    Os primeiros ``_LIMITE_AUTH_INICIAL`` são consultados em série: se todos
    responderem 401, a credencial padrão está errada e o restante nem é tentado.
    """
    iniciais = equipamentos[:_LIMITE_AUTH_INICIAL]
    resultados = [consultar(eq, usuario, senha) for eq in iniciais]
    if iniciais and all(r.erro == "auth" for r in resultados):
        log.error("cftv_model_credencial_recusada", testados=len(iniciais), abortado=True)
        return resultados
    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        resultados += list(pool.map(lambda eq: consultar(eq, usuario, senha), equipamentos[_LIMITE_AUTH_INICIAL:]))
    return resultados


def run() -> None:
    """Entry point para o APScheduler."""
    usuario = settings.CFTV_CAM_USER
    senha = settings.CFTV_CAM_PASS
    if not (settings.ZABBIX_URL and settings.ZABBIX_TOKEN and usuario and senha):
        log.warning("cftv_model_ignorado", motivo="ZABBIX_* ou CFTV_CAM_USER/CFTV_CAM_PASS não configurados")
        return

    try:
        equipamentos = listar_sem_modelo()
    except (requests.RequestException, RuntimeError) as exc:
        log.error("cftv_model_listagem_falhou", erro=str(exc))
        return
    log.info("cftv_model_coleta_iniciada", sem_modelo=len(equipamentos))

    resultados = coletar(equipamentos, usuario, senha)
    gravados = 0
    for r in resultados:
        if not r.model:
            continue
        try:
            gravar_modelo(r)
            gravados += 1
        except (requests.RequestException, RuntimeError) as exc:
            log.warning("cftv_model_gravacao_falhou", host=r.equipamento.host, erro=str(exc))

    erros: dict[str, int] = {}
    for r in resultados:
        if r.erro:
            erros[r.erro] = erros.get(r.erro, 0) + 1
    log.info("cftv_model_coleta_concluida", consultados=len(resultados), gravados=gravados, erros=erros)
