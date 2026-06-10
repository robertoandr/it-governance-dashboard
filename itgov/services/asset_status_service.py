"""Serviço de persistência e leitura de status de ativos monitorados via Zabbix.

sync() — chamado pelo job a cada 60s:
  Recebe os dados brutos das 2 chamadas Zabbix e atualiza a tabela local.
  Upsert de asset_status para cada host ativo.
  Registra transição em asset_status_history APENAS quando status muda.
  duration_seconds = segundos no status anterior; None se não há last_change.

Primeiro poll: popula asset_status, zero transições gravadas (sem estado anterior).
A partir do 2º poll: quedas e retornos passam a ser registrados com duração.

Métodos de leitura (nunca chamam Zabbix ao vivo — apenas leem a tabela local):
  list_assets   → listagem com filtros opcionais por asset_type e status
  summary       → contadores agrupados por asset_type e status
  history       → transições recentes ordenadas por changed_at DESC
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from itgov.clients.zabbix_asset_client import HostInfo
from itgov.models.db.asset_status import AssetStatusDB, AssetStatusHistoryDB

log = structlog.get_logger(__name__)

# Valores válidos de status
STATUS_UP = "up"
STATUS_DOWN = "down"
STATUS_UNKNOWN = "unknown"


@dataclass
class AssetSummary:
    """Resumo de status de ativos agrupado por tipo."""

    total: int
    por_tipo: dict[str, int]
    por_status: dict[str, int]
    por_tipo_status: dict[str, dict[str, int]]


def _utc(dt: datetime | None) -> datetime | None:
    """Garante timezone UTC — SQLite devolve datetimes sem tzinfo."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _derivar_status(hostid: str, icmp_map: dict[str, str]) -> str:
    """Converte valor icmpping em status legível.

    icmpping=1 → up
    icmpping=0 → down
    ausente no mapa → unknown (nunca assume up por omissão)
    """
    valor = icmp_map.get(hostid)
    if valor is None:
        return STATUS_UNKNOWN
    return STATUS_UP if valor == "1" else STATUS_DOWN


