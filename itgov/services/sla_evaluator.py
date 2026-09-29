"""Avaliação de SLA de tickets Zendesk — funções puras, sem I/O.

Modelo de SLA (padrão ITIL / service desk)
------------------------------------------

Cada ticket é avaliado em duas métricas, ambas em **horário comercial**:

* **1ª resposta** (``first_reply_time``) — tempo até a primeira resposta
  pública do time.
* **Resolução** (``requester_wait_time``) — tempo em que o solicitante
  esperou pelo time. O relógio **pausa** em ``pending``/``hold`` (aguardando
  o solicitante ou terceiros), então o time não é penalizado por espera que
  não depende dele.

As metas vêm da política de SLA configurada no Zendesk. Sem política, usamos
metas ITIL de mercado (``ITIL_DEFAULT_TARGETS``).

Fontes de dados, em ordem de confiança:

1. Métrica **concluída** → minutos úteis medidos pelo Zendesk
   (``metric_sets``) comparados com a meta.
2. Métrica **em andamento** → ``breach_at`` calculado pelo Zendesk
   (``slas``), que já considera horário comercial, feriados e pausas.
3. Sem nenhum dos dois → ``unknown`` (fica fora do denominador — contrato 2
   de ``zendesk_service``). Exceção: com metas ITIL padrão, um ticket cujo
   tempo corrido ainda está abaixo da meta está comprovadamente no prazo,
   pois tempo útil <= tempo corrido.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from itgov.models.zendesk import (
    SLAMetric,
    SLAStage,
    SLATarget,
    SLATargets,
    Ticket,
    TicketStatus,
)

FIRST_REPLY_METRIC = "first_reply_time"
RESOLUTION_METRIC = "requester_wait_time"

SOURCE_POLICY = "zendesk_policy"
SOURCE_ITIL_DEFAULT = "itil_default"

# Metas típicas de service desk ITIL (P1..P4), em minutos úteis.
ITIL_DEFAULT_TARGETS: dict[str, SLATarget] = {
    "urgent": SLATarget(first_reply_minutes=30, resolution_minutes=4 * 60),
    "high": SLATarget(first_reply_minutes=60, resolution_minutes=8 * 60),
    "normal": SLATarget(first_reply_minutes=4 * 60, resolution_minutes=24 * 60),
    "low": SLATarget(first_reply_minutes=8 * 60, resolution_minutes=40 * 60),
}

_SOLVED = (TicketStatus.SOLVED, TicketStatus.CLOSED)


class SLAStatus(StrEnum):
    """Resultado de uma métrica: três estados, nunca booleano."""

    OK = "ok"
    BREACHED = "breached"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class TicketSLAResult:
    """Resultado de SLA de um ticket nas duas métricas."""

    ticket_id: int
    first_reply: SLAStatus
    resolution: SLAStatus

    @property
    def overall(self) -> SLAStatus:
        """``breached`` se alguma métrica estourou; ``unknown`` só se nenhuma foi avaliada."""
        statuses = (self.first_reply, self.resolution)
        if SLAStatus.BREACHED in statuses:
            return SLAStatus.BREACHED
        if SLAStatus.OK in statuses:
            return SLAStatus.OK
        return SLAStatus.UNKNOWN


def default_targets() -> SLATargets:
    """Metas ITIL de mercado, usadas quando o tenant não tem política de SLA."""
    return SLATargets(by_priority=dict(ITIL_DEFAULT_TARGETS), source=SOURCE_ITIL_DEFAULT)


def targets_from_policies(policies: list[dict[str, Any]]) -> SLATargets | None:
    """Extrai metas da política de SLA de maior precedência do Zendesk.

    O Zendesk aplica a primeira política (menor ``position``) cujo filtro
    casa com o ticket. Para métricas em andamento usamos o ``breach_at`` do
    próprio ticket, que já reflete a política correta; estas metas servem
    para avaliar métricas concluídas. Prioridades ou métricas ausentes na
    política herdam o padrão ITIL.

    Args:
        policies: Lista ``sla_policies`` de ``/api/v2/slas/policies.json``.

    Returns:
        SLATargets com ``source="zendesk_policy"``, ou None se não houver política.
    """
    if not policies:
        return None
    policy = min(policies, key=lambda p: p.get("position") or 0)

    found: dict[str, dict[str, int]] = {}
    for m in policy.get("policy_metrics") or []:
        if m.get("metric") in (FIRST_REPLY_METRIC, RESOLUTION_METRIC) and m.get("target"):
            found.setdefault(m["priority"], {})[m["metric"]] = int(m["target"])

    by_priority: dict[str, SLATarget] = {}
    for prio, fallback in ITIL_DEFAULT_TARGETS.items():
        metrics = found.get(prio, {})
        by_priority[prio] = SLATarget(
            first_reply_minutes=metrics.get(FIRST_REPLY_METRIC, fallback.first_reply_minutes),
            resolution_minutes=metrics.get(RESOLUTION_METRIC, fallback.resolution_minutes),
        )
    return SLATargets(by_priority=by_priority, source=SOURCE_POLICY, policy_name=policy.get("title"))


def _as_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _evaluate_metric(
    ticket: Ticket,
    metric: str,
    measured_minutes: int | None,
    target_minutes: int,
    targets: SLATargets,
    now: datetime,
) -> SLAStatus:
    if measured_minutes is not None:
        return SLAStatus.OK if measured_minutes <= target_minutes else SLAStatus.BREACHED

    running = next(
        (m for m in ticket.sla_metrics if m.metric == metric and m.stage in (SLAStage.ACTIVE, SLAStage.PAUSED)),
        None,
    )
    if running is not None and running.breach_at is not None:
        return SLAStatus.BREACHED if _as_utc(running.breach_at) <= now else SLAStatus.OK

    if targets.source == SOURCE_ITIL_DEFAULT and ticket.status not in _SOLVED:
        elapsed_minutes = (now - _as_utc(ticket.created_at)).total_seconds() / 60
        if elapsed_minutes <= target_minutes:
            return SLAStatus.OK
    return SLAStatus.UNKNOWN


def evaluate_ticket(ticket: Ticket, targets: SLATargets, now: datetime | None = None) -> TicketSLAResult:
    """Avalia um ticket nas métricas de 1ª resposta e resolução.

    Args:
        ticket: Ticket com ``sla_metrics`` e ``metric_set`` (sideloads) quando disponíveis.
        targets: Metas por prioridade.
        now: Instante de referência (UTC). Padrão: agora.

    Returns:
        TicketSLAResult com o estado de cada métrica.
    """
    now = now or datetime.now(UTC)
    target = targets.for_priority(ticket.priority)
    ms = ticket.metric_set

    reply_minutes = ms.reply_business_minutes if ms else None
    # O tempo de espera só é final depois de resolvido; antes disso vale o breach_at.
    wait_minutes = ms.requester_wait_business_minutes if ms and ticket.status in _SOLVED else None

    return TicketSLAResult(
        ticket_id=ticket.id,
        first_reply=_evaluate_metric(
            ticket, FIRST_REPLY_METRIC, reply_minutes, target.first_reply_minutes, targets, now
        ),
        resolution=_evaluate_metric(ticket, RESOLUTION_METRIC, wait_minutes, target.resolution_minutes, targets, now),
    )


def _compliance(statuses: list[SLAStatus]) -> float | None:
    ok = statuses.count(SLAStatus.OK)
    evaluated = ok + statuses.count(SLAStatus.BREACHED)
    return round(ok / evaluated * 100, 1) if evaluated else None


def summarize(
    tickets: list[Ticket],
    targets: SLATargets,
    now: datetime | None = None,
    window_days: int | None = None,
) -> SLAMetric:
    """Agrega o SLA de um conjunto de tickets.

    Args:
        tickets: Tickets a avaliar.
        targets: Metas por prioridade.
        now: Instante de referência (UTC). Padrão: agora.
        window_days: Janela representada pelos tickets, apenas informativo.

    Returns:
        SLAMetric com compliance geral e por métrica. Tickets ``unknown`` não
        entram no denominador.
    """
    now = now or datetime.now(UTC)
    results = [evaluate_ticket(t, targets, now) for t in tickets]
    overall = [r.overall for r in results]
    replies = [t.metric_set.reply_business_minutes for t in tickets if t.metric_set]
    replies = [r for r in replies if r is not None]

    return SLAMetric(
        total_tickets=len(tickets),
        breached=overall.count(SLAStatus.BREACHED),
        unknown=overall.count(SLAStatus.UNKNOWN),
        compliance_pct=_compliance(overall),
        first_reply_compliance_pct=_compliance([r.first_reply for r in results]),
        resolution_compliance_pct=_compliance([r.resolution for r in results]),
        avg_first_reply_minutes=round(sum(replies) / len(replies), 1) if replies else None,
        window_days=window_days,
    )
