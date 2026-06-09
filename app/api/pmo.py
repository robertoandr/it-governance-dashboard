"""Flask-RESTX namespace for /api/pmo/manual — PMO manual governance input."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from flask import request
from flask_restx import Namespace, Resource, fields

from app.models.governance import PmoManualInput

log = structlog.get_logger(__name__)

ns = Namespace("pmo", description="PMO manual governance score input")

_DATA_DIR = Path(os.environ.get("PMO_DATA_DIR", "/app/data"))
_PMO_FILE = _DATA_DIR / "pmo_manual_input.json"

_pmo_model = ns.model(
    "PmoManualInput",
    {
        "score": fields.Float(required=True, min=0.0, max=100.0, description="Strategic governance score 0-100"),
        "notes": fields.String(description="Justification notes"),
        "updated_by": fields.String(description="Email/name of the manager who set this"),
        "updated_at": fields.String(description="ISO 8601 timestamp"),
    },
)


def _read_pmo() -> PmoManualInput | None:
    try:
        if _PMO_FILE.exists():
            data = json.loads(_PMO_FILE.read_text())
            return PmoManualInput(**data)
    except Exception as exc:
        log.warning("pmo_read_failed", error=str(exc))
    return None


def _write_pmo(pmo: PmoManualInput) -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _PMO_FILE.write_text(pmo.model_dump_json())
    log.info("pmo_written", score=pmo.score, updated_by=pmo.updated_by)


@ns.route("/manual")
class PmoManualResource(Resource):
    """GET/PUT /api/pmo/manual — read or update the PMO manual score."""

    @ns.doc("get_pmo_manual")
    @ns.marshal_with(_pmo_model, code=200)
    def get(self) -> dict[str, Any]:
        """Return the current PMO manual score, or 404 if never set."""
        pmo = _read_pmo()
        if pmo is None:
            ns.abort(404, "No PMO manual input set yet")
        return pmo.model_dump(mode="json")

    @ns.doc("put_pmo_manual")
    @ns.expect(_pmo_model, validate=True)
    @ns.marshal_with(_pmo_model, code=200)
    def put(self) -> dict[str, Any]:
        """Set or update the PMO manual score (manager action)."""
        body = request.get_json() or {}
        try:
            pmo = PmoManualInput(
                score=float(body["score"]),
                notes=str(body.get("notes", "")),
                updated_by=str(body.get("updated_by", "")),
                updated_at=datetime.now(UTC),
            )
        except (KeyError, ValueError) as exc:
            ns.abort(400, f"Invalid input: {exc}")
        _write_pmo(pmo)
        return pmo.model_dump(mode="json")
