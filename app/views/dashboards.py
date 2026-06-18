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
