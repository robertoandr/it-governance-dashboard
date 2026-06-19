"""HTML view routes for the governance dashboard."""

from __future__ import annotations

import asyncio

import structlog
from flask import Blueprint, abort, render_template
from flask_login import login_required

from app.auth.rbac import require_role
from app.services.metrics_aggregator import MetricsAggregator

log = structlog.get_logger(__name__)

bp = Blueprint("dashboards", __name__, template_folder="../templates")


@bp.context_processor
def _inject_globals() -> dict:
    """Provide app_version and environment when running inside legacy app.py."""
    from flask import current_app

    return {
        "app_version": current_app.config.get("APP_VERSION", "1.1.0"),
        "app_name": current_app.config.get("APP_NAME", "Governança de TI Dashboard"),
        "environment": current_app.config.get("APP_ENVIRONMENT", "production"),
    }


_VALID_PILLAR_IDS = {
    "strategic_alignment",
    "value_delivery",
    "risk_management",
    "resource_management",
    "performance_measure",
}


def _get_governance() -> dict:
    aggregator = MetricsAggregator()
    governance = asyncio.run(aggregator.calculate_full_score())
    return governance.model_dump(mode="json")


@bp.route("/dashboard")
@login_required
@require_role("admin", "gestor", "visualizador")
def dashboard_redirect():
    from flask import redirect, url_for

    return redirect(url_for("dashboards.overview"))


@bp.route("/")
@login_required
@require_role("admin", "gestor", "visualizador")
def overview() -> str:
    """Render governance overview dashboard."""
    data = _get_governance()
    return render_template("dashboards/overview.html", governance=data)


@bp.route("/pillars")
@login_required
@require_role("admin", "gestor", "visualizador")
def pillars() -> str:
    """Render all pillars detail page."""
    data = _get_governance()
    return render_template("dashboards/pillars.html", governance=data)


@bp.route("/sla")
@login_required
@require_role("admin", "gestor")
def sla_chamados() -> str:
    """Render painel SLA / Chamados (Zendesk)."""
    import os

    from itgov.api.v1.zendesk import get_cached_sla_detail

    if not os.getenv("ZENDESK_SUBDOMAIN"):
        abort(404)

    data = get_cached_sla_detail()
    return render_template("dashboards/sla_chamados.html", data=data)


@bp.route("/zendesk")
def zendesk_mttr() -> str:
    """Render Zendesk MTTR / suporte dashboard."""
    import os

    from itgov.api.v1.zendesk import get_cached_mttr_summary, get_cached_volume_by_status

    if not os.getenv("ZENDESK_SUBDOMAIN"):
        abort(404)

    mttr = get_cached_mttr_summary()
    volume = get_cached_volume_by_status()

    return render_template(
        "dashboards/zendesk_mttr.html",
        mttr=mttr,
        volume=volume,
    )


@bp.route("/governance/devices")
@login_required
@require_role("admin", "gestor")
def governance_devices() -> str:
    """Render pilar Dispositivos (Governança M365)."""
    import os

    from itgov.api.v1.governance_devices import get_cached_device_summary

    if not (os.getenv("AZURE_CLIENT_ID") or os.getenv("MSAL_CLIENT_ID")):
        abort(404)

    try:
        summary = get_cached_device_summary()
    except RuntimeError:
        abort(503)

    return render_template("dashboards/governance_devices.html", summary=summary)


@bp.route("/governance/apps")
@login_required
@require_role("admin", "gestor")
def governance_apps() -> str:
    """Render pilar Aplicativos (Governança M365)."""
    import os

    from itgov.api.v1.governance_apps import get_cached_app_summary

    if not (os.getenv("AZURE_CLIENT_ID") or os.getenv("MSAL_CLIENT_ID")):
        abort(404)

    try:
        summary = get_cached_app_summary()
    except RuntimeError:
        abort(503)

    return render_template("dashboards/governance_apps.html", summary=summary)


@bp.route("/governance/compliance")
@login_required
@require_role("admin", "gestor")
def governance_compliance() -> str:
    """Render pilar Compliance (Secure Score) — Governança M365."""
    import os

    from itgov.api.v1.governance_compliance import get_cached_compliance_summary

    if not (os.getenv("AZURE_CLIENT_ID") or os.getenv("MSAL_CLIENT_ID")):
        abort(404)

    summary = get_cached_compliance_summary()
    return render_template("dashboards/governance_compliance.html", summary=summary)


@bp.route("/governance/data")
@login_required
@require_role("admin", "gestor")
def governance_data() -> str:
    """Render pilar Dados (Sensitivity Labels) — Governança M365."""
    import os

    from itgov.api.v1.governance_data import get_cached_data_summary

    if not (os.getenv("AZURE_CLIENT_ID") or os.getenv("MSAL_CLIENT_ID")):
        abort(404)

    summary = get_cached_data_summary()
    return render_template("dashboards/governance_data.html", summary=summary)


@bp.route("/backup")
@login_required
@require_role("admin", "gestor")
def acronis_backup() -> str:
    """Render painel de Backup/Proteção Acronis."""
    import os

    from itgov.api.v1.acronis_backup import get_cached_acronis_summary

    if not os.getenv("ACRONIS_BASE_URL"):
        abort(404)

    data = get_cached_acronis_summary()
    return render_template("dashboards/acronis_backup.html", data=data)


@bp.route("/zabbix")
@login_required
@require_role("admin", "gestor", "operador")
def zabbix_monitoring() -> str:
    """Render painel de monitoramento Zabbix."""
    import os

    from itgov.api.v1.zabbix_monitoring import get_cached_problems, get_cached_zabbix_summary

    if not os.getenv("ZABBIX_URL"):
        abort(404)

    summary = get_cached_zabbix_summary()
    problems = get_cached_problems()
    return render_template("dashboards/zabbix_monitoring.html", summary=summary, problems=problems)


@bp.route("/pillars/<string:pillar_id>")
@login_required
@require_role("admin", "gestor", "visualizador")
def pillar_detail(pillar_id: str) -> str:
    """Render drill-down page for a single pillar.

    Args:
        pillar_id: One of the five pillar identifiers.
    """
    if pillar_id not in _VALID_PILLAR_IDS:
        abort(404)

    data = _get_governance()
    pillar = next(
        (p for p in data.get("pillars", []) if p["id"] == pillar_id),
        None,
    )
    if pillar is None:
        abort(404)

    return render_template("dashboards/pillar_detail.html", pillar=pillar, governance=data)
