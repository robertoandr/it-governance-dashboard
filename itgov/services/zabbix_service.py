"""Serviço Zabbix: JSON-RPC via SyncAPIClient + cache de token de sessão."""

from __future__ import annotations

import threading
from typing import Any
from urllib.parse import ParseResult, urlparse

import httpx
import structlog

from itgov.models.zabbix import (
    AcknowledgeRequest,
    ZabbixHost,
    ZabbixHostSummary,
    ZabbixProblem,
    ZabbixSeverity,
)
from itgov.utils.http_client import SyncAPIClient

log = structlog.get_logger(__name__)

_RPC_ID = 1  # stateless — id fixo é suficiente para JSON-RPC 2.0 síncrono
ZABBIX_JSONRPC_PATH: str = "/api_jsonrpc.php"


def _build_safe_netloc(parsed: ParseResult) -> str:
    """Constrói netloc sem userinfo, preservando porta se presente.

    Args:
        parsed: Resultado de ``urlparse`` com scheme, netloc e hostname já
            validados.

    Returns:
        String ``hostname`` ou ``hostname:port`` (sem credenciais).
    """
    host = parsed.hostname or ""
    return f"{host}:{parsed.port}" if parsed.port else host


def _normalize_zabbix_base_url(url: str) -> str:
    """Normaliza a URL base do Zabbix removendo o sufixo do endpoint JSON-RPC.

    Garante que o base_url passado ao cliente HTTP não inclua o path
    ``/api_jsonrpc.php``, evitando a duplicação de path quando ``_rpc()``
    adiciona ``ZABBIX_JSONRPC_PATH`` na requisição.

    Args:
        url: URL base do servidor Zabbix. Pode ou não terminar com
            ``/api_jsonrpc.php`` ou barra final.

    Returns:
        URL normalizada sem trailing slash nem sufixo JSON-RPC.

    Raises:
        ValueError: Se ``url`` for vazia, o scheme não for ``http``/``https``,
            ou a URL não tiver host válido.

    Examples:
        >>> _normalize_zabbix_base_url("https://zbx.corp.com/api_jsonrpc.php")
        'https://zbx.corp.com'
        >>> _normalize_zabbix_base_url("http://localhost")
        'http://localhost'
        >>> _normalize_zabbix_base_url("http://srv/zabbix/api_jsonrpc.php")
        'http://srv/zabbix'
    """
    url = url.strip()
    if not url:
        raise ValueError("Zabbix base URL não pode ser vazia")

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Scheme inválido '{parsed.scheme}': use http ou https")
    if not parsed.netloc:
        raise ValueError(f"URL sem host válido: '{url}'")

    normalized = url.rstrip("/")
    if normalized.endswith(ZABBIX_JSONRPC_PATH):
        stripped = normalized.removesuffix(ZABBIX_JSONRPC_PATH)
        # Loga sem userinfo para evitar vazar credenciais embutidas na URL
        safe_netloc = _build_safe_netloc(parsed)
        safe_original = parsed._replace(netloc=safe_netloc).geturl()
        safe_normalized = parsed._replace(netloc=safe_netloc, path=parsed.path.removesuffix(ZABBIX_JSONRPC_PATH)).geturl().rstrip("/")
        log.warning("zabbix.base_url.normalized", original=safe_original, normalized=safe_normalized)
        return stripped

    return normalized


