"""Base HTTP client: httpx.Client + tenacity retry + structlog.

Usado como classe base pelos serviços de integração (Zabbix, Zendesk, etc.).
Sync-first — compatível com Flask WSGI e threading._refresh_loop.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import structlog
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

log = structlog.get_logger(__name__)


def _is_retryable(exc: BaseException) -> bool:
    """Retenta apenas erros de transporte e 5xx (não 4xx — são bugs de cliente)."""
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return False


def _log_retry(state: RetryCallState) -> None:
    """Callback chamado antes de cada retry."""
    exc = state.outcome.exception() if state.outcome else None
    log.warning(
        "http_retry",
        attempt=state.attempt_number,
        exc_type=type(exc).__name__ if exc else None,
    )


class SyncAPIClient:
    """Base class para clientes HTTP síncronos.

    Fornece:
        - httpx.Client com timeouts configuráveis
        - Retry com exponential backoff (só 5xx + TransportError)
        - Structured logging com correlation_id por request
        - Context manager para gerenciamento do client

    Args:
        base_url: URL base da API (ex: "https://api.example.com").
        timeout: Timeout total em segundos (default: 10.0).
        max_retries: Número máximo de tentativas (default: 3).
        headers: Headers padrão enviados em todas as requests.
    """

    def __init__(
        self,
        base_url: str,
        timeout: float = 10.0,
        max_retries: int = 3,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url,
            timeout=httpx.Timeout(timeout),
            headers=headers or {},
            follow_redirects=False,
        )
        self._max_retries = max_retries
        self._log = log.bind(client=self.__class__.__name__, base_url=base_url)

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Executa HTTP request com retry e logging.

        Cada chamada recebe um correlation_id único para rastreamento.
        Retenta automaticamente em erros 5xx e falhas de transporte.

        Args:
            method: Método HTTP ("GET", "POST", etc.).
            url: Path relativo à base_url.
            **kwargs: Passado diretamente ao httpx.Client.request().

        Returns:
            httpx.Response com status 2xx.

        Raises:
            httpx.HTTPStatusError: Para respostas 4xx (não retentadas).
            httpx.TransportError: Se todas as tentativas falharem.
        """
        correlation_id = str(uuid.uuid4())[:8]
        bound_log = self._log.bind(method=method, url=url, correlation_id=correlation_id)

        @retry(
            stop=stop_after_attempt(self._max_retries),
            wait=wait_exponential(multiplier=1, min=1, max=10),
            retry=retry_if_exception(_is_retryable),
            before_sleep=_log_retry,
            reraise=True,
        )
        def _execute() -> httpx.Response:
            bound_log.info("http_request")
            response = self._client.request(method, url, **kwargs)
            response.raise_for_status()
            bound_log.info("http_response", status=response.status_code)
            return response

        return _execute()

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        """Atalho para GET."""
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        """Atalho para POST."""
        return self.request("POST", url, **kwargs)

    def close(self) -> None:
        """Fecha o httpx.Client e libera conexões."""
        self._client.close()

    def __enter__(self) -> SyncAPIClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
