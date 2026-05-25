import logging
import signal
import sys

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from jobs.github_pats import collect_github_pats
from jobs.gitleaks_scan import run_gitleaks_scan

from config import settings

logging.basicConfig(
    level=settings.LOG_LEVEL,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
log = logging.getLogger("collector")


def shutdown(signum, frame):
    log.info("Sinal recebido (%s), encerrando scheduler...", signum)
    scheduler.shutdown(wait=False)
    sys.exit(0)


if __name__ == "__main__":
    log.info("IT Governance Collector iniciando...")

    scheduler = BlockingScheduler(timezone=settings.TZ)

    scheduler.add_job(
        collect_github_pats,
        CronTrigger(hour="*/6"),
        id="github_pats_inventory",
        name="GitHub PATs Inventory",
        max_instances=1,
        coalesce=True,
    )

    scheduler.add_job(
        run_gitleaks_scan,
        CronTrigger(hour=2, minute=0),
        id="gitleaks_scan",
        name="Gitleaks Secret Scan",
        max_instances=1,
        coalesce=True,
    )

    log.info("Executando jobs iniciais...")
    try:
        collect_github_pats()
    except Exception as e:
        log.exception("Falha no warm start de github_pats: %s", e)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    log.info("Scheduler ativo. Aguardando triggers...")
    scheduler.start()
