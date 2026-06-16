"""HTML view routes for the governance dashboard."""

from __future__ import annotations

import asyncio

import structlog
from flask import Blueprint, abort, render_template

from app.services.metrics_aggregator import MetricsAggregator

log = structlog.get_logger(__name__)

bp = Blueprint("dashboards", __name__, template_folder="../templates")


@bp.context_processor
def _inject_globals() -> dict:
    """Provide app_version and environment when running inside legacy app.py."""
    from flask import current_app

    return {
        "app_version": current_app.config.get("APP_VERSION", "1.1.0"),
        "app_name": current_app.config.get("APP_NAME", "IT Governance Dashboard"),
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


@bp.route("/")
def overview() -> str:
    """Render governance overview dashboard."""
    data = _get_governance()
    return render_template("dashboards/overview.html", governance=data)


@bp.route("/pillars")
def pillars() -> str:
    """Render all pillars detail page."""
    data = _get_governance()
    return render_template("dashboards/pillars.html", governance=data)


@bp.route("/zendesk")
def zendesk_mttr() -> str:
    """Render Zendesk MTTR / suporte dashboard.

    Reusa o cache em memória de itgov.api.v1.zendesk (TTL 120s) em vez de
    paginar o histórico completo do Zendesk a cada carregamento — sem isso,
    a página leva ~20s (centenas de páginas na API) a cada request.
    """
    import config
    from itgov.api.v1.zendesk import get_cached_mttr_summary, get_cached_volume_by_status

    if not config.ZENDESK_ENABLED:
        abort(404)

    mttr = get_cached_mttr_summary()
    volume = get_cached_volume_by_status()

    return render_template(
        "dashboards/zendesk_mttr.html",
        mttr=mttr,
        volume=volume,
    )


@bp.route("/governance/devices")
def governance_devices() -> str:
    """Render pilar Dispositivos (Governança M365)."""
    import config
    from itgov.api.v1.governance_devices import get_cached_device_summary

    if not config.GRAPH_ENABLED:
        abort(404)

    try:
        summary = get_cached_device_summary()
    except RuntimeError:
        abort(503)

    return render_template("dashboards/governance_devices.html", summary=summary)


@bp.route("/governance/apps")
def governance_apps() -> str:
    """Render pilar Aplicativos (Governança M365)."""
    import config
    from itgov.api.v1.governance_apps import get_cached_app_summary

    if not config.GRAPH_ENABLED:
        abort(404)

    try:
        summary = get_cached_app_summary()
    except RuntimeError:
        abort(503)

    return render_template("dashboards/governance_apps.html", summary=summary)


@bp.route("/pillars/<string:pillar_id>")
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
