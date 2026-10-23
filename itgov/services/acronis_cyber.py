"""Investigação ao vivo de incidentes no Acronis Cyber Protect.

Diferente de ``acronis_backup`` (que lê KPIs já coletados no InfluxDB), este
módulo consulta a API Acronis **em tempo real** para montar o detalhe e a linha
do tempo de um incidente — a visão que o agente Cyber usa para investigar.

Usa uma conta de serviço dedicada com papéis *master* + *Cyber* (Cyber Protect).
As credenciais vêm de variáveis de ambiente, preferindo as dedicadas e caindo
para as do coletor Acronis quando as dedicadas não estão definidas:

- ``ACRONIS_BASE_URL`` (ex.: ``https://cyber.opustech.com.br``)
- ``ACRONIS_CYBER_CLIENT_ID`` / ``ACRONIS_CYBER_CLIENT_SECRET`` (dedicadas)
- ``ACRONIS_CLIENT_ID`` / ``ACRONIS_CLIENT_SECRET`` (fallback)

Enquanto as credenciais não existem, ``configurado()`` devolve ``False`` e as
consultas degradam para listas vazias — a página continua funcionando com os
dados do InfluxDB.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx
import structlog
from pydantic import BaseModel

log = structlog.get_logger(__name__)

_ALERTS_PATH = "/api/alert_manager/v1/alerts"
_AUTH_PATH = "/api/2/idp/token"
_TIMEOUT = 30

# Tipos de alerta que representam incidentes de SEGURANÇA — a investigação só
# mostra estes. O resto do alert_manager é ruído de monitoramento (software
# instalado, hardware, patches, reboot) e não entra na tela de incidentes.
_TIPOS_SEGURANCA_EXATOS = frozenset({"EDRIncidentDetected", "MaliciousUrlDetected"})
# Além dos exatos, qualquer tipo cujo nome contenha um destes termos.
_TERMOS_SEGURANCA = ("failedlogin", "ransomware", "activeprotection", "threat", "malware")

# Peso de severidade para escolher a "severidade do incidente" (a maior entre
# seus alertas) e ordenar a lista. Rótulos desconhecidos caem em 0.
_PESO_SEV: dict[str, int] = {
    "critical": 5,
    "error": 4,
    "high": 4,
    "warning": 3,
    "medium": 3,
    "low": 2,
    "info": 1,
    "informational": 1,
}


class AlertaCyber(BaseModel):
    """Um alerta individual do Acronis, normalizado para a investigação.

    Attributes:
        id: Identificador do alerta.
        incident_id: Incidente ao qual o alerta pertence (``id`` se não houver).
        tipo: Tipo do alerta (ex.: ``EDRIncidentDetected``).
        severidade: Rótulo de severidade (ex.: ``critical``).
        recurso: Nome da máquina afetada, quando disponível.
        criado_em: Timestamp ISO do alerta, ou ``None``.
        mitigado: Se o alerta já foi mitigado.
    """

    id: str
    incident_id: str
    tipo: str
    severidade: str
    recurso: str
    criado_em: str | None = None
    mitigado: bool = False


class IncidenteCyber(BaseModel):
    """Um incidente agregado (um ou mais alertas) com sua linha do tempo.

    Attributes:
        incident_id: Identificador do incidente.
        tipo: Tipo do alerta mais grave do incidente.
        severidade: Maior severidade entre os alertas.
        recurso: Máquina afetada.
        total_alertas: Quantos alertas compõem o incidente.
        mitigado: ``True`` se há ao menos um alerta mitigado.
        primeiro_em: Timestamp do alerta mais antigo.
        ultimo_em: Timestamp do alerta mais recente.
        timeline: Alertas do incidente, do mais antigo ao mais recente.
    """

    incident_id: str
    tipo: str
    severidade: str
    recurso: str
    total_alertas: int
    mitigado: bool
    primeiro_em: str | None
    ultimo_em: str | None
    timeline: list[AlertaCyber]


def _creds() -> tuple[str, str]:
    """Credenciais da conta de serviço, com fallback para as do coletor."""
    client_id = os.getenv("ACRONIS_CYBER_CLIENT_ID") or os.getenv("ACRONIS_CLIENT_ID") or ""
    secret = os.getenv("ACRONIS_CYBER_CLIENT_SECRET") or os.getenv("ACRONIS_CLIENT_SECRET") or ""
    return client_id, secret


def configurado() -> bool:
    """Há base e credenciais suficientes para a investigação ao vivo?"""
    client_id, secret = _creds()
    return bool(os.getenv("ACRONIS_BASE_URL") and client_id and secret)


def peso_severidade(severidade: str | None) -> int:
    """Peso numérico de uma severidade (maior = mais grave); 0 se desconhecida."""
    if not severidade:
        return 0
    return _PESO_SEV.get(str(severidade).strip().lower(), 0)


def eh_incidente_seguranca(tipo: str | None) -> bool:
    """O tipo de alerta representa um incidente de segurança (não monitoramento)?

    Args:
        tipo: Valor de ``type`` do alerta Acronis.

    Returns:
        ``True`` para EDR, URL maliciosa, falha de login, ransomware e afins.
    """
    if not tipo:
        return False
    if tipo in _TIPOS_SEGURANCA_EXATOS:
        return True
    baixo = tipo.lower()
    return any(termo in baixo for termo in _TERMOS_SEGURANCA)


def normalizar_alerta(raw: dict[str, Any]) -> AlertaCyber:
    """Converte um item cru de ``/alerts`` em ``AlertaCyber``.

    Lê as chaves de forma defensiva (a API varia entre tipos de alerta).

    Args:
        raw: Item de ``/api/alert_manager/v1/alerts``.

    Returns:
        O alerta normalizado.
    """
    details = raw.get("details") or {}
    alerta_id = str(raw.get("id") or "")
    incident_id = str(details.get("incidentId") or alerta_id)
    recurso = str(details.get("resourceName") or details.get("resourceId") or raw.get("resourceName") or "—")
    criado = raw.get("createdAt") or raw.get("created_at") or raw.get("receivedAt")
    return AlertaCyber(
        id=alerta_id,
        incident_id=incident_id,
        tipo=str(raw.get("type") or "—"),
        severidade=str(raw.get("severity") or "—"),
        recurso=recurso,
        criado_em=str(criado) if criado else None,
        mitigado=str(details.get("isMitigated", "")).lower() == "true",
    )


def agrupar_incidentes(alertas: list[AlertaCyber]) -> list[IncidenteCyber]:
    """Agrupa alertas por ``incident_id`` e monta a linha do tempo de cada um.

    A severidade e o tipo do incidente vêm do alerta mais grave; ``mitigado`` é
    ``True`` se qualquer alerta do incidente está mitigado. A lista resultante é
    ordenada por severidade (desc) e, em empate, pelo alerta mais recente.

    Args:
        alertas: Alertas já normalizados.

    Returns:
        Incidentes agregados, do mais grave ao menos grave.
    """
    por_id: dict[str, list[AlertaCyber]] = {}
    for a in alertas:
        por_id.setdefault(a.incident_id, []).append(a)

    incidentes: list[IncidenteCyber] = []
    for incident_id, grupo in por_id.items():
        timeline = sorted(grupo, key=lambda x: x.criado_em or "")
        mais_grave = max(grupo, key=lambda x: peso_severidade(x.severidade))
        incidentes.append(
            IncidenteCyber(
                incident_id=incident_id,
                tipo=mais_grave.tipo,
                severidade=mais_grave.severidade,
                recurso=mais_grave.recurso,
                total_alertas=len(grupo),
                mitigado=any(x.mitigado for x in grupo),
                primeiro_em=timeline[0].criado_em,
                ultimo_em=timeline[-1].criado_em,
                timeline=timeline,
            )
        )

    incidentes.sort(key=lambda i: (peso_severidade(i.severidade), i.ultimo_em or ""), reverse=True)
    return incidentes


async def _buscar_alertas(limit: int) -> list[dict[str, Any]]:
    """Busca os alertas ativos na API Acronis (paginado)."""
    base = os.getenv("ACRONIS_BASE_URL", "").rstrip("/")
    client_id, secret = _creds()
    itens: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
        resp = await c.post(
            f"{base}{_AUTH_PATH}",
            auth=(client_id, secret),
            data={"grant_type": "client_credentials"},
        )
        resp.raise_for_status()
        c.headers["Authorization"] = f"Bearer {resp.json()['access_token']}"
        params: dict[str, Any] = {"limit": min(limit, 1000)}
        while True:
            pagina = await c.get(f"{base}{_ALERTS_PATH}", params=params)
            pagina.raise_for_status()
            dados = pagina.json()
            itens.extend(dados.get("items", []))
            depois = (dados.get("paging") or {}).get("cursors", {}).get("after")
            if not depois or not dados.get("items") or len(itens) >= limit:
                break
            params["after"] = depois
    return itens[:limit]


def _coletar(limit: int) -> list[IncidenteCyber]:
    """Executa a busca async e agrupa, degradando para ``[]`` em falha."""
    if not configurado():
        return []
    try:
        alertas = asyncio.run(_buscar_alertas(limit))
    except (httpx.HTTPError, KeyError, ValueError, RuntimeError) as exc:
        log.warning("acronis_cyber.falhou", erro=type(exc).__name__, detalhe=str(exc)[:200])
        return []
    seguranca = [a for a in alertas if eh_incidente_seguranca(a.get("type"))]
    return agrupar_incidentes([normalizar_alerta(a) for a in seguranca])


def listar_incidentes(limit: int = 2000) -> list[IncidenteCyber]:
    """Lista os incidentes ativos, do mais grave ao menos grave.

    Args:
        limit: Teto de alertas a buscar antes de agrupar.

    Returns:
        Incidentes agregados; lista vazia se não configurado ou em falha.
    """
    return _coletar(limit)


def detalhe_incidente(incident_id: str, limit: int = 2000) -> IncidenteCyber | None:
    """Retorna um incidente específico com sua linha do tempo.

    Args:
        incident_id: Identificador do incidente.
        limit: Teto de alertas a buscar.

    Returns:
        O incidente, ou ``None`` se não encontrado / não configurado.
    """
    for inc in _coletar(limit):
        if inc.incident_id == incident_id:
            return inc
    return None
