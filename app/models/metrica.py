"""Pydantic schemas for the SLA Metrics resource."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Validation constants
# ---------------------------------------------------------------------------
CRITICIDADE_VALIDAS = frozenset({"baixa", "media", "alta", "critica"})

RANGE_VALIDOS = frozenset({"1d", "7d", "30d", "90d"})
WINDOW_VALIDOS = frozenset({"1h", "6h", "1d"})


# ---------------------------------------------------------------------------
# Ingestion (POST)
# ---------------------------------------------------------------------------
class SlaPointCreate(BaseModel):
    """Input schema for ingesting one SLA measurement point."""

    contrato_id: uuid.UUID
    fornecedor_id: uuid.UUID
    criticidade: str
    uptime_pct: Annotated[float, Field(ge=0.0, le=100.0)]
    incidentes: Annotated[int, Field(ge=0)]
    tempo_indisponivel_min: Annotated[int, Field(ge=0)]
    timestamp: datetime | None = None

    @field_validator("criticidade")
    @classmethod
    def validate_criticidade(cls, v: str) -> str:
        """Ensure criticidade is one of the allowed values.

        Args:
            v: Raw criticidade string.

        Returns:
            str: Validated criticidade.

        Raises:
            ValueError: If value is not in CRITICIDADE_VALIDAS.
        """
        if v not in CRITICIDADE_VALIDAS:
            raise ValueError(f"criticidade deve ser um de: {sorted(CRITICIDADE_VALIDAS)}")
        return v

    @model_validator(mode="after")
    def validate_consistency(self) -> SlaPointCreate:
        """Validate that downtime is consistent with uptime.

        A 100% uptime reading must not have downtime minutes or incidents.

        Returns:
            SlaPointCreate: Self after validation.

        Raises:
            ValueError: If uptime is 100% but downtime_min or incidents > 0.
        """
        if self.uptime_pct == 100.0 and (
            self.tempo_indisponivel_min > 0 or self.incidentes > 0
        ):
            raise ValueError(
                "uptime_pct 100% é inconsistente com incidentes ou tempo_indisponivel_min > 0"
            )
        return self


# ---------------------------------------------------------------------------
# Timeseries query params (GET /metricas/sla/{contrato_id})
# ---------------------------------------------------------------------------
class SlaQueryParams(BaseModel):
    """Query parameters for the SLA timeseries endpoint."""

    range: str = "7d"
    window: str = "1h"

    @field_validator("range")
    @classmethod
    def validate_range(cls, v: str) -> str:
        """Validate range duration string.

        Args:
            v: Duration string.

        Returns:
            str: Validated range.

        Raises:
            ValueError: If not in RANGE_VALIDOS.
        """
        if v not in RANGE_VALIDOS:
            raise ValueError(f"range deve ser um de: {sorted(RANGE_VALIDOS)}")
        return v

    @field_validator("window")
    @classmethod
    def validate_window(cls, v: str) -> str:
        """Validate window aggregation string.

        Args:
            v: Window duration string.

        Returns:
            str: Validated window.

        Raises:
            ValueError: If not in WINDOW_VALIDOS.
        """
        if v not in WINDOW_VALIDOS:
            raise ValueError(f"window deve ser um de: {sorted(WINDOW_VALIDOS)}")
        return v


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------
class SlaSeriesPoint(BaseModel):
    """One data point in a SLA timeseries response."""

    model_config = ConfigDict(from_attributes=True)

    timestamp: datetime
    uptime_pct: float


class SlaSeriesResponse(BaseModel):
    """Response for GET /metricas/sla/{contrato_id}."""

    contrato_id: uuid.UUID
    range: str
    window: str
    uptime_medio_pct: float | None
    points: list[SlaSeriesPoint]


class SlaRiscoItem(BaseModel):
    """One entry in the dashboard risk ranking."""

    contrato_id: uuid.UUID
    numero_contrato: str
    fornecedor_nome: str
    criticidade: str
    uptime_atual_pct: float
    uptime_contratado_pct: float
    deficit_pct: float


class DashboardResponse(BaseModel):
    """Response for GET /metricas/dashboard."""

    uptime_medio_pct: float
    contratos_em_risco: int
    total_contratos_monitorados: int
    top5_piores_slas: list[SlaRiscoItem]
    range: str
    gerado_em: datetime
