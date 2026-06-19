import logging
import signal
import sys
from datetime import datetime

import structlog
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from jobs.acronis_collector import run as collect_acronis
from jobs.acronis_risk_collector import run as collect_acronis_risk
from jobs.alert_job import run as run_alerts
from jobs.entra_id_collector import run as collect_entra_id
from jobs.github_pats import collect_github_pats
from jobs.github_pr_collector import run as collect_github_prs
from jobs.gitleaks_scan import run_gitleaks_scan
from jobs.intune_patch_collector import run as collect_intune_patch
from jobs.patch_collector import run as collect_patch
from jobs.secure_score_collector import run as collect_secure_score
from jobs.zabbix_availability_collector import run as collect_zabbix_availability
from jobs.zabbix_risk_collector import run as collect_zabbix_risks

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
        next_run_time=datetime.now(),
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

    if settings.ZABBIX_URL and settings.ZABBIX_TOKEN:
        scheduler.add_job(
            collect_zabbix_availability,
            CronTrigger(hour="*"),
            id="zabbix_availability_collector",
            name="Zabbix Availability Collector",
            max_instances=1,
            coalesce=True,
            next_run_time=datetime.now(),
        )
        log.info("zabbix_availability_job_registrado")

        scheduler.add_job(
            collect_zabbix_risks,
            CronTrigger(hour="*"),
            id="zabbix_risk_collector",
            name="Zabbix Risk Score Collector",
            max_instances=1,
            coalesce=True,
            next_run_time=datetime.now(),
        )
        log.info("zabbix_risk_job_registrado")
    else:
        log.warning("zabbix_jobs_ignorados", motivo="ZABBIX_URL ou ZABBIX_TOKEN não configurados")

    if settings.ACRONIS_BASE_URL and settings.ACRONIS_CLIENT_ID and settings.ACRONIS_CLIENT_SECRET:
        scheduler.add_job(
            collect_acronis,
            CronTrigger(hour="*"),
            id="acronis_collector",
            name="Acronis Cyber Cloud Collector",
            max_instances=1,
            coalesce=True,
            next_run_time=datetime.now(),
        )
        log.info("acronis_job_registrado")

        scheduler.add_job(
            collect_acronis_risk,
            CronTrigger(hour="*"),
            id="acronis_risk_collector",
            name="Acronis Risk Posture Collector",
            max_instances=1,
            coalesce=True,
            next_run_time=datetime.now(),
        )
        log.info("acronis_risk_job_registrado")
    else:
        log.warning("acronis_jobs_ignorados", motivo="ACRONIS_* env vars não configuradas")

    if settings.AZURE_TENANT_ID and settings.AZURE_CLIENT_ID and settings.AZURE_CLIENT_SECRET:
        scheduler.add_job(
            collect_entra_id,
            CronTrigger(hour="*/6"),
            id="entra_id_collector",
            name="Entra ID Metrics Collector",
            max_instances=1,
            coalesce=True,
            next_run_time=datetime.now(),
        )
        log.info("entra_id_job_registered")

        scheduler.add_job(
            collect_secure_score,
            CronTrigger(hour="*/6"),
            id="secure_score_collector",
            name="Microsoft Secure Score Collector",
            max_instances=1,
            coalesce=True,
            next_run_time=datetime.now(),
        )
        log.info("secure_score_job_registrado")

        scheduler.add_job(
            collect_intune_patch,
            CronTrigger(hour="*/6"),
            id="intune_patch_collector",
            name="Intune Patch Compliance Collector",
            max_instances=1,
            coalesce=True,
        )
        log.info("intune_patch_job_registered")
    else:
        log.warning("entra_id_job_skipped", reason="AZURE_* env vars not set")

    if settings.LDAP_SERVER and settings.LDAP_BIND_DN and settings.WINRM_USER:
        scheduler.add_job(
            collect_patch,
            CronTrigger(hour="*/6"),
            id="patch_collector",
            name="AD+WinRM Patch Compliance Collector",
            max_instances=1,
            coalesce=True,
            next_run_time=datetime.now(),
        )
        log.info("patch_job_registered")
    else:
        log.warning(
            "patch_job_skipped",
            reason="LDAP_SERVER / LDAP_BIND_DN / WINRM_USER não configurados",
        )

    if settings.SMTP_HOST and settings.SMTP_FROM and settings.SMTP_TO:
        scheduler.add_job(
            run_alerts,
            IntervalTrigger(seconds=settings.ALERT_JOB_INTERVAL_S),
            id="alert_job",
            name="Governance Alert Job",
            max_instances=1,
            coalesce=True,
            next_run_time=datetime.now(),
        )
        log.info("alert_job_registrado", interval_s=settings.ALERT_JOB_INTERVAL_S)
    else:
        log.warning("alert_job_ignorado", motivo="SMTP_HOST/FROM/TO não configurados")

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    log.info("scheduler_starting", jobs=len(scheduler.get_jobs()))
    scheduler.start()
