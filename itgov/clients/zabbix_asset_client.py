"""Cliente Zabbix para coleta de status de ativos (câmeras e servidores).

Faz exatamente 2 chamadas à API Zabbix por ciclo de poll:
  1. host.get com selectGroups → lista de hosts monitorados + grupos
  2. item.get com filter key_=icmpping → última leitura ICMP por host

Autenticação (em ordem de preferência):
  ZBX_TOKEN → token de API nativo Zabbix 7.x (Bearer header)
  ZBX_USER + ZBX_PASSWORD → login de sessão (compatível com Zabbix <7.x)

Classificação de asset_type:
  Derivada dos grupos do host via ZBX_GROUP_MAP (JSON env var).
  Formato: {"camera": ["Cameras", "Câmeras"], "server": ["Linux servers"]}
  Hosts sem correspondência recebem asset_type="other".
"""

from __future__ import annotations

import json
import os
from typing import Any, NamedTuple

import requests
import structlog

log = structlog.get_logger(__name__)

_JSONRPC_PATH = "/api_jsonrpc.php"
_RPC_ID = 1

# Mapeamento padrão: sobrescrito por ZBX_GROUP_MAP em prod
_DEFAULT_GROUP_MAP: dict[str, list[str]] = {
    "camera": ["Cameras", "Camera", "Câmeras", "Câmera"],
    "server": ["Servers", "Linux servers", "Windows servers", "Servidores"],
}


class HostInfo(NamedTuple):
    """Dados de um host Zabbix enriquecidos com asset_type derivado."""

    hostid: str
    host: str
    name: str
    asset_type: str  # camera | server | other | <customizado>


