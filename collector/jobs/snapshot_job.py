"""Snapshot diário do score de governança — persiste via SnapshotService.

Executado pelo APScheduler às 02:00 (CronTrigger). Calcula o score atual
via InfluxDBMetricsProvider (parâmetros explícitos das env vars do collector)
e chama SnapshotService.maybe_save() para dedupe automático.

Adiciona o project root ao sys.path para importar app.* e itgov.* sem
depender da estrutura de pacotes do collector.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import structlog

# Project root precisa vir antes do collector/ para que `import config`
# em itgov/db/session.py resolva para o config.py raiz (com DATABASE_URL).
_PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

log = structlog.get_logger("snapshot_job")


def run() -> None:
    """Ponto de entrada do job — chamado pelo APScheduler."""
    log.info("snapshot_job_iniciado")

    # ─── Coleta e cálculo do score ────────────────────────────────────
    try:
        from app.services.influxdb_provider import InfluxDBMetricsProvider
        from app.services.metrics_aggregator import MetricsAggregator

        provider = InfluxDBMetricsProvider(
            url=os.getenv("INFLUX_URL"),
            token=os.getenv("INFLUX_TOKEN"),
            org=os.getenv("INFLUX_ORG"),
            bucket_raw=os.getenv("INFLUX_BUCKET_RAW", "governance_raw"),
        )
        score = asyncio.run(MetricsAggregator(provider=provider).calculate_full_score())
    except Exception as exc:
        log.error("snapshot_score_coleta_falhou", error=str(exc))
        return

    # ─── Persistência com dedupe ──────────────────────────────────────
    try:
        from itgov.services.snapshot_service import SnapshotService

        db_url = os.getenv("DATABASE_URL", "sqlite:///data/govti.db")
        kwargs: dict = {"connect_args": {"check_same_thread": False}} if "sqlite" in db_url else {}
        engine = create_engine(db_url, **kwargs)

        with Session(engine) as session:
            snap = SnapshotService(session).maybe_save(score)
            if snap:
                log.info(
                    "snapshot_persistido",
                    id=str(snap.id),
                    global_score=snap.global_score,
                    coverage=f"{snap.coverage_real}/{snap.coverage_total}",
                )
            else:
                log.info("snapshot_sem_mudanca_material_ignorado")
    except Exception as exc:
        log.error("snapshot_persistencia_falhou", error=str(exc))
