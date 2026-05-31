"""Modelos Pydantic v2 para integração com Zendesk API."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class TicketStatus(StrEnum):
    NEW = "new"
    OPEN = "open"
    PENDING = "pending"
    HOLD = "hold"
    SOLVED = "solved"
    CLOSED = "closed"


class TicketPriority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class Ticket(BaseModel):
    """Ticket do Zendesk."""

    id: int
    subject: str
    status: TicketStatus
    priority: TicketPriority | None = None
    created_at: datetime
    updated_at: datetime
    assignee_id: int | None = None
    requester_id: int
    tags: list[str] = Field(default_factory=list)

    @field_validator("priority", mode="before")
    @classmethod
    def coerce_priority(cls, v: str | None) -> TicketPriority | None:
        if v is None:
            return None
        try:
            return TicketPriority(v)
        except ValueError:
            return TicketPriority.NORMAL

    @property
    def is_open(self) -> bool:
        return self.status in (TicketStatus.NEW, TicketStatus.OPEN, TicketStatus.PENDING)

    @property
    def age_hours(self) -> float:
        """Age in hours since ticket creation. Naive timestamps assumed UTC."""
        created = (
            self.created_at.replace(tzinfo=UTC) if self.created_at.tzinfo is None else self.created_at.astimezone(UTC)
        )
        return (datetime.now(UTC) - created).total_seconds() / 3600


class SLAPolicy(BaseModel):
    """Política de SLA associada a um ticket."""

    policy_id: int
    policy_title: str
    metric: str = Field(description="Ex: first_reply_time, next_reply_time")
    breach_at: datetime | None = None
    breached: bool = False


class SLAMetric(BaseModel):
    """Métricas de SLA agregadas."""

    total_tickets: int
    breached: int
    compliance_pct: float = Field(ge=0.0, le=100.0)
    avg_first_reply_minutes: float | None = None


class SatisfactionRating(BaseModel):
    """Avaliação de satisfação (CSAT) de um ticket."""

    ticket_id: int
    score: str = Field(description="good | bad | unoffered")
    comment: str | None = None
    created_at: datetime


class CSATSummary(BaseModel):
    """Resumo de satisfação do cliente."""

    total_ratings: int
    good: int
    bad: int
    csat_pct: float = Field(ge=0.0, le=100.0)
