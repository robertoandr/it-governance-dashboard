"""Cálculo de governança do pilar Compliance a partir do Secure Score."""

from __future__ import annotations

from itgov.models.governance_compliance import ComplianceSummary, RecomendacaoControle

_TOP_RECOMENDACOES = 10

# Mapeamento de controlName → chave no dict security_controls
_SECURITY_CONTROL_MAP = {
    "EnableSafeLinksForEmail": "safe_links_enabled",
    "EnableSafeAttachmentForEmail": "safe_attachments_enabled",
    "TurnOnAuditDataRecording": "audit_log_enabled",
}


def _extract_security_controls(control_profiles: list[dict]) -> dict:
    """Extrai status dos controles de segurança específicos a partir dos perfis.

    Args:
        control_profiles: Lista retornada por SecureScoreGraphClient.get_security_controls().

    Returns:
        Dict com chaves safe_links_enabled, safe_attachments_enabled, audit_log_enabled (bool).
    """
    result: dict = {}
    for profile in control_profiles:
        control_name = profile.get("controlName", "")
        key = _SECURITY_CONTROL_MAP.get(control_name)
        if key:
            status = profile.get("implementationStatus", "")
            result[key] = status == "implemented"
    return result


def calcular_resumo_compliance(
    secure_score: dict | None,
    control_profiles: list[dict] | None = None,
) -> ComplianceSummary:
    """Calcula o resumo de Compliance a partir do registro Secure Score do Graph.

    Args:
        secure_score: dict retornado por SecureScoreGraphClient.get_latest_secure_score(),
            ou None se não houver dados disponíveis.
        control_profiles: lista opcional retornada por get_security_controls().
            Se fornecida, extrai status de Safe Links, Safe Attachments e Audit Log.

    Returns:
        ComplianceSummary com score, breakdown por categoria, top recomendações
        e status dos controles de segurança específicos.
    """
    security_controls = _extract_security_controls(control_profiles) if control_profiles else {}

    if secure_score is None:
        return ComplianceSummary(security_controls=security_controls)

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
        security_controls=security_controls,
    )
