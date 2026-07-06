"""Cálculo de governança do pilar Compliance a partir do Secure Score."""

from __future__ import annotations

from itgov.models.governance_compliance import (
    ComplianceSummary,
    ControlePendente,
    HistoricoPonto,
    RecomendacaoControle,
)

_TOP_RECOMENDACOES = 10

# Mapeamento de controlName → chave no dict security_controls
_SECURITY_CONTROL_MAP = {
    "EnableSafeLinksForEmail": "safe_links_enabled",
    "EnableSafeAttachmentForEmail": "safe_attachments_enabled",
    "TurnOnAuditDataRecording": "audit_log_enabled",
}

# Ordem de preferência de basis em averageComparativeScores — grupo por
# tamanho de tenant (TotalSeats) é mais relevante que a média de todos os
# tenants (AllTenants).
_COMPARATIVE_BASIS_PRIORITY = ["TotalSeats", "AllTenants"]


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


def _extract_comparative_score(secure_score: dict) -> tuple[float | None, str | None]:
    """Extrai o averageComparativeScores mais relevante (TotalSeats > AllTenants).

    Args:
        secure_score: dict retornado por get_latest_secure_score().

    Returns:
        Tupla (score, basis), ou (None, None) se o campo estiver ausente/vazio.
    """
    entradas = secure_score.get("averageComparativeScores") or []
    por_basis = {e.get("basis"): e.get("averageScore") for e in entradas if e.get("basis")}

    for basis in _COMPARATIVE_BASIS_PRIORITY:
        score = por_basis.get(basis)
        if score is not None:
            return round(float(score), 1), basis

    return None, None


def montar_tabela_controles(
    control_profiles: list[dict] | None,
    control_scores: list[dict] | None = None,
) -> list[ControlePendente]:
    """Monta a tabela de controles a partir do join entre os dois endpoints do Graph.

    ``secureScoreControlProfiles`` (control_profiles) é um catálogo genérico de
    controles — não tem o score real do tenant, e o campo ``controlName`` vem
    vazio para este tenant; a chave estável desse recurso é ``id``.
    ``secureScores.controlScores`` (control_scores) tem o score/percentual real
    por controle deste tenant, referenciando o controle pelo campo
    ``controlName`` — que na prática contém o mesmo valor do ``id`` do catálogo.

    Sem ``control_scores`` não há como saber o estado real de nenhum controle
    (o catálogo sozinho não diz nada sobre o tenant), então a tabela fica vazia
    nesse caso — mostrar as ~449 linhas do catálogo cru como "pendente" seria
    dado incorreto.

    Args:
        control_profiles: lista retornada por SecureScoreGraphClient.get_security_controls().
        control_scores: lista ``secure_score["controlScores"]`` (score real por controle).

    Returns:
        Lista de ControlePendente ordenada por max_score DESC — um item por
        controle efetivamente avaliado para o tenant.
    """
    if not control_scores:
        return []

    perfis_por_id = {p.get("id"): p for p in (control_profiles or []) if p.get("id")}

    linhas = []
    for cs in control_scores:
        nome = cs.get("controlName") or ""
        perfil = perfis_por_id.get(nome, {})

        max_score = float(perfil.get("maxScore") or 0.0)
        score = float(cs.get("score") or 0.0)
        pct = cs.get("scoreInPercentage")
        status_raw = (cs.get("implementationStatus") or perfil.get("implementationStatus") or "").lower()

        if status_raw == "ignored":
            status = "ignorado"
        elif pct is not None and pct >= 100.0:
            status = "implementado"
        else:
            status = "pendente"

        linhas.append(
            ControlePendente(
                control_name=nome,
                title=perfil.get("title") or nome,
                categoria=perfil.get("controlCategory") or cs.get("controlCategory") or "Outros",
                max_score=max_score,
                score=score,
                status=status,
                acao=perfil.get("remediation") or "",
                action_url=perfil.get("actionUrl"),
            )
        )

    linhas.sort(key=lambda c: c.max_score, reverse=True)
    return linhas


def calcular_variacao_30d(historico: list[dict] | None, pct_atual: float | None) -> float | None:
    """Calcula a variação em pontos percentuais do Secure Score vs. ~30 dias atrás.

    Args:
        historico: lista de pontos {"time": iso8601, "pct": float}, ordenada
            ascendente por tempo (mais antigo primeiro).
        pct_atual: percentual atual do Secure Score.

    Returns:
        pct_atual - pct de ~30 dias atrás, arredondado a 1 casa; None se não
        houver histórico suficiente para comparar.
    """
    if not historico or pct_atual is None:
        return None

    from datetime import UTC, datetime, timedelta

    cutoff = datetime.now(UTC) - timedelta(days=30)

    referencia = None
    for ponto in historico:
        ts = ponto["time"]
        ts_dt = datetime.fromisoformat(ts.replace("Z", "+00:00")) if isinstance(ts, str) else ts
        if ts_dt <= cutoff:
            referencia = ponto["pct"]
        else:
            break

    if referencia is None:
        referencia = historico[0]["pct"]

    return round(pct_atual - referencia, 1)


def calcular_resumo_compliance(
    secure_score: dict | None,
    control_profiles: list[dict] | None = None,
    historico_90d: list[dict] | None = None,
) -> ComplianceSummary:
    """Calcula o resumo de Compliance a partir do registro Secure Score do Graph.

    Args:
        secure_score: dict retornado por SecureScoreGraphClient.get_latest_secure_score(),
            ou None se não houver dados disponíveis.
        control_profiles: lista opcional retornada por get_security_controls().
            Se fornecida, extrai status de Safe Links, Safe Attachments e Audit Log,
            além da tabela completa de controles (ordenada por max_score DESC).
        historico_90d: lista opcional de pontos {"time", "pct"} do InfluxDB
            (gov_m365_secure_score), usada para o gráfico de evolução e a
            variação vs 30 dias atrás.

    Returns:
        ComplianceSummary com score, breakdown por categoria, top recomendações,
        status dos controles de segurança específicos, comparação com tenants
        similares, tabela completa de controles e histórico de 90 dias.
    """
    security_controls = _extract_security_controls(control_profiles) if control_profiles else {}
    tabela_controles = montar_tabela_controles(control_profiles, (secure_score or {}).get("controlScores"))
    historico_pontos = [HistoricoPonto(**p) for p in (historico_90d or [])]

    if secure_score is None:
        variacao_30d = calcular_variacao_30d(historico_90d, None)
        return ComplianceSummary(
            security_controls=security_controls,
            controles=tabela_controles,
            historico_90d=historico_pontos,
            variacao_30d=variacao_30d,
        )

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

    comparative_pct, comparative_basis = _extract_comparative_score(secure_score)
    variacao_30d = calcular_variacao_30d(historico_90d, pct)

    return ComplianceSummary(
        current_score=current,
        max_score=maximo,
        pct=pct,
        category_breakdown=category_breakdown,
        recomendacoes=recomendacoes,
        security_controls=security_controls,
        comparative_pct=comparative_pct,
        comparative_basis=comparative_basis,
        controles=tabela_controles,
        historico_90d=historico_pontos,
        variacao_30d=variacao_30d,
    )
