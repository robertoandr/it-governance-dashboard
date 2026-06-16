"""Cálculo de governança do pilar Compliance a partir do Secure Score."""

from __future__ import annotations

from itgov.models.governance_compliance import ComplianceSummary, RecomendacaoControle

_TOP_RECOMENDACOES = 10


def calcular_resumo_compliance(secure_score: dict | None) -> ComplianceSummary:
    """Calcula o resumo de Compliance a partir do registro Secure Score do Graph.

    Args:
        secure_score: dict retornado por SecureScoreGraphClient.get_latest_secure_score(),
            ou None se não houver dados disponíveis.

    Returns:
        ComplianceSummary com score, breakdown por categoria e top recomendações
        (controles com menor scoreInPercentage, excluindo os já 100% implementados).
    """
    if secure_score is None:
        return ComplianceSummary()

    current = secure_score.get("currentScore")
    maximo = secure_score.get("maxScore")
    pct = round(current / maximo * 100, 1) if current is not None and maximo else None

    controles = secure_score.get("controlScores") or []

    categoria_scores: dict[str, list[float]] = {}
    for c in controles:
        categoria = c.get("controlCategory") or "Outros"
        score_pct = c.get("scoreInPercentage")
        if score_pct is not None:
            categoria_scores.setdefault(categoria, []).append(score_pct)

    category_breakdown = {
        categoria: round(sum(scores) / len(scores), 1) for categoria, scores in categoria_scores.items()
    }

    pendentes = [c for c in controles if (c.get("scoreInPercentage") or 0) < 100.0]
    pendentes.sort(key=lambda c: c.get("scoreInPercentage") or 0)

    recomendacoes = [
        RecomendacaoControle(
            control_name=c.get("controlName") or "",
            categoria=c.get("controlCategory") or "Outros",
            descricao=c.get("description") or "",
            score_pct=c.get("scoreInPercentage") or 0.0,
        )
        for c in pendentes[:_TOP_RECOMENDACOES]
    ]

    return ComplianceSummary(
        current_score=current,
        max_score=maximo,
        pct=pct,
        category_breakdown=category_breakdown,
        recomendacoes=recomendacoes,
    )
