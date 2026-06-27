"""Modelos Pydantic para o pilar Compliance — Governança de TI."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RecomendacaoControle(BaseModel):
    """Controle do Secure Score com implementação baixa — ação recomendada."""

    control_name: str
    categoria: str
    descricao: str
    score_pct: float = Field(ge=0.0, le=100.0)


class ComplianceSummary(BaseModel):
    """Resumo de Compliance a partir do Microsoft Secure Score.

    ``pct`` é ``None`` quando não há dado coletado (Graph indisponível ou
    sem histórico) — não deve ser confundido com 0% de compliance.

    ``security_controls`` contém status dos controles de segurança específicos
    (Safe Links, Safe Attachments, Audit Log) quando disponíveis.
    """

    current_score: float | None = Field(default=None, ge=0.0)
    max_score: float | None = Field(default=None, ge=0.0)
    pct: float | None = Field(default=None, ge=0.0, le=100.0)
    category_breakdown: dict[str, float] = Field(default_factory=dict)
    recomendacoes: list[RecomendacaoControle] = Field(default_factory=list)
    security_controls: dict = Field(default_factory=dict)
