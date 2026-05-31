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
    """Resumo de satisfação do cliente.

    ``csat_pct`` is ``None`` when ``sample_size == 0`` (no answered surveys),
    distinguishing "no data" from "0% satisfaction". Consumers should render
    ``None`` as "N/A" rather than "0%".
    """

    total_ratings: int
    good: int
    bad: int
    csat_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    sample_size: int = Field(default=0, ge=0, description="Answered surveys (good + bad) in the window")
