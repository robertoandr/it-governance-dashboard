"""Modelos Pydantic para o pilar Service Health — Governança M365."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ServiceStatus(BaseModel):
    service_id: str
    display_name: str
    status_raw: str
    status_label: str = Field(description="OK | ATENÇÃO | CRÍTICO")
    status_code: int = Field(description="0=OK, 1=atenção, 2=crítico")


class ServiceHealthSummary(BaseModel):
    total: int = Field(ge=0)
    ok: int = Field(ge=0)
    warning: int = Field(ge=0)
    critical: int = Field(ge=0)
    all_operational: bool
    services: list[ServiceStatus] = Field(default_factory=list)
