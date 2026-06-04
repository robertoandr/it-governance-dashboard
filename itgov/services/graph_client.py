from __future__ import annotations

import time
from collections.abc import AsyncIterator
from urllib.parse import urlparse

import httpx
import structlog
from opentelemetry.trace import StatusCode
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

import config
from itgov.observability import get_meter, get_tracer

log = structlog.get_logger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
LOGIN_URL = "https://login.microsoftonline.com"

_SP_SELECT = "id,displayName,appId,servicePrincipalType,signInActivity,passwordCredentials,keyCredentials,appRoles"
_SP_DELTA_URL = f"{GRAPH_BASE}/servicePrincipals/delta?$select={_SP_SELECT}&$top=999"

# ── OTel Metric instruments (created lazily; SDK deduplicates by name) ────────
_METER_NAME = "itgov.graph"


def _requests_counter():
    return get_meter(_METER_NAME).create_counter(
        "m365_graph_requests_total",
        unit="1",
        description="Total Graph API requests by tenant, endpoint and status_code.",
    )


def _errors_counter():
    return get_meter(_METER_NAME).create_counter(
        "m365_graph_errors_total",
        unit="1",
        description="Total Graph API errors by tenant and error_type.",
    )


def _request_duration_histogram():
    return get_meter(_METER_NAME).create_histogram(
        "m365_graph_request_duration_seconds",
        unit="s",
        description="Graph API request latency distribution by tenant and endpoint.",
    )


class GraphAuthError(Exception):
    pass


class GraphRateLimitError(Exception):
    def __init__(self, retry_after: int = 60):
        self.retry_after = retry_after
        super().__init__(f"Rate limited; retry after {retry_after}s")


def _get_credentials() -> tuple[str, str, str]:
    """Read credentials from config — never log the returned values."""
    return (
        config.GRAPH_TENANT_ID,
        config.GRAPH_CLIENT_ID,
        config.GRAPH_CLIENT_SECRET,
    )


def _url_path(url: str) -> str:
    """Return the path component of a URL (strips query string)."""
    return urlparse(url).path


def _url_host(url: str) -> str:
    return urlparse(url).hostname or "graph.microsoft.com"


async def _fetch_token(client: httpx.AsyncClient) -> str:
    tracer = get_tracer("itgov.graph")
    with tracer.start_as_current_span("graph.auth.token_fetch") as span:
        tenant_id, client_id, client_secret = _get_credentials()
        span.set_attribute("graph.auth.method", "client_credentials")
        span.set_attribute("graph.tenant_id", tenant_id or "")
        span.set_attribute("http.request.method", "POST")
        span.set_attribute("server.address", "login.microsoftonline.com")
        span.set_attribute("url.path", f"/{tenant_id}/oauth2/v2.0/token")

        resp = await client.post(
            f"{LOGIN_URL}/{tenant_id}/oauth2/v2.0/token",
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
                "scope": "https://graph.microsoft.com/.default",
            },
        )
        if resp.status_code != 200:
            exc = GraphAuthError(f"Token request failed: HTTP {resp.status_code}")
            span.set_status(StatusCode.ERROR, f"HTTP {resp.status_code}")
            span.set_attribute("graph.error.type", "auth")
            span.set_attribute("http.response.status_code", resp.status_code)
            span.record_exception(exc)
            _errors_counter().add(1, {"tenant": tenant_id or "", "error_type": "auth"})
            log.error("graph.token.failed", status_code=resp.status_code)
            raise exc
        span.set_attribute("http.response.status_code", 200)
        return resp.json()["access_token"]


@retry(
    retry=retry_if_exception_type(httpx.TransportError),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    reraise=True,
)
async def _get(client: httpx.AsyncClient, url: str, token: str) -> dict:
    resp = await client.get(url, headers={"Authorization": f"Bearer {token}"})
    if resp.status_code == 429:
        retry_after = int(resp.headers.get("Retry-After", "60"))
        raise GraphRateLimitError(retry_after)
    resp.raise_for_status()
    return resp.json()


