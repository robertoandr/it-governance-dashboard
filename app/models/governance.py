"""Pydantic models for governance domain."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class DataSource(StrEnum):
    """Reliability of the data backing a governance pillar."""

    LIVE = "live"
    MANUAL = "manual"
    PARTIAL = "partial"
    COMING_SOON = "coming_soon"


class PillarID(StrEnum):
    """Identifiers for the five COBIT-aligned governance pillars."""

    STRATEGIC_ALIGNMENT = "strategic_alignment"
    VALUE_DELIVERY = "value_delivery"
    RISK_MANAGEMENT = "risk_management"
    RESOURCE_MANAGEMENT = "resource_management"
    PERFORMANCE_MEASURE = "performance_measure"


PILLAR_META: dict[PillarID, dict[str, Any]] = {
    PillarID.STRATEGIC_ALIGNMENT: {
        "label": "Alinhamento Estratégico",
        "weight": 0.15,
        "color": "#3B82F6",
        "order": 1,
    },
    PillarID.VALUE_DELIVERY: {
        "label": "Entrega de Valor",
        "weight": 0.20,
        "color": "#10B981",
        "order": 2,
    },
    PillarID.RISK_MANAGEMENT: {
        "label": "Gestão de Riscos",
        "weight": 0.25,
        "color": "#EF4444",
        "order": 3,
    },
    PillarID.RESOURCE_MANAGEMENT: {
        "label": "Gestão de Recursos",
        "weight": 0.15,
        "color": "#F59E0B",
        "order": 4,
    },
    PillarID.PERFORMANCE_MEASURE: {
        "label": "Mensuração de Desempenho",
        "weight": 0.25,
        "color": "#8B5CF6",
        "order": 5,
    },
}


class ComponentMetric(BaseModel):
    """A single measurable metric contributing to a pillar score."""

    id: str
    label: str
    value: float = Field(ge=0.0, le=100.0)
    raw_value: float | None = None
    unit: str = "%"
    source: str = "coming_soon"
    weight: float = Field(default=1.0, gt=0.0)
    trend: str = "stable"


class PillarScore(BaseModel):
    """Computed score for a single governance pillar."""

    id: PillarID
    label: str
    score: float = Field(ge=0.0, le=100.0)
    weight: float
    color: str
    status: str
    trend: str
    components: list[ComponentMetric] = Field(default_factory=list)
    previous_score: float | None = None
    data_source: DataSource = DataSource.COMING_SOON
    last_collected: datetime | None = None
    collector_eta: str | None = None

    @field_validator("score", mode="before")
    @classmethod
    def clamp_score(cls, v: float) -> float:
        return max(0.0, min(100.0, float(v)))


class EntraIdMetrics(BaseModel):
    """Snapshot of Microsoft Entra ID identity metrics."""

    total_users: int
    guest_users: int
    mfa_enabled_pct: float
    stale_accounts_90d: int
    privileged_roles_count: int
    ca_policies_count: int
    collected_at: datetime


class PmoManualInput(BaseModel):
    """Manually entered PMO governance score."""

    score: float = Field(ge=0.0, le=100.0)
    notes: str = ""
    updated_by: str = ""
    updated_at: datetime | None = None


class GovernanceScore(BaseModel):
    """Full governance score aggregating all five pillars."""

    global_score: float = Field(ge=0.0, le=100.0)
    status: str
    trend: str
    pillars: list[PillarScore] = Field(default_factory=list)
    computed_at: str = ""

    @field_validator("global_score", mode="before")
    @classmethod
    def clamp_global(cls, v: float) -> float:
        return max(0.0, min(100.0, float(v)))
