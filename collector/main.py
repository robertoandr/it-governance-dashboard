import logging
import signal
import sys
from datetime import datetime

import structlog
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from jobs.github_pats import collect_github_pats
from jobs.github_pr_collector import run as collect_github_prs
from jobs.gitleaks_scan import run_gitleaks_scan

from config import settings

structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(settings.LOG_LEVEL)),
)
log = structlog.get_logger("collector")


def shutdown(signum, frame):
    log.info("shutdown_signal", signum=signum)
    scheduler.shutdown(wait=False)
    sys.exit(0)


if __name__ == "__main__":
    log.info("collector_starting")

    scheduler = BlockingScheduler(timezone=settings.TZ)

    scheduler.add_job(
        collect_github_prs,
        IntervalTrigger(minutes=15),
        id="github_pr_collector",
        name="GitHub PR Collector",
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(),  # run immediately on start (90d backfill)
    )

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

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    log.info("scheduler_starting", jobs=len(scheduler.get_jobs()))
    scheduler.start()
