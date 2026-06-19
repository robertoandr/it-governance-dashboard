"""Flask-RESTX namespace para /api/intune — Intune Patch Compliance."""

from __future__ import annotations

import json

import structlog
from flask import make_response
from flask_restx import Namespace, Resource

log = structlog.get_logger(__name__)

ns = Namespace("intune", description="Intune patch compliance metrics")

_CACHE_MAX_AGE = 60  # coleta é de 6h; 60s evita martelar o Influx no auto-refresh


def _provider():
    from app.services.influxdb_provider import InfluxDBMetricsProvider

    return InfluxDBMetricsProvider()


@ns.route("/patch-compliance")
class IntunePatchComplianceResource(Resource):
    """GET /api/intune/patch-compliance — compliance global + breakdown por OS."""

    @ns.doc(
        "get_intune_patch_compliance",
        description=(
            "Retorna compliance de patches Intune agregado e por SO. "
            "HTTP 206 quando dado tem mais de 15h (stale=true no body). "
            "Nunca retorna 500 por staleness — dado velho não é erro de servidor."
        ),
        responses={
            200: "Dado fresco (< 15h)",
            206: "Dado stale (> 15h sem coleta) — body válido, usar com cautela",
            503: "InfluxDB indisponível",
        },
    )
    def get(self):
        """Compliance de patches Intune: global + por OS."""
        try:
            data = _provider()._intune_stats()
        except Exception:
            log.exception("intune_patch_compliance_failed")
            return {"error": "influxdb_unavailable"}, 503

        status = 206 if data.get("stale") else 200
        resp = make_response(json.dumps(data), status)
        resp.headers["Content-Type"] = "application/json"
        resp.headers["Cache-Control"] = f"max-age={_CACHE_MAX_AGE}, private"
        return resp
