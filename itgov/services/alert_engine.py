"""Motor de alertas com causa-raiz, anti-flap e supressão de filhas.

Lógica por ciclo de poll:
  1. Obtém hierarquia do cache (DVRs, câmeras, ativos Lojas)
  2. Consulta status ICMP de todos os hosts via Zabbix API (icmpping)
  3. Aplica anti-flap: só dispara após FLAP_THRESHOLD polls DOWN consecutivos
  4. Suprime alertas de câmeras-filhas quando o DVR-pai está confirmadamente DOWN
  5. Persiste estado em active_alerts e histórico em alert_log
  6. Envia notificação Teams + expõe via GET /v1/alerts/active
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy.orm import Session

from itgov.models.db.alert import ActiveAlert, AlertLog
from itgov.services.teams_notifier import TeamsNotifier
from itgov.services.zabbix_hierarchy import HierarchyCache, HierarchyTree, HostNode

log = structlog.get_logger(__name__)

# Status ICMP retornado pelo Zabbix: "0" = DOWN, "1" = UP
_ICMP_UP = "1"
_ICMP_DOWN = "0"


@dataclass
class _HostState:
    """Estado interno de um host no ciclo de alertas."""

    down_count: int = 0  # polls consecutivos DOWN
    alerted: bool = False  # já disparou alerta (anti-flap passou)
    confirmed_down: bool = False  # DOWN confirmado (alerted == True)


class AlertEngine:
    """Processa um ciclo de poll e dispara/resolve alertas conforme regras.

    Args:
        hierarchy_cache: Cache de hierarquia Zabbix.
        session_factory: Callable que retorna uma Session SQLAlchemy.
        notifier: Instância do TeamsNotifier (None desabilita envio Teams).
        flap_threshold: Polls DOWN consecutivos antes de alertar (padrão: 2).
    """

    def __init__(
        self,
        hierarchy_cache: HierarchyCache,
        session_factory: Any,
        notifier: TeamsNotifier | None,
        flap_threshold: int = 2,
    ) -> None:
        self._cache = hierarchy_cache
        self._session_factory = session_factory
        self._notifier = notifier
        self._flap_threshold = flap_threshold
        # Estado por hostid — acesso restrito à thread de poll (sem lock necessário)
        self._states: dict[str, _HostState] = {}
        self._lock = threading.Lock()

    def _state(self, hostid: str) -> _HostState:
        if hostid not in self._states:
            self._states[hostid] = _HostState()
        return self._states[hostid]

    def run_cycle(self, icmp_status: dict[str, str]) -> None:
        """Executa um ciclo completo de avaliação de alertas.

        Args:
            icmp_status: Mapa hostid → "0"|"1" retornado pelo Zabbix.
        """
        tree = self._cache.get()

        with self._lock, self._session_factory() as session:
            # DVRs primeiro: atualiza confirmed_down antes de calcular supressão
            self._processar_dvrs(tree, icmp_status, session)
            # Supressão baseada no estado JÁ atualizado neste ciclo
            dvrs_down: set[str] = self._dvrs_confirmados_down(tree, icmp_status)
            self._processar_cameras(tree, icmp_status, dvrs_down, session)
            self._processar_individuais(tree, icmp_status, session)
            session.commit()

    # ── Helpers de estado ─────────────────────────────────────────────────────

    def _dvrs_confirmados_down(self, tree: HierarchyTree, icmp: dict[str, str]) -> set[str]:
        """Retorna conjunto de hostnames de DVRs confirmadamente DOWN.

        Usado para suprimir alertas das câmeras-filhas.
        """
        result: set[str] = set()
        for node in tree.dvrs.values():
            status = icmp.get(node.hostid, _ICMP_UP)
            st = self._state(node.hostid)
            if status == _ICMP_DOWN and st.confirmed_down:
                result.add(node.host)
        return result

    def _is_down(self, icmp: dict[str, str], hostid: str) -> bool:
        return icmp.get(hostid, _ICMP_UP) == _ICMP_DOWN

    # ── Processamento por categoria ───────────────────────────────────────────

    def _processar_dvrs(
        self,
        tree: HierarchyTree,
        icmp: dict[str, str],
        session: Session,
    ) -> None:
        for node in tree.dvrs.values():
            is_down = self._is_down(icmp, node.hostid)
            self._avaliar(node, is_down, session, n_filhos=len(node.cameras))

    def _processar_cameras(
        self,
        tree: HierarchyTree,
        icmp: dict[str, str],
        dvrs_down: set[str],
        session: Session,
    ) -> None:
        for node in tree.cameras.values():
            # Suprime: câmera cujo DVR-pai está confirmadamente DOWN
            if node.dvr_parent and node.dvr_parent in dvrs_down:
                log.debug("camera_suprimida", hostid=node.hostid, dvr=node.dvr_parent)
                continue
            is_down = self._is_down(icmp, node.hostid)
            self._avaliar(node, is_down, session)

        for node in tree.standalone.values():
            is_down = self._is_down(icmp, node.hostid)
            self._avaliar(node, is_down, session)

    def _processar_individuais(
        self,
        tree: HierarchyTree,
        icmp: dict[str, str],
        session: Session,
    ) -> None:
        for node in tree.lojas.values():
            is_down = self._is_down(icmp, node.hostid)
            self._avaliar(node, is_down, session)

    # ── Núcleo de avaliação ───────────────────────────────────────────────────

    def _avaliar(
        self,
        node: HostNode,
        is_down: bool,
        session: Session,
        n_filhos: int = 0,
    ) -> None:
        st = self._state(node.hostid)

        if is_down:
            st.down_count += 1
            if st.down_count >= self._flap_threshold and not st.alerted:
                # Anti-flap passou → dispara alerta
                st.alerted = True
                st.confirmed_down = True
                self._disparar_alerta(node, session, n_filhos)
        else:
            # HOST UP
            if st.confirmed_down:
                # Recuperação
                self._resolver_alerta(node, session)
            st.down_count = 0
            st.alerted = False
            st.confirmed_down = False

    # ── Disparo e resolução ───────────────────────────────────────────────────

    def _disparar_alerta(self, node: HostNode, session: Session, n_filhos: int = 0) -> None:
        tipo, severidade, msg = _formatar_alerta(node, n_filhos)
        agora = datetime.now(UTC)

        log_entry = AlertLog(
            host_id=node.hostid,
            hostgroup=node.ramo,
            tipo=tipo,
            severidade=severidade,
            msg=msg,
            status="open",
            created_at=agora,
        )
        session.add(log_entry)

        # Upsert em active_alerts
        ativo = session.get(ActiveAlert, node.hostid)
        if ativo is None:
            ativo = ActiveAlert(host_id=node.hostid, created_at=agora)
            session.add(ativo)
        ativo.host_name = node.name
        ativo.hostgroup = node.ramo
        ativo.tipo = tipo
        ativo.severidade = severidade
        ativo.msg = msg
        ativo.filhos_afetados = n_filhos if n_filhos > 0 else None
        ativo.updated_at = agora

        log.warning("alerta_disparado", hostid=node.hostid, tipo=tipo, msg=msg)

        if self._notifier:
            self._enviar_notificacao(node, tipo, msg, n_filhos)

    def _resolver_alerta(self, node: HostNode, session: Session) -> None:
        agora = datetime.now(UTC)
        msg = f"✅ [{node.ramo}] {node.name} RECUPERADO"

        # Fecha entradas abertas no log
        abertas = session.query(AlertLog).filter(AlertLog.host_id == node.hostid, AlertLog.status == "open").all()
        for entrada in abertas:
            entrada.status = "resolved"
            entrada.resolved_at = agora

        # Grava evento de recuperação no log
        session.add(
            AlertLog(
                host_id=node.hostid,
                hostgroup=node.ramo,
                tipo="RECOVERY",
                severidade="info",
                msg=msg,
                status="resolved",
                created_at=agora,
                resolved_at=agora,
            )
        )

        # Remove de active_alerts
        ativo = session.get(ActiveAlert, node.hostid)
        if ativo:
            session.delete(ativo)

        log.info("alerta_resolvido", hostid=node.hostid, msg=msg)

        if self._notifier:
            try:
                self._notifier.notify_recovery(node.ramo, node.name, msg)
            except Exception as exc:
                log.error("notificacao_recovery_falhou", error=str(exc))

    def _enviar_notificacao(self, node: HostNode, tipo: str, msg: str, n_filhos: int) -> None:
        try:
            if tipo == "DVR_DOWN":
                self._notifier.notify_dvr_down(node.ramo, node.name, n_filhos, msg)  # type: ignore[union-attr]
            else:
                self._notifier.notify_individual_down(node.ramo, node.name, tipo, msg)  # type: ignore[union-attr]
        except Exception as exc:
            log.error("notificacao_falhou", error=str(exc), tipo=tipo)


def _formatar_alerta(node: HostNode, n_filhos: int) -> tuple[str, str, str]:
    """Retorna (tipo, severidade, msg) para um host DOWN."""
    if node.role == "dvr":
        tipo = "DVR_DOWN"
        sev = "critical"
        msg = f"🔴 [{node.ramo}] {node.name} DOWN → {n_filhos} câmera(s) afetada(s)"
    elif node.role == "camera":
        tipo = "CAMERA_DOWN"
        sev = "warning"
        msg = f"⚠️ [{node.ramo}] Câmera {node.name} DOWN"
    elif node.role == "standalone":
        tipo = "CAMERA_DOWN"
        sev = "warning"
        msg = f"⚠️ [{node.ramo}] Câmera {node.name} DOWN (stand-alone)"
    else:
        # lojas: faciais, antenas de tag, etc.
        tipo = "ATIVO_DOWN"
        sev = "warning"
        msg = f"⚠️ [{node.ramo}] {node.name} DOWN"

    return tipo, sev, msg
