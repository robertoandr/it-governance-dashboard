"""Flask-RESTX namespace for Kubernetes liveness and readiness probes.

Liveness  → GET /api/health         (never checks dependencies — process-only)
Readiness → GET /api/health/ready   (checks all configured dependencies)

Usage (registered in app factory via ``api.add_namespace(ns, path="/health")``).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import structlog
from flask_restx import Namespace, Resource, fields

from app.config import get_settings
from app.services.health_checker import CheckResult, HealthChecker

log = structlog.get_logger(__name__)

ns = Namespace("health", description="Liveness & Readiness probes")

# ── Flask-RESTX models (Swagger documentation) ────────────────────────────────

liveness_model = ns.model(
    "Liveness",
    {
        "status": fields.String(description="Always 'alive' when the process responds"),
        "timestamp": fields.String(description="ISO 8601 UTC timestamp"),
    },
)

readiness_model = ns.model(
    "Readiness",
    {
        "status": fields.String(description="'ready' or 'not_ready'"),
        "version": fields.String(description="Application version"),
        "checks": fields.Raw(description="Per-dependency check results"),
        "timestamp": fields.String(description="ISO 8601 UTC timestamp"),
    },
)


# ── Resources ─────────────────────────────────────────────────────────────────


@ns.route("")
class LivenessProbe(Resource):
    """Kubernetes liveness probe.

    Returns 200 if the Flask process is alive and responding.
    MUST NOT check external dependencies — dependency failures must not
    trigger a container restart, only a traffic exclusion (readiness).
    """

    @ns.marshal_with(liveness_model)
    def get(self) -> dict[str, object]:
        """Returns 200/alive whenever the process can handle requests."""
        ts = datetime.now(UTC).isoformat(timespec="seconds")
        log.info("liveness_probe_ok", timestamp=ts)
        return {"status": "alive", "timestamp": ts}


@ns.route("/ready")
class ReadinessProbe(Resource):
    """Kubernetes readiness probe.

    Returns 200 when all configured dependencies are reachable.
    Returns 503 when any dependency fails — removing the pod from
    the Service's endpoint list until dependencies recover.
    """

    @ns.response(200, "All dependencies healthy", readiness_model)
    @ns.response(503, "One or more dependencies unavailable", readiness_model)
    def get(self) -> tuple[dict[str, object], int]:
        """Runs parallel dependency checks and returns aggregate health status."""
        ts = datetime.now(UTC).isoformat(timespec="seconds")
        checker = HealthChecker()
        results: dict[str, CheckResult] = asyncio.run(checker.check_all(timeout=2.0))

        checks_payload: dict[str, dict[str, object]] = {}
        for name, r in results.items():
            entry: dict[str, object] = {"ok": r.ok, "latency_ms": round(r.latency_ms, 2), "error": r.error}
            if r.extra:
                entry.update(r.extra)
            checks_payload[name] = entry

        all_ok = all(r.ok for r in results.values())
        status = "ready" if all_ok else "not_ready"
        http_code = 200 if all_ok else 503
        version = get_settings().app.version

        log.info("readiness_probe", status=status, checks=checks_payload, timestamp=ts)

        return {"status": status, "version": version, "checks": checks_payload, "timestamp": ts}, http_code