class ZabbixService(SyncAPIClient):
    """Cliente Zabbix JSON-RPC com cache de token e retry automático.

    O token de sessão é armazenado em memória e renovado automaticamente
    quando a API retorna erro de sessão expirada. Thread-safe via Lock.

    Args:
        url: URL base do Zabbix (ex: "https://zabbix.example.com").
        user: Usuário Zabbix para autenticação.
        password: Senha do usuário.
        timeout: Timeout HTTP em segundos (default: 15).
        max_retries: Tentativas em caso de falha 5xx (default: 3).
    """

    def __init__(
        self,
        url: str,
        user: str,
        password: str,
        timeout: float = 15.0,
        max_retries: int = 3,
    ) -> None:
        super().__init__(base_url=_normalize_zabbix_base_url(url), timeout=timeout, max_retries=max_retries)
        self._user = user
        self._password = password
        self._token: str | None = None
        self._lock = threading.Lock()

    # ── Auth ──────────────────────────────────────────────────────────────────

    def _get_token(self) -> str:
        """Retorna token de sessão existente ou autentica e obtém novo."""
        with self._lock:
            if self._token:
                return self._token
            self._token = self._login()
            return self._token

    def _login(self) -> str:
        """Autentica no Zabbix e retorna o token de sessão."""
        result = self._rpc("user.login", {"username": self._user, "password": self._password}, auth=False)
        if not isinstance(result, str) or not result:
            raise RuntimeError("Zabbix login retornou token inválido")
        log.info("zabbix_login_ok")
        return result

    def _invalidate_token(self) -> None:
        with self._lock:
            self._token = None

    # ── JSON-RPC Core ─────────────────────────────────────────────────────────

    def _rpc(self, method: str, params: dict[str, Any], *, auth: bool = True) -> Any:
        """Executa chamada JSON-RPC ao Zabbix.

        Args:
            method: Método Zabbix (ex: "problem.get").
            params: Parâmetros do método.
            auth: Se True, inclui token de autenticação.

        Returns:
            Conteúdo de result no corpo JSON-RPC.

        Raises:
            RuntimeError: Se a API retornar erro.
            httpx.HTTPStatusError: Para erros HTTP 4xx/5xx.
        """
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": _RPC_ID,
        }
        if auth:
            payload["auth"] = self._get_token()

        try:
            resp = self.post(ZABBIX_JSONRPC_PATH, json=payload)
        except httpx.HTTPStatusError as exc:
            log.error("zabbix_http_error", method=method, status=exc.response.status_code)
            raise

        body = resp.json()

        if "error" in body:
            err = body["error"]
            err_data = str(err.get("data", "")).lower()
            # Token expirado ou sessão inválida → renova e retenta UMA vez
            if auth and ("re-login" in err_data or "session" in err_data):
                log.warning("zabbix_session_expired", method=method)
                self._invalidate_token()
                payload["auth"] = self._get_token()
                resp = self.post(ZABBIX_JSONRPC_PATH, json=payload)
                body = resp.json()
                if "error" in body:
                    raise RuntimeError(f"Zabbix API error: {body['error']}")
            else:
                raise RuntimeError(f"Zabbix API error: {err}")

        return body.get("result")

    # ── Public Methods ────────────────────────────────────────────────────────

    def get_hosts(self) -> list[ZabbixHost]:
        """Retorna hosts monitorados com status de disponibilidade.

        Returns:
            Lista de ZabbixHost ordenada por disponibilidade (down primeiro).
        """
        raw = self._rpc(
            "host.get",
            {
                "output": ["hostid", "host", "name", "status"],
                "selectInterfaces": ["available"],
                "monitored_hosts": True,
                "filter": {"status": 0},
            },
        )
        hosts: list[ZabbixHost] = []
        for h in raw or []:
            ifaces = h.get("interfaces", [])
            available = max((int(i.get("available", 0)) for i in ifaces), default=0)
            hosts.append(
                ZabbixHost(
                    hostid=h["hostid"],
                    host=h["host"],
                    name=h.get("name", h["host"]),
                    status=int(h.get("status", 0)),
                    available=available,
                )
            )
        return sorted(hosts, key=lambda h: h.available)

    def get_host_summary(self) -> ZabbixHostSummary:
        """Retorna contagem de hosts por disponibilidade."""
        hosts = self.get_hosts()
        total = len(hosts)
        up = sum(1 for h in hosts if h.available == 1)
        down = sum(1 for h in hosts if h.available == 2)
        unknown = total - up - down
        up_pct = round((up / total) * 100, 1) if total else 0.0
        return ZabbixHostSummary(total=total, up=up, down=down, unknown=unknown, up_pct=up_pct)

    def get_problems(self, limit: int = 50) -> list[ZabbixProblem]:
        """Retorna problemas ativos recentes.

        Args:
            limit: Máximo de problemas a retornar (default: 50).

        Returns:
            Lista de ZabbixProblem ordenada por severidade decrescente.
        """
        raw_problems = (
            self._rpc(
                "problem.get",
                {
                    "output": ["eventid", "objectid", "name", "severity", "clock", "r_clock", "acknowledged"],
                    "sortfield": "eventid",
                    "sortorder": "DESC",
                    "limit": limit,
                    "recent": True,
                },
            )
            or []
        )

        if not raw_problems:
            return []

        # Busca hosts dos triggers em lote
        trigger_ids = list({p["objectid"] for p in raw_problems})
        triggers = (
            self._rpc(
                "trigger.get",
                {
                    "output": ["triggerid"],
                    "selectHosts": ["name"],
                    "triggerids": trigger_ids,
                },
            )
            or []
        )
        trigger_hosts: dict[str, list[str]] = {
            t["triggerid"]: [h["name"] for h in t.get("hosts", [])] for t in triggers
        }

        problems: list[ZabbixProblem] = []
        for p in raw_problems:
            problems.append(
                ZabbixProblem(
                    eventid=p["eventid"],
                    objectid=p["objectid"],
                    name=p["name"],
                    severity=int(p.get("severity", 0)),
                    acknowledged=p.get("acknowledged", "0"),
                    clock=p["clock"],
                    r_clock=p.get("r_clock") or None,
                    hosts=trigger_hosts.get(p["objectid"], []),
                )
            )

        return sorted(problems, key=lambda p: p.severity, reverse=True)

    def get_severity_distribution(self) -> dict[str, int]:
        """Retorna contagem de problemas por severidade.

        Returns:
            Dict com labels de severidade como chave e contagem como valor.
        """
        problems = self.get_problems()
        dist: dict[str, int] = {s.label: 0 for s in ZabbixSeverity}
        for p in problems:
            dist[p.severity.label] += 1
        return dist

    def acknowledge_event(self, req: AcknowledgeRequest) -> bool:
        """Reconhece um evento no Zabbix.

        Args:
            req: Dados do reconhecimento (eventid + mensagem).

        Returns:
            True se o reconhecimento foi aceito.

        Raises:
            RuntimeError: Se a API retornar erro.
        """
        result = self._rpc(
            "event.acknowledge",
            {
                "eventids": [req.eventid],
                "action": 6,  # 2 (acknowledge) + 4 (add message)
                "message": req.message,
            },
        )
        log.info("zabbix_ack_ok", eventid=req.eventid)
        return bool(result and result.get("eventids"))
