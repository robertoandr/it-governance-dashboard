"""Modelos Pydantic v2 para integração com Zendesk API."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import AliasChoices, BaseModel, Field, field_validator


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


class SLAStage(StrEnum):
    """Estágio de uma métrica de SLA segundo o Zendesk (``ticket.slas``)."""

    ACTIVE = "active"
    PAUSED = "paused"
    ACHIEVED = "achieved"


class TicketSLAMetric(BaseModel):
    """Estado de uma métrica de SLA de um ticket, calculado pelo Zendesk.

    Vem do sideload ``include=tickets(slas)``. ``breach_at`` já considera o
    horário comercial e as pausas definidos na política do tenant.
    """

    metric: str
    stage: SLAStage
    breach_at: datetime | None = None


class TicketMetricSet(BaseModel):
    """Métricas de tempo de um ticket (sideload ``metric_sets``), em minutos úteis.

    ``reply_business_minutes`` é o tempo até a 1ª resposta pública;
    ``requester_wait_business_minutes`` é o tempo total em que o solicitante
    esperou pelo time (exclui pending/hold) — a métrica de resolução do SLA.
    """

    reply_business_minutes: int | None = None
    requester_wait_business_minutes: int | None = None
    solved_at: datetime | None = None

    @classmethod
    def from_api(cls, raw: dict) -> TicketMetricSet:
        """Constrói a partir do objeto ``ticket_metric`` da API do Zendesk."""
        return cls(
            reply_business_minutes=(raw.get("reply_time_in_minutes") or {}).get("business"),
            requester_wait_business_minutes=(raw.get("requester_wait_time_in_minutes") or {}).get("business"),
            solved_at=raw.get("solved_at"),
        )


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
    group_id: int | None = None
    tags: list[str] = Field(default_factory=list)
    # A API entrega como ``slas`` (sideload ``include=tickets(slas)``).
    sla_metrics: list[TicketSLAMetric] = Field(
        default_factory=list, validation_alias=AliasChoices("slas", "sla_metrics")
    )
    metric_set: TicketMetricSet | None = None

    @field_validator("sla_metrics", mode="before")
    @classmethod
    def unwrap_slas(cls, v: object) -> object:
        """Aceita o formato da API (``{"policy_metrics": [...]}``) ou a lista direta."""
        if isinstance(v, dict):
            return v.get("policy_metrics") or []
        return v or []

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


class SLATarget(BaseModel):
    """Metas de SLA de uma prioridade, em minutos úteis."""

    first_reply_minutes: int = Field(gt=0)
    resolution_minutes: int = Field(gt=0)


class SLATargets(BaseModel):
    """Metas de SLA por prioridade e de onde vieram.

    ``source`` é ``"zendesk_policy"`` quando lidas da política configurada no
    Zendesk, ou ``"itil_default"`` quando o tenant não tem política.
    """

    by_priority: dict[str, SLATarget]
    source: str
    policy_name: str | None = None

    def for_priority(self, priority: str | None) -> SLATarget:
        """Retorna a meta da prioridade (tickets sem prioridade contam como ``normal``)."""
        return self.by_priority.get(priority or "normal") or self.by_priority["normal"]


class SLAMetric(BaseModel):
    """Métricas de SLA agregadas de uma janela de tickets.

    ``compliance_pct`` é ``None`` quando nenhum ticket pôde ser avaliado —
    "sem dados" não é "100% no prazo". Tickets ``unknown`` ficam fora do
    denominador (ver contrato 2 em ``zendesk_service``).
    """

    total_tickets: int
    breached: int
    compliance_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    avg_first_reply_minutes: float | None = None
    unknown: int = 0
    first_reply_compliance_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    resolution_compliance_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    window_days: int | None = None


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
