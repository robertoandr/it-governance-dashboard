"""Flask-RESTX namespace for /api/overview endpoint."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import structlog
from flask_restx import Namespace, Resource, fields

from app.services.metrics_aggregator import MetricsAggregator

log = structlog.get_logger(__name__)

ns = Namespace("overview", description="Governance overview score")

_component_model = ns.model(
    "ComponentMetric",
    {
        "id": fields.String(required=True),
        "label": fields.String(required=True),
        "value": fields.Float(required=True),
        "raw_value": fields.Float(allow_null=True),
        "unit": fields.String,
        "source": fields.String,
        "weight": fields.Float,
        "trend": fields.String,
    },
)

_pillar_model = ns.model(
    "PillarScore",
    {
        "id": fields.String(required=True),
        "label": fields.String(required=True),
        "score": fields.Float(required=True),
        "weight": fields.Float(required=True),
        "color": fields.String,
        "status": fields.String,
        "trend": fields.String,
        "previous_score": fields.Float(allow_null=True),
        "components": fields.List(fields.Nested(_component_model)),
        "data_source": fields.String(description="live | manual | partial | coming_soon"),
        "last_collected": fields.String(allow_null=True, description="ISO 8601 timestamp of last real data"),
        "collector_eta": fields.String(allow_null=True, description="When real data is expected"),
    },
)

_overview_model = ns.model(
    "GovernanceScore",
    {
        "global_score": fields.Float(required=True),
        "status": fields.String(required=True),
        "trend": fields.String,
        "computed_at": fields.String,
        "pillars": fields.List(fields.Nested(_pillar_model)),
    },
)

# In-memory cache: {"data": dict, "expires_at": float}
_cache: dict[str, Any] = {}
_TTL_SECONDS = 25


def _get_cached_or_compute() -> dict[str, Any]:
    now = time.monotonic()
    if _cache.get("expires_at", 0) > now and _cache.get("data") is not None:
        log.debug("overview_cache_hit")
        return _cache["data"]  # type: ignore[return-value]

    aggregator = MetricsAggregator()
    governance = asyncio.run(aggregator.calculate_full_score())
    data: dict[str, Any] = governance.model_dump(mode="json")
    _cache["data"] = data
    _cache["expires_at"] = now + _TTL_SECONDS
    log.info("overview_cache_refreshed", ttl=_TTL_SECONDS)
    return data


@ns.route("")
class OverviewResource(Resource):
    """GET /api/overview — returns the full governance score."""

    @ns.doc("get_overview")
    @ns.marshal_with(_overview_model, code=200)
    def get(self) -> dict[str, Any]:
        """Return current global governance score with all pillar details."""
        return _get_cached_or_compute()
