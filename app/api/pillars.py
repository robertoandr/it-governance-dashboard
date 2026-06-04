"""Flask-RESTX namespace for /api/pillars endpoints."""

from __future__ import annotations

from typing import Any

import structlog
from flask_restx import Namespace, Resource

from app.api.dashboards import _get_cached_or_compute

log = structlog.get_logger(__name__)

ns = Namespace("pillars", description="Individual governance pillar scores")

_VALID_PILLAR_IDS = {
    "strategic_alignment",
    "value_delivery",
    "risk_management",
    "resource_management",
    "performance_measure",
}


@ns.route("")
class PillarListResource(Resource):
    """GET /api/pillars — list all pillar scores."""

    @ns.doc("list_pillars")
    def get(self) -> list[dict[str, Any]]:
        """Return a list of all five governance pillars."""
        data = _get_cached_or_compute()
        return data.get("pillars", [])


@ns.route("/<string:pillar_id>")
@ns.param("pillar_id", "Pillar identifier (e.g. risk_management)")
class PillarDetailResource(Resource):
    """GET /api/pillars/<pillar_id> — single pillar detail."""

    @ns.doc("get_pillar")
    def get(self, pillar_id: str) -> tuple[dict[str, Any], int]:
        """Return a single pillar by its ID.

        Args:
            pillar_id: One of the five pillar identifiers.

        Returns:
            Pillar data dict or 404 error.
        """
        if pillar_id not in _VALID_PILLAR_IDS:
            log.warning("pillar_not_found", pillar_id=pillar_id)
            return {"error": f"Pillar '{pillar_id}' not found"}, 404

        data = _get_cached_or_compute()
        pillars: list[dict[str, Any]] = data.get("pillars", [])
        for pillar in pillars:
            if pillar.get("id") == pillar_id:
                return pillar, 200

        return {"error": f"Pillar '{pillar_id}' not found"}, 404