class AssetStatusService:
    """Sincroniza e expõe status de ativos coletados via Zabbix.

    Args:
        session: Session SQLAlchemy ativa.
    """

    def __init__(self, session: Session) -> None:
        self._session = session
        self._log = log.bind(service="AssetStatusService")

    # ── Sync (gravação) ───────────────────────────────────────────────────────

    def sync(self, hosts: list[HostInfo], icmp_map: dict[str, str]) -> int:
        """Sincroniza asset_status e grava transições quando status muda.

        Args:
            hosts: Lista de HostInfo retornada pelo ZabbixAssetClient.
            icmp_map: Mapa hostid → valor icmpping ("0"|"1").

        Returns:
            Número de transições registradas neste ciclo.
        """
        agora = datetime.now(UTC)
        hostids = [h.hostid for h in hosts]

        # Carrega estado atual de todos os hosts monitorados de uma vez
        stmt = select(AssetStatusDB).where(AssetStatusDB.hostid.in_(hostids))
        existentes: dict[str, AssetStatusDB] = {r.hostid: r for r in self._session.scalars(stmt)}

        transicoes = 0
        novos: list[AssetStatusDB] = []
        historico: list[AssetStatusHistoryDB] = []

        for host in hosts:
            novo_status = _derivar_status(host.hostid, icmp_map)
            atual = existentes.get(host.hostid)

            if atual is None:
                # Primeiro poll — insere sem registrar transição
                novos.append(
                    AssetStatusDB(
                        hostid=host.hostid,
                        host=host.host,
                        name=host.name,
                        asset_type=host.asset_type,
                        status=novo_status,
                        last_change=None,  # sem histórico anterior
                        updated_at=agora,
                    )
                )
            elif atual.status != novo_status:
                # Status mudou → registra transição com duração no status anterior
                duration: int | None = None
                last_change_utc = _utc(atual.last_change)
                if last_change_utc is not None:
                    duration = int((agora - last_change_utc).total_seconds())

                historico.append(
                    AssetStatusHistoryDB(
                        hostid=host.hostid,
                        asset_type=host.asset_type,
                        from_status=atual.status,
                        to_status=novo_status,
                        changed_at=agora,
                        duration_seconds=duration,
                    )
                )

                atual.status = novo_status
                atual.host = host.host
                atual.name = host.name
                atual.asset_type = host.asset_type
                atual.last_change = agora
                atual.updated_at = agora
                transicoes += 1
            else:
                # Status inalterado — atualiza apenas updated_at e metadados
                atual.host = host.host
                atual.name = host.name
                atual.asset_type = host.asset_type
                atual.updated_at = agora

        if novos:
            self._session.add_all(novos)
        if historico:
            self._session.add_all(historico)

        self._session.commit()

        self._log.info(
            "asset_sync_concluido",
            hosts=len(hosts),
            novos=len(novos),
            transicoes=transicoes,
        )
        return transicoes

    # ── Leitura (endpoints nunca chamam Zabbix) ───────────────────────────────

    def list_assets(
        self,
        asset_type: str | None = None,
        status: str | None = None,
    ) -> list[AssetStatusDB]:
        """Lista ativos com filtros opcionais.

        Args:
            asset_type: Filtro por tipo (camera | server | other | ...).
            status: Filtro por status (up | down | unknown).

        Returns:
            Lista ordenada por asset_type, host.
        """
        stmt = select(AssetStatusDB)
        if asset_type:
            stmt = stmt.where(AssetStatusDB.asset_type == asset_type)
        if status:
            stmt = stmt.where(AssetStatusDB.status == status)
        stmt = stmt.order_by(AssetStatusDB.asset_type, AssetStatusDB.host)
        return list(self._session.scalars(stmt))

    def summary(self) -> AssetSummary:
        """Retorna contagens agrupadas por tipo e status.

        Returns:
            AssetSummary com totais globais e breakdown por tipo/status.
        """
        stmt = select(
            AssetStatusDB.asset_type,
            AssetStatusDB.status,
            func.count().label("qtd"),
        ).group_by(AssetStatusDB.asset_type, AssetStatusDB.status)

        rows = self._session.execute(stmt).all()

        total = 0
        por_tipo: dict[str, int] = {}
        por_status: dict[str, int] = {}
        por_tipo_status: dict[str, dict[str, int]] = {}

        for atype, status_val, qtd in rows:
            total += qtd
            por_tipo[atype] = por_tipo.get(atype, 0) + qtd
            por_status[status_val] = por_status.get(status_val, 0) + qtd
            if atype not in por_tipo_status:
                por_tipo_status[atype] = {}
            por_tipo_status[atype][status_val] = qtd

        return AssetSummary(
            total=total,
            por_tipo=por_tipo,
            por_status=por_status,
            por_tipo_status=por_tipo_status,
        )

    def history(
        self,
        days: int = 7,
        hostid: str | None = None,
        asset_type: str | None = None,
        limit: int = 200,
    ) -> list[AssetStatusHistoryDB]:
        """Retorna transições de status recentes.

        Args:
            days: Janela de tempo em dias (padrão: 7).
            hostid: Filtro opcional por host específico.
            asset_type: Filtro opcional por tipo de ativo.
            limit: Máximo de registros retornados (padrão: 200).

        Returns:
            Lista ordenada por changed_at DESC (mais recente primeiro).
        """
        desde = datetime.now(UTC) - timedelta(days=days)
        stmt = select(AssetStatusHistoryDB).where(AssetStatusHistoryDB.changed_at >= desde)
        if hostid:
            stmt = stmt.where(AssetStatusHistoryDB.hostid == hostid)
        if asset_type:
            stmt = stmt.where(AssetStatusHistoryDB.asset_type == asset_type)
        stmt = stmt.order_by(AssetStatusHistoryDB.changed_at.desc()).limit(limit)
        return list(self._session.scalars(stmt))
