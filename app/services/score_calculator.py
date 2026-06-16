"""Governance score calculation engine."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog

from app.models.governance import (
    PILLAR_META,
    ComponentMetric,
    DataSource,
    GovernanceScore,
    PillarID,
    PillarScore,
)

log = structlog.get_logger(__name__)

THRESHOLD_OPERATIONAL: float = 85.0
THRESHOLD_DEGRADED: float = 60.0


def _status(score: float) -> str:
    if score >= THRESHOLD_OPERATIONAL:
        return "OPERACIONAL"
    if score >= THRESHOLD_DEGRADED:
        return "DEGRADADO"
    return "CRÍTICO"


def _trend(current: float, previous: float | None) -> str:
    if previous is None:
        return "stable"
    delta = current - previous
    if delta > 1.0:
        return "up"
    if delta < -1.0:
        return "down"
    return "stable"


class ScoreCalculator:
    """Calculates pillar and global governance scores."""

    def calculate_pillar(
        self,
        pillar_id: PillarID,
        components: list[dict[str, Any]],
        previous_score: float | None = None,
    ) -> PillarScore:
        """Compute weighted average score for a single pillar.

        Args:
            pillar_id: The pillar identifier.
            components: List of component dicts with at least 'value' and 'weight'.
            previous_score: Prior score for trend calculation.

        Returns:
            PillarScore with computed fields.
        """
        meta = PILLAR_META[pillar_id]
        parsed: list[ComponentMetric] = [ComponentMetric(**c) for c in components]

        # Componentes estimados aparecem na UI mas não entram na média do pilar
        real = [c for c in parsed if not c.is_estimated]
        scored = real if real else parsed  # fallback: usa todos se 100% estimado

        total_weight = sum(c.weight for c in scored)
        score = 0.0 if total_weight == 0 else sum(c.value * c.weight for c in scored) / total_weight

        pillar_trend = _trend(score, previous_score)

        log.debug(
            "pillar_calculated",
            pillar=pillar_id.value,
            score=round(score, 2),
            components_total=len(parsed),
            components_scored=len(scored),
            trend=pillar_trend,
        )

        return PillarScore(
            id=pillar_id,
            label=meta["label"],
            score=round(score, 2),
            weight=meta["weight"],
            color=meta["color"],
            status=_status(score),
            trend=pillar_trend,
            components=parsed,
            previous_score=previous_score,
        )

    def calculate_global(self, pillar_scores: list[PillarScore]) -> GovernanceScore:
        """Compute weighted global governance score from pillar scores.

        Pilares com data_source=coming_soon são excluídos e os pesos dos pilares
        reais são renormalizados para somar 1.0 — o score não é distorcido por
        dados inventados. Se TODOS forem coming_soon, usa todos como fallback.

        Args:
            pillar_scores: List of computed pillar scores.

        Returns:
            GovernanceScore aggregating all pillars.
        """
        all_weight = sum(p.weight for p in pillar_scores)
        real = [p for p in pillar_scores if p.data_source != DataSource.COMING_SOON]
        scored = real if real else pillar_scores  # fallback: todos coming_soon

        real_weight = sum(p.weight for p in scored)
        global_score = sum(p.score * p.weight for p in scored) / real_weight if real_weight > 0 else 0.0
        coverage = round(real_weight / all_weight, 4) if all_weight > 0 else 1.0

        previous_global: float | None = None
        prevs = [p.previous_score for p in scored if p.previous_score is not None]
        if len(prevs) == len(scored):
            prev_real_weight = sum(PILLAR_META[p.id]["weight"] for p in scored)
            previous_global = (
                sum(prev * PILLAR_META[p.id]["weight"] for p, prev in zip(scored, prevs, strict=False))
                / prev_real_weight
                if prev_real_weight > 0
                else None
            )

        global_trend = _trend(global_score, previous_global)

        log.info(
            "global_score_calculated",
            global_score=round(global_score, 2),
            coverage=coverage,
            active_pillars=len(scored),
            total_pillars=len(pillar_scores),
            status=_status(global_score),
            trend=global_trend,
        )

        return GovernanceScore(
            global_score=round(global_score, 2),
            status=_status(global_score),
            trend=global_trend,
            pillars=sorted(pillar_scores, key=lambda p: PILLAR_META[p.id]["order"]),
            computed_at=datetime.now(UTC).isoformat(),
            coverage=coverage,
            active_pillars=len(scored),
            total_pillars=len(pillar_scores),
        )
