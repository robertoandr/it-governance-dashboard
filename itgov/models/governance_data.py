"""Modelos Pydantic para o pilar Dados (DLP/Sensitivity Labels) — Governança de TI."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SensitivityLabelInfo(BaseModel):
    """Um sensitivity label publicado no tenant."""

    label_id: str
    name: str
    description: str | None = None
    is_active: bool = True


class DataGovernanceSummary(BaseModel):
    """Resumo de governança de dados (sensitivity labels).

    ``total_labels == 0`` é um achado de governança válido — significa que
    o tenant não tem estratégia de classificação de dados via sensitivity
    labels configurada, não um erro de coleta.
    """

    total_labels: int = Field(ge=0)
    labels: list[SensitivityLabelInfo] = Field(default_factory=list)
