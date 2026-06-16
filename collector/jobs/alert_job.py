"""Alerta de governança — lê gov_zabbix_summary/riscos do InfluxDB, envia SMTP.

Executado pelo BlockingScheduler do itgov-collector (processo único — sem race de
schedulers). Estado persiste em /data/alert_state.json com escrita atômica.

Tipos de alerta:
  disaster  — problems_disaster > 0 (severity=5 no Zabbix)
  score     — zabbix_risk_score < ALERT_SCORE_THRESHOLD

Anti-spam: cooldown de ALERT_COOLDOWN_S segundos por tipo.
Dados residem em InfluxDB (gov_zabbix_summary + gov_zabbix_riscos), escritos pelo
zabbix_risk_collector a cada hora — janela de leitura: -2h.
"""

from __future__ import annotations

import contextlib
import json
import os
import smtplib
import sys
import tempfile
import time
from email.message import EmailMessage
from pathlib import Path

import structlog
from influxdb_client import InfluxDBClient

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import settings

log = structlog.get_logger("alert_job")

_STATE_FILE = Path("/data/alert_state.json")


# ── InfluxDB ──────────────────────────────────────────────────────────────────


def _query_influx() -> dict[str, float | str]:
    """Retorna últimas métricas relevantes de gov_zabbix_summary e gov_zabbix_riscos.

    Retorna dict vazio se InfluxDB estiver inacessível ou sem dados na janela.
    """
    flux = f"""
from(bucket: "{settings.INFLUX_BUCKET_RAW}")
  |> range(start: -2h)
  |> filter(fn: (r) =>
      r._measurement == "gov_zabbix_summary" or
      r._measurement == "gov_zabbix_riscos")
  |> last()
  |> keep(columns: ["_measurement", "_field", "_value"])
"""
    result: dict[str, float | str] = {}
    with InfluxDBClient(
        url=settings.INFLUX_URL,
        token=settings.INFLUX_TOKEN,
        org=settings.INFLUX_ORG,
    ) as client:
        for table in client.query_api().query(flux):
            for record in table.records:
                key = f"{record.get_measurement()}.{record.get_field()}"
                result[key] = record.get_value()
    return result


# ── Estado persistido ─────────────────────────────────────────────────────────


def _load_state() -> dict:
    try:
        return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=_STATE_FILE.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, _STATE_FILE)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _cooldown_ok(state: dict, key: str) -> bool:
    return (time.time() - state.get(f"{key}_last_ts", 0.0)) >= settings.ALERT_COOLDOWN_S


# ── SMTP ──────────────────────────────────────────────────────────────────────


