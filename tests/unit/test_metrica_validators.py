"""Unit tests for SLA Metric Pydantic validators."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.metrica import SlaPointCreate, SlaQueryParams

_VALID_UUID = "00000000-0000-0000-0000-000000000001"

_BASE = {
    "contrato_id": _VALID_UUID,
    "fornecedor_id": _VALID_UUID,
    "criticidade": "alta",
    "uptime_pct": 99.5,
    "incidentes": 0,
    "tempo_indisponivel_min": 0,
}


# ---------------------------------------------------------------------------
# SlaPointCreate — field-level validators
# ---------------------------------------------------------------------------
class TestSlaPointCreate:
    def test_valid_payload_accepted(self) -> None:
        p = SlaPointCreate.model_validate(_BASE)
        assert p.uptime_pct == 99.5
        assert p.criticidade == "alta"
        assert p.timestamp is None

    def test_invalid_criticidade_raises(self) -> None:
        with pytest.raises(ValidationError, match="criticidade deve ser um de"):
            SlaPointCreate.model_validate({**_BASE, "criticidade": "urgente"})

    def test_uptime_above_100_raises(self) -> None:
        with pytest.raises(ValidationError):
            SlaPointCreate.model_validate({**_BASE, "uptime_pct": 100.1})

    def test_uptime_negative_raises(self) -> None:
        with pytest.raises(ValidationError):
            SlaPointCreate.model_validate({**_BASE, "uptime_pct": -1.0})

    def test_incidentes_negative_raises(self) -> None:
        with pytest.raises(ValidationError):
            SlaPointCreate.model_validate({**_BASE, "incidentes": -1})

    def test_tempo_indisponivel_negative_raises(self) -> None:
        with pytest.raises(ValidationError):
            SlaPointCreate.model_validate({**_BASE, "tempo_indisponivel_min": -1})

    def test_uptime_zero_accepted(self) -> None:
        p = SlaPointCreate.model_validate({**_BASE, "uptime_pct": 0.0, "incidentes": 5, "tempo_indisponivel_min": 60})
        assert p.uptime_pct == 0.0

    def test_timestamp_optional_defaults_none(self) -> None:
        p = SlaPointCreate.model_validate(_BASE)
        assert p.timestamp is None

    def test_explicit_timestamp_accepted(self) -> None:
        p = SlaPointCreate.model_validate(
            {**_BASE, "timestamp": "2026-05-01T00:00:00Z"}
        )
        assert p.timestamp is not None

    # cross-field: 100 % uptime + incidents must fail
    def test_perfect_uptime_with_incidents_raises(self) -> None:
        with pytest.raises(ValidationError, match="inconsistente"):
            SlaPointCreate.model_validate(
                {**_BASE, "uptime_pct": 100.0, "incidentes": 1, "tempo_indisponivel_min": 0}
            )

    def test_perfect_uptime_with_downtime_raises(self) -> None:
        with pytest.raises(ValidationError, match="inconsistente"):
            SlaPointCreate.model_validate(
                {**_BASE, "uptime_pct": 100.0, "incidentes": 0, "tempo_indisponivel_min": 5}
            )

    def test_perfect_uptime_no_incidents_accepted(self) -> None:
        p = SlaPointCreate.model_validate(
            {**_BASE, "uptime_pct": 100.0, "incidentes": 0, "tempo_indisponivel_min": 0}
        )
        assert p.uptime_pct == 100.0


# ---------------------------------------------------------------------------
# SlaQueryParams
# ---------------------------------------------------------------------------
class TestSlaQueryParams:
    def test_defaults_accepted(self) -> None:
        p = SlaQueryParams.model_validate({})
        assert p.range == "7d"
        assert p.window == "1h"

    def test_valid_combinations(self) -> None:
        for r in ("1d", "7d", "30d", "90d"):
            for w in ("1h", "6h", "1d"):
                p = SlaQueryParams.model_validate({"range": r, "window": w})
                assert p.range == r
                assert p.window == w

    def test_invalid_range_raises(self) -> None:
        with pytest.raises(ValidationError, match="range deve ser um de"):
            SlaQueryParams.model_validate({"range": "3d"})

    def test_invalid_window_raises(self) -> None:
        with pytest.raises(ValidationError, match="window deve ser um de"):
            SlaQueryParams.model_validate({"window": "15m"})
