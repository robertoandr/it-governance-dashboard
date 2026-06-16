"""Cliente Microsoft Graph para dados de adoção de MFA.

Busca usuários e detalhes de registro de autenticação (isMfaRegistered),
com paginação @odata.nextLink e tratamento de throttling 429.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator

import httpx
import structlog
from opentelemetry.trace import StatusCode

from itgov.observability import get_meter, get_tracer
from itgov.services.graph_client import GraphRateLimitError, _fetch_token, _get, _url_host, _url_path

log = structlog.get_logger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

_USER_SELECT = "id,userPrincipalName,displayName,accountEnabled"
_USERS_URL = f"{GRAPH_BASE}/users?$select={_USER_SELECT}&$top=999"

_MFA_DETAILS_URL = (
    f"{GRAPH_BASE}/reports/authenticationMethods/userRegistrationDetails"
    "?$select=id,userPrincipalName,isMfaRegistered&$top=999"
)

_METER_NAME = "itgov.mfa_graph"


def _requests_counter():
    return get_meter(_METER_NAME).create_counter("m365_mfa_graph_requests_total", unit="1")


def _errors_counter():
    return get_meter(_METER_NAME).create_counter("m365_mfa_graph_errors_total", unit="1")


async def _paginate(
    client: httpx.AsyncClient,
    start_url: str,
    token: str,
    tenant_id: str,
    label: str,
) -> AsyncIterator[dict]:
    """Itera páginas de um endpoint Graph com nextLink e retry em 429."""
    tracer = get_tracer("itgov.mfa_graph")
    url: str | None = start_url
    page = 0

    while url:
        page += 1
        endpoint = _url_path(url)
        t0 = time.monotonic()

        with tracer.start_as_current_span(f"graph.{label}.page") as span:
            span.set_attribute("http.request.method", "GET")
            span.set_attribute("url.path", endpoint)
            span.set_attribute("server.address", _url_host(url))
            span.set_attribute("graph.page_number", page)

            try:
                data = await _get(client, url, token)
            except GraphRateLimitError as exc:
                span.set_status(StatusCode.ERROR, "rate_limited")
                _errors_counter().add(1, {"tenant": tenant_id, "error_type": "rate_limit", "label": label})
                log.warning("mfa_graph.rate_limited", label=label, retry_after=exc.retry_after)
                await asyncio.sleep(exc.retry_after)
                # retenta a mesma página sem avançar url
                continue
            except Exception as exc:
                span.record_exception(exc)
                span.set_status(StatusCode.ERROR, str(exc))
                _errors_counter().add(1, {"tenant": tenant_id, "error_type": "other", "label": label})
                raise

            duration = time.monotonic() - t0
            _requests_counter().add(1, {"tenant": tenant_id, "endpoint": endpoint, "status_code": "200"})
            log.debug(
                "mfa_graph.page",
                label=label,
                page=page,
                count=len(data.get("value", [])),
                duration_s=round(duration, 2),
            )

        for item in data.get("value", []):
            yield item

        url = data.get("@odata.nextLink")

    log.info("mfa_graph.done", label=label, pages=page, tenant_id=tenant_id)


class MFAGraphClient:
    """Cliente assíncrono para buscar usuários e status de MFA real."""

    def __init__(self, timeout: float = 60.0) -> None:
        self._timeout = timeout

    async def get_users(self, tenant_id: str) -> list[dict]:
        """Retorna todos os usuários com id, UPN, displayName, accountEnabled."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            token = await _fetch_token(client)
            resultado = []
            async for user in _paginate(client, _USERS_URL, token, tenant_id, "users"):
                resultado.append(user)
        return resultado

    async def get_mfa_registration_details(self, tenant_id: str) -> dict[str, bool]:
        """Retorna mapa {upn → isMfaRegistered} via userRegistrationDetails.

        Usa isMfaRegistered (não isMfaCapable) conforme requisito do projeto.
        """
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            token = await _fetch_token(client)
            resultado: dict[str, bool] = {}
            async for item in _paginate(client, _MFA_DETAILS_URL, token, tenant_id, "mfa_details"):
                upn = item.get("userPrincipalName", "")
                if upn:
                    resultado[upn.lower()] = bool(item.get("isMfaRegistered", False))
        return resultado

    async def get_users_with_mfa(self, tenant_id: str) -> list[dict]:
        """Combina usuários e MFA em uma única chamada paralela.

        Retorna lista de dicts com: id, userPrincipalName, displayName,
        accountEnabled, mfa_enabled.
        """
        users_task = asyncio.create_task(self.get_users(tenant_id))
        mfa_task = asyncio.create_task(self.get_mfa_registration_details(tenant_id))
        users, mfa_map = await asyncio.gather(users_task, mfa_task)

        for user in users:
            upn = (user.get("userPrincipalName") or "").lower()
            user["mfa_enabled"] = mfa_map.get(upn, False)

        log.info("mfa_graph.combined", total=len(users), mfa_map_size=len(mfa_map), tenant_id=tenant_id)
        return users