def _send_smtp(subject: str, body: str) -> None:
    """Envia e-mail best-effort — falhas são logadas mas não propagadas."""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.SMTP_FROM
    msg["To"] = settings.SMTP_TO
    msg.set_content(body)

    # SMTP_TLS=True  → smtplib.SMTP  + starttls()  (STARTTLS — porta 25/587)
    # SMTP_TLS=False → smtplib.SMTP_SSL             (SSL implícito — porta 465)
    smtp_cls = smtplib.SMTP_SSL if not settings.SMTP_TLS else smtplib.SMTP
    try:
        with smtp_cls(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as smtp:
            if settings.SMTP_TLS:
                smtp.starttls()
            if settings.SMTP_AUTH:
                smtp.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            smtp.send_message(msg)
        log.info("smtp_enviado", subject=subject, to=settings.SMTP_TO)
    except Exception as exc:
        log.error("smtp_falhou", error=str(exc), subject=subject)


# ── Avaliação de alertas ──────────────────────────────────────────────────────


def _check_and_alert(metrics: dict, state: dict) -> dict:
    """Avalia thresholds e dispara e-mails se necessário. Retorna novo estado."""
    new_state = dict(state)
    now = time.time()

    problems_disaster = int(metrics.get("gov_zabbix_summary.problems_disaster", 0))
    zabbix_score = float(metrics.get("gov_zabbix_riscos.score", 100.0))
    top_incidente = str(metrics.get("gov_zabbix_riscos.top_incidente", "N/A"))

    # ── DISASTER ─────────────────────────────────────────────────────────────
    if problems_disaster > 0:
        if not new_state.get("disaster_active") or _cooldown_ok(new_state, "disaster"):
            new_state["disaster_active"] = True
            new_state["disaster_last_ts"] = now
            _send_smtp(
                subject=f"[DISASTER] {problems_disaster} trigger(s) severidade máxima — TI Govti",
                body=(
                    f"ALERTA DISASTER\n\n"
                    f"Triggers com severidade DISASTER: {problems_disaster}\n"
                    f"Incidente principal: {top_incidente}\n"
                    f"Score Zabbix atual: {zabbix_score:.1f}/100\n\n"
                    f"Dashboard: http://172.29.2.11:5000\n"
                    f"Zabbix:    http://172.29.2.11:8080\n\n"
                    f"Alerta se repete a cada "
                    f"{settings.ALERT_COOLDOWN_S // 3600}h enquanto o problema estiver aberto."
                ),
            )
        else:
            log.debug("disaster_em_cooldown", problems_disaster=problems_disaster)
    else:
        if new_state.get("disaster_active"):
            log.info("disaster_resolvido")
        new_state["disaster_active"] = False

    # ── SCORE CRÍTICO ─────────────────────────────────────────────────────────
    if zabbix_score < settings.ALERT_SCORE_THRESHOLD:
        if not new_state.get("score_critical") or _cooldown_ok(new_state, "score"):
            new_state["score_critical"] = True
            new_state["score_last_ts"] = now
            _send_smtp(
                subject=(
                    f"[CRÍTICO] Score Zabbix {zabbix_score:.1f} < {settings.ALERT_SCORE_THRESHOLD:.0f} — TI Govti"
                ),
                body=(
                    f"SCORE ZABBIX CRÍTICO\n\n"
                    f"Score atual: {zabbix_score:.1f}/100  "
                    f"(threshold: {settings.ALERT_SCORE_THRESHOLD:.0f})\n"
                    f"Triggers DISASTER: {problems_disaster}\n"
                    f"Incidente principal: {top_incidente}\n\n"
                    f"Dashboard: http://172.29.2.11:5000\n"
                ),
            )
        else:
            log.debug("score_em_cooldown", score=zabbix_score)
    elif zabbix_score >= settings.ALERT_SCORE_THRESHOLD:
        if new_state.get("score_critical"):
            log.info("score_normalizado", score=zabbix_score)
        new_state["score_critical"] = False

    return new_state


# ── Entry point ───────────────────────────────────────────────────────────────


def run() -> None:
    """Ponto de entrada do APScheduler — todas as exceções são absorvidas."""
    log.debug("alert_job_iniciado")

    smtp_ready = bool(settings.SMTP_HOST and settings.SMTP_FROM and settings.SMTP_TO)
    if not smtp_ready:
        log.warning("alert_job_ignorado", motivo="SMTP_HOST/FROM/TO não configurados")
        return

    try:
        metrics = _query_influx()
    except Exception as exc:
        log.error("alert_job_influx_falhou", error=str(exc))
        return

    if not metrics:
        log.warning("alert_job_sem_metricas", janela="-2h", bucket=settings.INFLUX_BUCKET_RAW)
        return

    log.debug(
        "alert_job_metricas",
        problems_disaster=metrics.get("gov_zabbix_summary.problems_disaster", "N/A"),
        zabbix_score=metrics.get("gov_zabbix_riscos.score", "N/A"),
    )

    state = _load_state()
    try:
        new_state = _check_and_alert(metrics, state)
        _save_state(new_state)
    except Exception as exc:
        log.error("alert_job_avaliacao_falhou", error=str(exc))
