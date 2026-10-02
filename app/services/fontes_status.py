"""Situação das fontes de dados da dashboard (online / offline / não configurada).

Cada fonte é conferida com uma chamada leve e autenticada, em paralelo e com
prazo curto. O resultado fica em cache com atualização em segundo plano, para
a Visão Geral nunca esperar por uma API externa.

Diferente de ``HealthChecker.check_all`` (readiness do Kubernetes), aqui entram
os SaaS externos: a queda de um deles não pode derrubar o app, só aparecer
vermelha no painel.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

import httpx
import structlog
from pydantic import BaseModel

from app.config import get_settings
from app.services.health_checker import HealthChecker
from itgov.utils.cache_swr import CacheSWR

log = structlog.get_logger(__name__)

PRAZO_S = 5.0
_TZ = ZoneInfo("America/Sao_Paulo")

Estado = Literal["online", "offline", "nao_configurada"]


class FonteStatus(BaseModel):
    """Situação de uma fonte de dados.

    Attributes:
        id: Identificador curto (ex.: ``"zendesk"``).
        nome: Nome exibido.
        estado: ``online``, ``offline`` ou ``nao_configurada``.
        detalhe: Motivo do offline (sem credenciais), versão etc.
        latencia_ms: Tempo da checagem, quando houve.
        verificado_em: Horário de Brasília da checagem (``dd/mm HH:MM``).
    """

    id: str
    nome: str
    estado: Estado
    detalhe: str = ""
    latencia_ms: int | None = None
    verificado_em: str = ""


Checagem = Callable[[httpx.AsyncClient], Awaitable[str]]


class FonteOfflineError(Exception):
    """A fonte respondeu, mas não como esperado (credencial inválida, HTTP de erro)."""


def _env(*nomes: str) -> str:
    """Primeira variável de ambiente definida entre ``nomes``."""
    for nome in nomes:
        if valor := os.getenv(nome, "").strip():
            return valor
    return ""


def _ligado(nome: str) -> bool:
    return os.getenv(nome, "").strip().lower() in ("1", "true", "yes", "on")


def _exigir(resp: httpx.Response, ok: Callable[[dict], bool] | None = None) -> dict:
    """Valida a resposta; levanta ``FonteOfflineError`` só com o código HTTP."""
    if resp.status_code != 200:
        raise FonteOfflineError(f"HTTP {resp.status_code}")
    corpo = resp.json() if resp.content else {}
    if ok is not None and not ok(corpo):
        raise FonteOfflineError("credencial recusada")
    return corpo


# ── checagens ───────────────────────────────────────────────────────────────


async def _influxdb(_c: httpx.AsyncClient) -> str:
    r = await HealthChecker().check_influxdb()
    if not r.ok:
        raise FonteOfflineError(r.error or "sem resposta")
    return ""


async def _zabbix(_c: httpx.AsyncClient) -> str:
    r = await HealthChecker().check_zabbix()
    if not r.ok:
        raise FonteOfflineError(r.error or "sem resposta")
    return f"versão {(r.extra or {}).get('version', '?')}"


async def _zendesk(c: httpx.AsyncClient) -> str:
    sub = _env("ZENDESK_SUBDOMAIN")
    auth = (f"{_env('ZENDESK_EMAIL')}/token", _env("ZENDESK_API_TOKEN"))
    resp = await c.get(f"https://{sub}.zendesk.com/api/v2/users/me.json", auth=auth)
    # Credencial inválida devolve 200 com usuário anônimo (id nulo).
    _exigir(resp, lambda d: (d.get("user") or {}).get("id") is not None)
    return ""


async def _m365(c: httpx.AsyncClient) -> str:
    tenant = _env("MSAL_TENANT_ID", "AZURE_TENANT_ID")
    resp = await c.post(
        f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
        data={
            "client_id": _env("MSAL_CLIENT_ID", "AZURE_CLIENT_ID"),
            "client_secret": _env("MSAL_CLIENT_SECRET", "AZURE_CLIENT_SECRET"),
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        },
    )
    _exigir(resp, lambda d: bool(d.get("access_token")))
    return ""


async def _clickup(c: httpx.AsyncClient) -> str:
    resp = await c.get("https://api.clickup.com/api/v2/user", headers={"Authorization": _env("CLICKUP_TOKEN")})
    _exigir(resp)
    return ""


async def _acronis(c: httpx.AsyncClient) -> str:
    base = _env("ACRONIS_BASE_URL").rstrip("/")
    resp = await c.post(
        f"{base}/api/2/idp/token",
        auth=(_env("ACRONIS_CLIENT_ID"), _env("ACRONIS_CLIENT_SECRET")),
        data={"grant_type": "client_credentials"},
    )
    _exigir(resp, lambda d: bool(d.get("access_token")))
    return ""


async def _github(c: httpx.AsyncClient) -> str:
    resp = await c.get(
        "https://api.github.com/rate_limit",
        headers={
            "Authorization": f"Bearer {_env('GITHUB_TOKEN', 'GITHUB__TOKEN')}",
            "Accept": "application/vnd.github+json",
        },
    )
    _exigir(resp)
    return ""


def _fontes() -> list[tuple[str, str, bool, Checagem]]:
    """(id, nome, configurada, checagem) de cada fonte, na ordem de exibição."""
    s = get_settings()
    return [
        ("influxdb", "InfluxDB", s.influx.enabled, _influxdb),
        ("zabbix", "Zabbix", s.zabbix.enabled, _zabbix),
        ("zendesk", "Zendesk", bool(_env("ZENDESK_SUBDOMAIN") and _env("ZENDESK_API_TOKEN")), _zendesk),
        (
            "m365",
            "Microsoft 365",
            bool(_env("MSAL_CLIENT_ID", "AZURE_CLIENT_ID") and _env("MSAL_CLIENT_SECRET", "AZURE_CLIENT_SECRET")),
            _m365,
        ),
        ("clickup", "ClickUp", bool(_env("CLICKUP_TOKEN")), _clickup),
        ("acronis", "Acronis", bool(_env("ACRONIS_BASE_URL") and _env("ACRONIS_CLIENT_ID")), _acronis),
        (
            "github",
            "GitHub",
            (_ligado("GITHUB__ENABLED") or s.github.enabled) and bool(_env("GITHUB_TOKEN", "GITHUB__TOKEN")),
            _github,
        ),
    ]


async def _checar(
    cliente: httpx.AsyncClient, fid: str, nome: str, configurada: bool, checagem: Checagem
) -> FonteStatus:
    agora = datetime.now(_TZ).strftime("%d/%m %H:%M")
    if not configurada:
        return FonteStatus(
            id=fid, nome=nome, estado="nao_configurada", detalhe="credenciais ausentes", verificado_em=agora
        )
    t0 = time.monotonic()
    try:
        detalhe = await asyncio.wait_for(checagem(cliente), PRAZO_S)
        estado: Estado = "online"
    except FonteOfflineError as exc:
        detalhe, estado = str(exc), "offline"
    except TimeoutError:
        detalhe, estado = f"sem resposta em {PRAZO_S:.0f} s", "offline"
    except (httpx.HTTPError, ValueError) as exc:
        # Só o tipo do erro: a mensagem do httpx pode trazer a URL com dados sensíveis.
        detalhe, estado = type(exc).__name__, "offline"
    latencia = int((time.monotonic() - t0) * 1000)
    if estado == "offline":
        log.warning("fonte.offline", fonte=fid, detalhe=detalhe)
    return FonteStatus(id=fid, nome=nome, estado=estado, detalhe=detalhe, latencia_ms=latencia, verificado_em=agora)


async def checar_fontes() -> list[FonteStatus]:
    """Confere todas as fontes em paralelo (sem cache).

    Returns:
        Uma ``FonteStatus`` por fonte, na ordem de exibição.
    """
    async with httpx.AsyncClient(timeout=PRAZO_S) as cliente:
        return list(await asyncio.gather(*(_checar(cliente, *f) for f in _fontes())))


def _carregar() -> list[FonteStatus]:
    return asyncio.run(checar_fontes())


_cache: CacheSWR[list[FonteStatus]] = CacheSWR("fontes.status", ttl=120)


def status_fontes() -> list[FonteStatus]:
    """Situação das fontes com cache de 2 min atualizado em segundo plano."""
    return _cache.get(_carregar)


def atualizar_agora() -> list[FonteStatus]:
    """Confere as fontes agora, sem esperar o cache vencer (botão "Atualizar")."""
    return _cache.atualizar_agora(_carregar)


def aquecer() -> None:
    """Faz a primeira checagem em segundo plano (subida do worker)."""
    _cache.aquecer(_carregar)
