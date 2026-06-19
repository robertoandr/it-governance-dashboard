"""Modelos Pydantic para o pilar Dispositivos — Governança de TI."""

from __future__ import annotations

from pydantic import BaseModel, Field


class DeviceSummary(BaseModel):
    """Resumo de governança de dispositivos (Entra ID device objects).

    ``managed_pct`` é ``None`` quando nenhum dispositivo tem ``isManaged``
    preenchido (true ou false) — este tenant não usa Intune para gestão,
    então "sem dado" não deve ser confundido com "0% gerenciado".
    """

    total_devices: int = Field(ge=0)
    stale_90d: int = Field(ge=0, description="Sem sign-in nos últimos 90 dias")
    managed_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    os_distribution: dict[str, int] = Field(default_factory=dict)
    trust_type_distribution: dict[str, int] = Field(default_factory=dict)
