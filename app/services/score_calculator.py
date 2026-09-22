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

        total_weight = sum(c.weight for c in parsed)
        score = 0.0 if total_weight == 0 else sum(c.value * c.weight for c in parsed) / total_weight

        pillar_trend = _trend(score, previous_score)

        log.debug(
            "pillar_calculated",
            pillar=pillar_id.value,
            score=round(score, 2),
            components=len(parsed),
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

        Pillars still on ``coming_soon`` (no collector has ever written real
        data for them) are excluded from the weighted average and the weights
        of the remaining pillars are renormalized. Blending in a pillar's
        mock/seed score as if it were real would silently understate how much
        of the global number is actually backed by live data — worse than
        just reporting a smaller, but honest, average.

        Args:
            pillar_scores: List of computed pillar scores.

        Returns:
            GovernanceScore aggregating pillars with real data. If none have
            real data yet, falls back to the full (mock) set so the page
            never renders a blank/zero score.
        """
        real = [p for p in pillar_scores if p.data_source != DataSource.COMING_SOON]
        basis = real or pillar_scores
        total_weight = sum(p.weight for p in basis)
        global_score = sum(p.score * p.weight for p in basis) / total_weight if total_weight else 0.0
        previous_global: float | None = None

        prevs = [p.previous_score for p in basis if p.previous_score is not None]
        if len(prevs) == len(basis) and total_weight:
            previous_global = (
                sum(prev * PILLAR_META[p.id]["weight"] for p, prev in zip(basis, prevs, strict=False)) / total_weight
            )

        global_trend = _trend(global_score, previous_global)

        log.info(
            "global_score_calculated",
            global_score=round(global_score, 2),
            status=_status(global_score),
            trend=global_trend,
            pillars=len(pillar_scores),
            pillars_live=len(real),
        )

        return GovernanceScore(
            global_score=round(global_score, 2),
            status=_status(global_score),
            trend=global_trend,
            pillars=sorted(pillar_scores, key=lambda p: PILLAR_META[p.id]["order"]),
            computed_at=datetime.now(UTC).isoformat(),
        )