class GraphClient:
    """Async Microsoft Graph API client with delta query support."""

    def __init__(self, timeout: float = 60.0) -> None:
        self._timeout = timeout
        self.last_delta_link: str | None = None

    async def get_service_principals_delta(
        self,
        tenant_id: str,
        delta_link: str | None = None,
    ) -> AsyncIterator[dict]:
        """Yield Service Principals using Graph delta queries.

        Args:
            tenant_id: Microsoft tenant ID (used for logging only).
            delta_link: Previous deltaLink for incremental sync.
                        None triggers a full initial scan.

        Yields:
            Individual SP objects from the Graph API response.

        After iteration completes, ``self.last_delta_link`` holds the new
        deltaLink to persist for the next run.
        """
        self.last_delta_link = None
        start_url = delta_link if delta_link is not None else _SP_DELTA_URL
        mode = "delta" if delta_link is not None else "full"
        log.info("graph.sp_delta.start", tenant_id=tenant_id, mode=mode)

        tracer = get_tracer("itgov.graph")

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            token = await _fetch_token(client)
            url: str | None = start_url
            page = 0

            while url:
                page += 1
                endpoint = _url_path(url)
                t0 = time.monotonic()

                with tracer.start_as_current_span("graph.api.get") as get_span:
                    get_span.set_attribute("http.request.method", "GET")
                    get_span.set_attribute("url.path", endpoint)
                    get_span.set_attribute("server.address", _url_host(url))
                    get_span.set_attribute("graph.page_number", page)

                    try:
                        data = await _get(client, url, token)
                    except GraphRateLimitError as exc:
                        get_span.set_status(StatusCode.ERROR, "rate_limited")
                        get_span.set_attribute("graph.error.type", "rate_limit")
                        get_span.set_attribute("graph.retry_after_seconds", exc.retry_after)
                        _errors_counter().add(1, {"tenant": tenant_id, "error_type": "rate_limit"})
                        _requests_counter().add(
                            1,
                            {"tenant": tenant_id, "endpoint": endpoint, "status_code": "429"},
                        )
                        raise
                    except httpx.TransportError as exc:
                        get_span.record_exception(exc)
                        get_span.set_status(StatusCode.ERROR, str(exc))
                        _errors_counter().add(1, {"tenant": tenant_id, "error_type": "network"})
                        _requests_counter().add(
                            1,
                            {"tenant": tenant_id, "endpoint": endpoint, "status_code": "0"},
                        )
                        raise
                    except Exception as exc:
                        get_span.record_exception(exc)
                        get_span.set_status(StatusCode.ERROR, str(exc))
                        _errors_counter().add(1, {"tenant": tenant_id, "error_type": "other"})
                        _requests_counter().add(
                            1,
                            {"tenant": tenant_id, "endpoint": endpoint, "status_code": "0"},
                        )
                        raise

                    get_span.set_attribute("http.response.status_code", 200)
                    get_span.set_attribute("graph.has_next_page", bool(data.get("@odata.nextLink")))
                    get_span.set_attribute("graph.delta_link_present", bool(data.get("@odata.deltaLink")))

                duration = time.monotonic() - t0
                _requests_counter().add(1, {"tenant": tenant_id, "endpoint": endpoint, "status_code": "200"})
                _request_duration_histogram().record(duration, {"tenant": tenant_id, "endpoint": endpoint})

                items = data.get("value", [])
                log.debug("graph.sp_delta.page", page=page, count=len(items))

                for sp in items:
                    yield sp

                next_link = data.get("@odata.nextLink")
                delta_link_response = data.get("@odata.deltaLink")

                if delta_link_response:
                    self.last_delta_link = delta_link_response
                    url = None
                elif next_link:
                    url = next_link
                else:
                    url = None

        log.info(
            "graph.sp_delta.done",
            tenant_id=tenant_id,
            mode=mode,
            pages=page,
            delta_obtained=self.last_delta_link is not None,
        )


# Module-level convenience for simple full-scan usage (backwards compat)
async def iter_service_principals(timeout: float = 60.0) -> AsyncIterator[dict]:
    """Yield all Service Principals via delta full-scan (no persistence)."""
    client = GraphClient(timeout=timeout)
    tenant_id = config.GRAPH_TENANT_ID or "unknown"
    async for sp in client.get_service_principals_delta(tenant_id):
        yield sp
