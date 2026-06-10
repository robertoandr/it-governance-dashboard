"""Job de polling de status de ativos via Zabbix — executa a cada 60s.

Faz exatamente 2 chamadas Zabbix por ciclo:
  1. host.get com selectGroups → classificação por asset_type
  2. item.get key_=icmpping → status up/down/unknown por host

O resultado é persistido em asset_status (upsert) e asset_status_history
(somente quando status muda). Os endpoints Flask NUNCA chamam o Zabbix —
leem apenas a tabela local atualizada por este job.

Variáveis de ambiente necessárias:
  ZBX_URL (obrigatória)
  ZBX_TOKEN  OU  ZBX_USER + ZBX_PASSWORD
  ZBX_GROUP_MAP (opcional, JSON com mapeamento asset_type → grupos)
  DATABASE_URL (padrão: sqlite:///data/govti.db)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import structlog

# Project root deve vir antes do collector/ para que `import config`
# em itgov/db/session.py resolva para o config.py raiz (com DATABASE_URL).
_PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

log = structlog.get_logger("asset_status_job")


def run() -> None:
    """Ponto de entrada do job — chamado pelo APScheduler a cada 60s."""
    log.info("asset_status_job_iniciado")

    zbx_url = os.getenv("ZBX_URL", "")
    if not zbx_url:
        log.warning("asset_status_job_ignorado", motivo="ZBX_URL não configurada")
        return

    # ─── Coleta via Zabbix (2 chamadas) ──────────────────────────────────────
    try:
        from itgov.clients.zabbix_asset_client import ZabbixAssetClient

        client = ZabbixAssetClient.from_env()
        hosts = client.get_hosts_with_groups()
        hostids = [h.hostid for h in hosts]
        icmp_map = client.get_icmp_status(hostids)

        log.info("asset_zabbix_coletado", hosts=len(hosts), com_icmp=len(icmp_map))
    except Exception as exc:
        log.error("asset_zabbix_coleta_falhou", error=str(exc))
        return

    # ─── Persistência em SQLite/PostgreSQL ────────────────────────────────────
    try:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        from itgov.services.asset_status_service import AssetStatusService

        db_url = os.getenv("DATABASE_URL", "sqlite:///data/govti.db")
        kwargs: dict = {"connect_args": {"check_same_thread": False}} if "sqlite" in db_url else {}
        engine = create_engine(db_url, **kwargs)

        with Session(engine) as session:
            transicoes = AssetStatusService(session).sync(hosts, icmp_map)
            log.info("asset_status_persistido", transicoes=transicoes)
    except Exception as exc:
        log.error("asset_status_persistencia_falhou", error=str(exc))
