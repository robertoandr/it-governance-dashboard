"""Cliente da API de EDR do Acronis (``/api/mdr/v1``) para o agente de incidentes.

Referência: https://developer.acronis.com/doc/mdr/v1/reference/openapi.json

Leitura (usada pelo agente):

- ``listar_abertos``: incidentes não mitigados cuja investigação não foi
  encerrada (``state`` diferente de ``CLOSED``/``FALSE_POSITIVE``);
- ``detalhe``: o incidente com as detecções (o que aconteceu) e as ações de
  resposta que o Acronis oferece para ele.

Escrita (só depois de aprovação na fila, ver ``app.services.incidentes_agente``):

- ``atualizar_estado``: muda o estado de investigação com comentário;
- ``executar_acao``: dispara uma ação de resposta da lista ``ACOES_PERMITIDAS``.
  Desligar/reiniciar máquina, apagar arquivo, liberar na allowlist, rollback e
  patch ficam de fora de propósito: são feitos no console do Acronis, por gente;
- ``status_acao``: acompanha a ação disparada.

Mesmas credenciais da investigação ao vivo (``acronis_cyber._creds``).
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import httpx
import structlog
from pydantic import BaseModel

from itgov.services import acronis_cyber

log = structlog.get_logger(__name__)

_BASE = "/api/mdr/v1"
_TIMEOUT = 60
_ANALYTICS = "itgov-dashboard"

ESTADOS_FINAIS = ("CLOSED", "FALSE_POSITIVE")
ESTADOS_PERMITIDOS = ("TRIAGED", "FALSE_POSITIVE", "CLOSED")
ACOES_PERMITIDAS = (
    "WORKLOAD_ISOLATE",
    "WORKLOAD_DEISOLATE",
    "PROCESS_STOP",
    "PROCESS_STOP_TREE",
    "PROCESS_FILE_QUARANTINE",
    "ADD_ALL_PROCESS_FILE_URL_TO_BLOCK_LIST",
)


class IncidenteNaoEncontradoError(Exception):
    """O Acronis não tem mais o incidente (410/404): a máquina saiu ou o incidente foi apagado."""


class AcaoNaoPermitidaError(ValueError):
    """Ação fora de ``ACOES_PERMITIDAS`` ou não oferecida pelo Acronis para o incidente."""


class Deteccao(BaseModel):
    """Uma detecção do incidente, resumida para a triagem.

    Attributes:
        quando: ``event_time``.
        motor: ``BEHAVIORAL``, ``URLF``, ``FILE``...
        descricao: O que o Acronis viu.
        ameaca: ``threat_name``.
        veredito: ``MALICIOUS``/``SUSPICIOUS``.
        confianca: 0–100, quando vem.
        bloqueado: Se o Acronis informou que bloqueou (``is_blocked``).
        alvo: URL, arquivo ou processo envolvido.
    """

    quando: str = ""
    motor: str = ""
    descricao: str = ""
    ameaca: str = ""
    veredito: str = ""
    confianca: int | None = None
    bloqueado: bool | None = None
    alvo: str = ""


class IncidenteEdr(BaseModel):
    """Incidente do EDR com o que a triagem precisa.

    Attributes:
        incident_id: Id do incidente.
        customer_id: Tenant do incidente (exigido nas chamadas).
        maquina: ``host_name``.
        criado_em: ``created_at``.
        atualizado_em: ``updated_at``.
        severidade: ``LOW``…``CRITICAL``.
        veredito: ``MALICIOUS``/``SUSPICIOUS``...
        estado: Estado da investigação (``NOT_STARTED``...).
        mitigacao: ``NOT_MITIGATED``/``MITIGATED``/``AUTO_MITIGATED``.
        categorias: ``URL_BLOCKED``, ``POTENTIALLY_UNWANTED_APPLICATION``...
        link: Endereço do incidente no console.
        acoes: Ação → ``workload_id`` das ações oferecidas pelo Acronis.
        deteccoes: Detecções resumidas (só no ``detalhe``).
    """

    incident_id: str
    customer_id: str
    maquina: str = ""
    criado_em: str = ""
    atualizado_em: str = ""
    severidade: str = ""
    veredito: str = ""
    estado: str = ""
    mitigacao: str = ""
    categorias: list[str] = []
    link: str = ""
    acoes: dict[str, str] = {}
    deteccoes: list[Deteccao] = []


def configurado() -> bool:
    """Há base e credenciais do Acronis?"""
    return acronis_cyber.configurado()


def _cliente() -> httpx.Client:
    """Cliente autenticado (token client_credentials) na base do Acronis."""
    base = os.getenv("ACRONIS_BASE_URL", "").rstrip("/")
    client_id, secret = acronis_cyber._creds()
    resp = httpx.post(
        f"{base}/api/2/idp/token",
        auth=(client_id, secret),
        data={"grant_type": "client_credentials"},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return httpx.Client(
        base_url=f"{base}{_BASE}",
        headers={"Authorization": f"Bearer {resp.json()['access_token']}"},
        timeout=_TIMEOUT,
    )


def _categorias(raw: Any) -> list[str]:
    return [str(c) for c in raw] if isinstance(raw, list) else []


def _deteccao(raw: dict[str, Any]) -> Deteccao:
    processo = raw.get("process") if isinstance(raw.get("process"), dict) else {}
    arquivo = raw.get("file") if isinstance(raw.get("file"), dict) else {}
    alvo = raw.get("dest_url") or arquivo.get("name") or processo.get("name") or processo.get("path") or ""
    confianca = raw.get("confidence")
    return Deteccao(
        quando=str(raw.get("event_time") or ""),
        motor=str(raw.get("engine") or ""),
        descricao=str(raw.get("description") or "")[:300],
        ameaca=str(raw.get("threat_name") or "")[:120],
        veredito=str(raw.get("verdict") or ""),
        confianca=int(confianca) if isinstance(confianca, int | float) else None,
        bloqueado=raw.get("is_blocked") if isinstance(raw.get("is_blocked"), bool) else None,
        alvo=str(alvo)[:200],
    )


def normalizar(raw: dict[str, Any]) -> IncidenteEdr:
    """Converte um item de ``/incidents`` ou de ``/incidents/{id}``."""
    acoes = {
        str(a.get("action")): str(a.get("workload_id") or "")
        for a in raw.get("incident_actions") or []
        if isinstance(a, dict) and a.get("action")
    }
    return IncidenteEdr(
        incident_id=str(raw.get("incident_id") or ""),
        customer_id=str(raw.get("customer_id") or ""),
        maquina=str(raw.get("host_name") or ""),
        criado_em=str(raw.get("created_at") or ""),
        atualizado_em=str(raw.get("updated_at") or ""),
        severidade=str(raw.get("severity") or ""),
        veredito=str(raw.get("verdict") or ""),
        estado=str(raw.get("state") or ""),
        mitigacao=str(raw.get("mitigation_state") or ""),
        categorias=_categorias(raw.get("incident_categories")),
        link=str(raw.get("incident_link") or ""),
        acoes=acoes,
        deteccoes=[_deteccao(d) for d in (raw.get("detections") or [])[:10] if isinstance(d, dict)],
    )


def listar_abertos(limite: int = 2000) -> list[IncidenteEdr]:
    """Incidentes não mitigados com investigação em aberto, do mais novo ao mais antigo.

    Raises:
        httpx.HTTPError: Falha ao falar com o Acronis.
    """
    itens: list[dict[str, Any]] = []
    with _cliente() as c:
        params: dict[str, Any] = {"limit": 200, "mitigation_state": "NOT_MITIGATED", "analytics_source": _ANALYTICS}
        while len(itens) < limite:
            resp = c.get("/incidents", params=params)
            resp.raise_for_status()
            dados = resp.json()
            itens += dados.get("items") or []
            if not dados.get("cursor") or not dados.get("items"):
                break
            params["cursor"] = dados["cursor"]
    abertos = [normalizar(i) for i in itens[:limite] if i.get("state") not in ESTADOS_FINAIS]
    return sorted(abertos, key=lambda i: i.criado_em, reverse=True)


def detalhe(incident_id: str, customer_id: str) -> IncidenteEdr:
    """Incidente com detecções e ações de resposta oferecidas.

    Raises:
        IncidenteNaoEncontradoError: O Acronis não tem mais o incidente (410/404).
        httpx.HTTPError: Outra falha.
    """
    with _cliente() as c:
        resp = c.get(
            f"/incidents/{incident_id}",
            params={
                "customer_id": customer_id,
                "with_incident_actions": "true",
                "with_detections": "true",
                "with_detection_per_type_limit": 5,
                "analytics_source": _ANALYTICS,
            },
        )
    if resp.status_code in (404, 410):
        raise IncidenteNaoEncontradoError(incident_id)
    resp.raise_for_status()
    return normalizar(resp.json())


def atualizar_estado(incident_id: str, customer_id: str, estado: str, comentario: str) -> None:
    """Muda o estado de investigação do incidente, com comentário.

    Raises:
        ValueError: Estado fora de ``ESTADOS_PERMITIDOS``.
        IncidenteNaoEncontradoError: O incidente não existe mais.
        httpx.HTTPError: Falha ao falar com o Acronis.
    """
    if estado not in ESTADOS_PERMITIDOS:
        raise ValueError(f"estado não permitido: {estado}")
    with _cliente() as c:
        resp = c.post(
            f"/incidents/{incident_id}/investigation_state",
            params={"customer_id": customer_id, "analytics_source": _ANALYTICS},
            headers={"Idempotency-Key": str(uuid.uuid4())},
            json={"state": estado, "comment": comentario[:1000]},
        )
    if resp.status_code in (404, 410):
        raise IncidenteNaoEncontradoError(incident_id)
    resp.raise_for_status()
    log.info("acronis_edr.estado_atualizado", incidente=incident_id, estado=estado)


def atualizar_estado_lote(pares: list[tuple[str, str]], estado: str, comentario: str) -> dict[str, str]:
    """Muda o estado de vários incidentes numa chamada só (``POST /incidents/investigation_state``).

    Args:
        pares: ``(incident_id, customer_id)`` de cada incidente.
        estado: Um de ``ESTADOS_PERMITIDOS``.
        comentario: Mesmo comentário para todos.

    Returns:
        incident_id → ``""`` se deu certo, ou a mensagem de erro do item.

    Raises:
        ValueError: Estado fora de ``ESTADOS_PERMITIDOS``.
        httpx.HTTPError: Falha da chamada inteira.
    """
    if estado not in ESTADOS_PERMITIDOS:
        raise ValueError(f"estado não permitido: {estado}")
    if not pares:
        return {}
    with _cliente() as c:
        resp = c.post(
            "/incidents/investigation_state",
            params={"analytics_source": _ANALYTICS},
            headers={"Idempotency-Key": str(uuid.uuid4())},
            json={
                "customer_incident_ids": [{"customer_id": cid, "incident_id": iid} for iid, cid in pares],
                "update": {"state": estado, "comment": comentario[:1000]},
            },
        )
    resp.raise_for_status()
    resultado = {iid: "" for iid, _ in pares}
    for item in (resp.json() or {}).get("items") or [] if resp.status_code == 207 else []:
        alvo = item.get("item") if isinstance(item.get("item"), dict) else {}
        iid = str(alvo.get("incident_id") or "")
        if iid in resultado and int(item.get("status") or 0) >= 300:
            erro = item.get("error") if isinstance(item.get("error"), dict) else {}
            resultado[iid] = str(erro.get("message") or erro.get("code") or item.get("status"))[:300]
    log.info("acronis_edr.estado_lote", estado=estado, total=len(pares), falhas=sum(1 for v in resultado.values() if v))
    return resultado


def executar_acao(incidente: IncidenteEdr, acao: str, comentario: str) -> str:
    """Dispara uma ação de resposta permitida e oferecida para o incidente.

    Args:
        incidente: Incidente com ``acoes`` vindas do ``detalhe``.
        acao: Uma de ``ACOES_PERMITIDAS``.
        comentario: Registrado no Acronis junto com a ação.

    Returns:
        ``activity_id`` da ação (para ``status_acao``).

    Raises:
        AcaoNaoPermitidaError: Ação fora da lista ou não oferecida para o incidente.
        httpx.HTTPError: Falha ao falar com o Acronis.
    """
    if acao not in ACOES_PERMITIDAS or acao not in incidente.acoes:
        raise AcaoNaoPermitidaError(acao)
    params = {
        "action": acao,
        "customer_id": incidente.customer_id,
        "comment": comentario[:500],
        "analytics_source": _ANALYTICS,
    }
    if incidente.acoes[acao]:
        params["workload_id"] = incidente.acoes[acao]
    with _cliente() as c:
        resp = c.post(
            f"/incidents/{incidente.incident_id}/response_action",
            params=params,
            headers={"Idempotency-Key": str(uuid.uuid4())},
        )
    resp.raise_for_status()
    activity_id = str(resp.json().get("activity_id") or "")
    log.info("acronis_edr.acao_disparada", incidente=incidente.incident_id, acao=acao, activity=activity_id)
    return activity_id


def status_acao(incident_id: str, customer_id: str, activity_id: str) -> str:
    """Situação de uma ação disparada (``QUEUED``, ``STARTED``, ``SUCCEEDED``, ``FAILED``...).

    Raises:
        httpx.HTTPError: Falha ao falar com o Acronis.
    """
    with _cliente() as c:
        resp = c.get(
            f"/incidents/{incident_id}/response_action",
            params={"activity_id": activity_id, "customer_id": customer_id, "analytics_source": _ANALYTICS},
        )
    resp.raise_for_status()
    return str(resp.json().get("status") or "")
