"""Parser de hierarquia Zabbix para o módulo de alertas CFTV.

Constrói um grafo de dependência DVR→câmeras lendo hostgroup.get + host.get.
Cache em memória com TTL de 1 hora (thread-safe via RLock).

Convenção de nomenclatura de grupos esperada:
  CFTV/{ramo}/{dvr_name}   → hosts neste grupo são câmeras do DVR dvr_name
  CFTV/{ramo}/Stand-Alone  → câmeras standalone (alertas individuais)
  Lojas/...                → ativos sem DVR (faciais, antenas) — individuais

DVR é identificado como o host cujo hostname técnico bate com o
último segmento de um grupo CFTV de profundidade 3.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

import structlog

from itgov.utils.http_client import SyncAPIClient

log = structlog.get_logger(__name__)

_JSONRPC_PATH = "/api_jsonrpc.php"
_CACHE_TTL = 3600  # 1 hora


@dataclass
class HostNode:
    """Representação de um host no grafo de hierarquia."""

    hostid: str
    host: str  # nome técnico (hostname)
    name: str  # visible name
    ramo: str  # ex: "Centro"
    role: str  # dvr | camera | standalone | lojas | outro
    dvr_parent: str | None = None  # host técnico do DVR pai (só para câmeras)
    cameras: list[str] = field(default_factory=list)  # hostids filhos (só para DVR)


@dataclass
class HierarchyTree:
    """Árvore de hierarquia completa resolvida a partir do Zabbix."""

    dvrs: dict[str, HostNode]  # hostname → HostNode (role=dvr)
    cameras: dict[str, HostNode]  # hostid → HostNode (role=camera)
    standalone: dict[str, HostNode]  # hostid → HostNode (role=standalone)
    lojas: dict[str, HostNode]  # hostid → HostNode (role=lojas)
    all_hosts: dict[str, HostNode]  # hostid → HostNode (todos)


class ZabbixHierarchyClient(SyncAPIClient):
    """Cliente Zabbix para leitura da hierarquia de grupos.

    Faz exatamente 2 chamadas por refresh:
      1. hostgroup.get + selectHosts → todos os grupos com seus hosts
      2. host.get → detalhes dos hosts (name, status)
    """

    def __init__(
        self,
        url: str,
        token: str | None = None,
        user: str = "",
        password: str = "",
        timeout: float = 20.0,
    ) -> None:
        super().__init__(base_url=url.rstrip("/"), timeout=timeout)
        self._token = token
        self._user = user
        self._password = password
        self._session_token: str | None = None
        self._lock = threading.Lock()

    def _auth_header(self) -> dict[str, str]:
        if self._token:
            return {"Authorization": f"Bearer {self._token}"}
        return {}

    def _get_session(self) -> str | None:
        if self._token:
            return None
        with self._lock:
            if not self._session_token:
                result = self._rpc("user.login", {"username": self._user, "password": self._password}, auth=False)
                if not isinstance(result, str):
                    raise RuntimeError("Login Zabbix retornou token inválido")
                self._session_token = result
            return self._session_token

    def _rpc(self, method: str, params: dict[str, Any], *, auth: bool = True) -> Any:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}
        if auth:
            sess = self._get_session()
            if sess:
                payload["auth"] = sess
        headers = {"Content-Type": "application/json", **self._auth_header()}
        resp = self.post(_JSONRPC_PATH, json=payload, headers=headers)
        body = resp.json()
        if "error" in body:
            raise RuntimeError(f"Zabbix API [{method}]: {body['error']}")
        return body.get("result")

    def fetch_hierarchy(self) -> HierarchyTree:
        """Busca grupos e hosts do Zabbix e monta a árvore de dependências.

        Returns:
            HierarchyTree com DVRs, câmeras, standalone e ativos Lojas mapeados.
        """
        # Chamada 1: grupos com lista de hosts membros
        raw_groups: list[dict] = (
            self._rpc(
                "hostgroup.get",
                {
                    "output": ["groupid", "name"],
                    "selectHosts": ["hostid", "host", "name"],
                    "real_hosts": True,
                },
            )
            or []
        )

        # Chamada 2: status completo dos hosts monitorados
        raw_hosts: list[dict] = (
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

        # Índice hostid → {host, name, groups[]}
        host_detail: dict[str, dict] = {
            h["hostid"]: {
                "host": h["host"],
                "name": h.get("name", h["host"]),
                "groups": [g["name"] for g in h.get("groups", [])],
            }
            for h in raw_hosts
        }

        return _build_tree(raw_groups, host_detail)


def _primary_group(groups: list[str]) -> str:
    """Retorna o grupo mais específico (maior profundidade em '/')."""
    if not groups:
        return ""
    return max(groups, key=lambda g: g.count("/"))


def _build_tree(raw_groups: list[dict], host_detail: dict[str, dict]) -> HierarchyTree:
    """Constrói o HierarchyTree a partir dos dados brutos do Zabbix."""
    # Mapa: dvr_name → ramo (e.g. "DVR-1" → "Centro")
    dvr_name_to_ramo: dict[str, str] = {}
    # Mapa: dvr_name → [hostids das câmeras]
    dvr_cameras_hostids: dict[str, list[str]] = {}

    for grp in raw_groups:
        gname: str = grp["name"]
        parts = gname.split("/")

        # CFTV/{ramo}/{dvr_name} → grupo de câmeras
        if parts[0] == "CFTV" and len(parts) == 3 and parts[2] != "Stand-Alone":
            ramo = parts[1]
            dvr_name = parts[2]
            dvr_name_to_ramo[dvr_name] = ramo
            member_ids = [h["hostid"] for h in grp.get("hosts", [])]
            dvr_cameras_hostids.setdefault(dvr_name, []).extend(member_ids)

    # Identificar quais hosts são DVRs: hostname bate com um dvr_name conhecido
    dvr_host_by_name: dict[str, HostNode] = {}
    for hostid, detail in host_detail.items():
        hname = detail["host"]
        if hname in dvr_name_to_ramo:
            ramo = dvr_name_to_ramo[hname]
            node = HostNode(
                hostid=hostid,
                host=hname,
                name=detail["name"],
                ramo=ramo,
                role="dvr",
            )
            dvr_host_by_name[hname] = node

    # Mapear câmeras aos seus DVRs
    cameras: dict[str, HostNode] = {}
    for dvr_name, cam_ids in dvr_cameras_hostids.items():
        dvr_node = dvr_host_by_name.get(dvr_name)
        if dvr_node is None:
            continue
        ramo = dvr_name_to_ramo[dvr_name]
        for cam_id in cam_ids:
            if cam_id not in host_detail:
                continue
            det = host_detail[cam_id]
            cam_node = HostNode(
                hostid=cam_id,
                host=det["host"],
                name=det["name"],
                ramo=ramo,
                role="camera",
                dvr_parent=dvr_name,
            )
            cameras[cam_id] = cam_node
            dvr_node.cameras.append(cam_id)

    # Standalone: hosts em grupo CFTV/{ramo}/Stand-Alone
    standalone: dict[str, HostNode] = {}
    for grp in raw_groups:
        gname: str = grp["name"]
        parts = gname.split("/")
        if parts[0] == "CFTV" and len(parts) == 3 and parts[2] == "Stand-Alone":
            ramo = parts[1]
            for h in grp.get("hosts", []):
                hid = h["hostid"]
                if hid in host_detail:
                    det = host_detail[hid]
                    standalone[hid] = HostNode(
                        hostid=hid,
                        host=det["host"],
                        name=det["name"],
                        ramo=ramo,
                        role="standalone",
                    )

    # Lojas: hosts cujo grupo primário começa com "Lojas/"
    lojas: dict[str, HostNode] = {}
    known_ids = {n.hostid for n in dvr_host_by_name.values()} | set(cameras) | set(standalone)
    for hostid, detail in host_detail.items():
        if hostid in known_ids:
            continue
        pg = _primary_group(detail["groups"])
        if pg.startswith("Lojas/"):
            ramo = pg.split("/")[1] if pg.count("/") >= 1 else "Desconhecido"
            lojas[hostid] = HostNode(
                hostid=hostid,
                host=detail["host"],
                name=detail["name"],
                ramo=ramo,
                role="lojas",
            )

    all_hosts: dict[str, HostNode] = {n.hostid: n for n in dvr_host_by_name.values()}
    all_hosts.update(cameras)
    all_hosts.update(standalone)
    all_hosts.update(lojas)

    log.info(
        "hierarchy_built",
        dvrs=len(dvr_host_by_name),
        cameras=len(cameras),
        standalone=len(standalone),
        lojas=len(lojas),
    )
    return HierarchyTree(
        dvrs={n.hostid: n for n in dvr_host_by_name.values()},
        cameras=cameras,
        standalone=standalone,
        lojas=lojas,
        all_hosts=all_hosts,
    )


class HierarchyCache:
    """Cache em memória do HierarchyTree com refresh automático a cada 1 hora.

    Thread-safe: usa RLock para garantir exclusão mútua no refresh.
    """

    def __init__(self, client: ZabbixHierarchyClient, ttl: int = _CACHE_TTL) -> None:
        self._client = client
        self._ttl = ttl
        self._tree: HierarchyTree | None = None
        self._last_refresh: float = 0.0
        self._rlock = threading.RLock()

    def get(self) -> HierarchyTree:
        """Retorna a árvore do cache, realizando refresh se expirada."""
        with self._rlock:
            now = time.monotonic()
            if self._tree is None or (now - self._last_refresh) >= self._ttl:
                self._refresh()
            return self._tree  # type: ignore[return-value]

    def invalidate(self) -> None:
        """Força próxima chamada a get() a buscar do Zabbix."""
        with self._rlock:
            self._last_refresh = 0.0

    def _refresh(self) -> None:
        log.info("hierarchy_cache_refresh")
        try:
            self._tree = self._client.fetch_hierarchy()
            self._last_refresh = time.monotonic()
        except Exception as exc:
            log.error("hierarchy_cache_refresh_failed", error=str(exc))
            if self._tree is None:
                raise