class ZabbixAssetClient:
    """Cliente Zabbix JSON-RPC para polling de status de ativos.

    Args:
        url: URL base do Zabbix (ex: "https://zabbix.corp.com"). Sem trailing slash.
        token: API token Zabbix 7.x. Se informado, user/password são ignorados.
        user: Usuário para autenticação por login (Zabbix <7.x).
        password: Senha do usuário.
        group_map: Mapeamento asset_type → lista de nomes de grupos Zabbix.
            None usa o mapa padrão (_DEFAULT_GROUP_MAP).
        timeout: Timeout HTTP em segundos (padrão: 15).
    """

    def __init__(
        self,
        url: str,
        token: str | None = None,
        user: str = "",
        password: str = "",
        group_map: dict[str, list[str]] | None = None,
        timeout: float = 15.0,
    ) -> None:
        self._endpoint = url.rstrip("/") + _JSONRPC_PATH
        self._token = token
        self._user = user
        self._password = password
        self._session_token: str | None = None
        self._group_map = group_map or _DEFAULT_GROUP_MAP
        self._timeout = timeout
        # Índice invertido: nome_do_grupo → asset_type (O(1) lookup por host)
        self._group_index: dict[str, str] = {
            grupo: atype for atype, grupos in self._group_map.items() for grupo in grupos
        }
        self._log = log.bind(client="ZabbixAssetClient")

    @classmethod
    def from_env(cls) -> ZabbixAssetClient:
        """Cria instância a partir de variáveis de ambiente.

        Vars lidas:
          ZBX_URL (obrigatória), ZBX_TOKEN, ZBX_USER, ZBX_PASSWORD, ZBX_GROUP_MAP.
        """
        url = os.environ.get("ZBX_URL", "")
        if not url:
            raise ValueError("ZBX_URL não configurada — defina a variável de ambiente ZBX_URL")
        token = os.environ.get("ZBX_TOKEN") or None
        user = os.environ.get("ZBX_USER", "")
        password = os.environ.get("ZBX_PASSWORD", "")
        group_map_raw = os.environ.get("ZBX_GROUP_MAP")
        group_map: dict[str, list[str]] | None = None
        if group_map_raw:
            group_map = json.loads(group_map_raw)
        return cls(url=url, token=token, user=user, password=password, group_map=group_map)

    # ── Autenticação ─────────────────────────────────────────────────────────

    def _auth_headers(self) -> dict[str, str]:
        """Headers HTTP para autenticação Bearer (Zabbix 7.x API token)."""
        if self._token:
            return {"Authorization": f"Bearer {self._token}"}
        return {}

    def _auth_field(self) -> str | None:
        """Valor do campo JSON-RPC 'auth' (user/password session token)."""
        if self._token:
            return None  # Zabbix 7.x usa Bearer — campo auth ignorado
        if not self._session_token:
            self._session_token = self._login()
        return self._session_token

    def _login(self) -> str:
        """Autentica com user/password e retorna token de sessão."""
        result = self._rpc(
            "user.login",
            {"username": self._user, "password": self._password},
            auth=False,
        )
        if not isinstance(result, str) or not result:
            raise RuntimeError("Zabbix login retornou token inválido")
        self._log.info("zabbix_asset_login_ok")
        return result

    # ── JSON-RPC ─────────────────────────────────────────────────────────────

    def _rpc(self, method: str, params: dict[str, Any], *, auth: bool = True) -> Any:
        """Executa chamada JSON-RPC ao Zabbix.

        Args:
            method: Método Zabbix (ex: "host.get").
            params: Parâmetros do método.
            auth: Se True, inclui credencial de autenticação no payload.

        Returns:
            Conteúdo do campo 'result' na resposta JSON-RPC.

        Raises:
            RuntimeError: Se a API retornar campo 'error'.
            requests.HTTPError: Para erros HTTP 4xx/5xx.
        """
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": _RPC_ID,
        }
        if auth:
            auth_val = self._auth_field()
            if auth_val:
                payload["auth"] = auth_val

        headers = {"Content-Type": "application/json", **self._auth_headers()}
        resp = requests.post(self._endpoint, json=payload, headers=headers, timeout=self._timeout)
        resp.raise_for_status()
        body: dict[str, Any] = resp.json()
        if "error" in body:
            raise RuntimeError(f"Zabbix API error [{method}]: {body['error']}")
        return body.get("result")

    # ── Chamadas públicas (exatamente 2 por poll) ─────────────────────────────

    def get_hosts_with_groups(self) -> list[HostInfo]:
        """Retorna todos os hosts monitorados com asset_type derivado dos grupos.

        Chamada 1/2 por ciclo de poll.

        Returns:
            Lista de HostInfo com hostid, host, name e asset_type classificado.
        """
        raw = (
            self._rpc(
                "host.get",
                {
                    "output": ["hostid", "host", "name"],
                    "selectGroups": ["name"],
                    "monitored_hosts": True,
                    "filter": {"status": 0},
                },
            )
            or []
        )

        resultado: list[HostInfo] = []
        for h in raw:
            grupos = [g["name"] for g in h.get("groups", [])]
            asset_type = self._classificar(grupos)
            resultado.append(
                HostInfo(
                    hostid=h["hostid"],
                    host=h["host"],
                    name=h.get("name", h["host"]),
                    asset_type=asset_type,
                )
            )

        self._log.debug("zabbix_hosts_coletados", total=len(resultado))
        return resultado

    def get_icmp_status(self, hostids: list[str]) -> dict[str, str]:
        """Retorna mapa hostid → valor icmpping para os hosts informados.

        Hosts sem item icmpping no Zabbix NÃO aparecem no resultado —
        o service interpreta a ausência como 'unknown'.

        Chamada 2/2 por ciclo de poll.

        Args:
            hostids: Lista de hostids a consultar.

        Returns:
            Dict[hostid, "0"|"1"] (apenas hosts com item icmpping ativo).
        """
        if not hostids:
            return {}

        raw = (
            self._rpc(
                "item.get",
                {
                    "output": ["hostid", "lastvalue"],
                    "hostids": hostids,
                    "filter": {"key_": "icmpping"},
                    "monitored": True,
                },
            )
            or []
        )

        mapa = {item["hostid"]: item["lastvalue"] for item in raw}
        self._log.debug("zabbix_icmp_coletado", hosts_com_icmp=len(mapa), total=len(hostids))
        return mapa

    # ── Classificação ─────────────────────────────────────────────────────────

    def _classificar(self, grupos: list[str]) -> str:
        """Deriva asset_type a partir dos grupos do host.

        Itera os grupos na ordem retornada pelo Zabbix; retorna o primeiro
        mapeamento encontrado. Hosts sem correspondência → 'other'.
        """
        for grupo in grupos:
            atype = self._group_index.get(grupo)
            if atype:
                return atype
        return "other"
