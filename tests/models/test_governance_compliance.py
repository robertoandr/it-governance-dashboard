"""Testes para itgov/models/governance_compliance.py."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from itgov.models.governance_compliance import ComplianceSummary, RecomendacaoControle


class TestComplianceSummary:
    def test_pct_aceita_none(self) -> None:
        resumo = ComplianceSummary()

        assert resumo.pct is None
        assert resumo.current_score is None

    def test_pct_fora_do_intervalo_rejeitado(self) -> None:
        with pytest.raises(ValidationError):
            ComplianceSummary(pct=150.0)

    def test_defaults_sao_vazios(self) -> None:
        resumo = ComplianceSummary()

        assert resumo.category_breakdown == {}
        assert resumo.recomendacoes == []


class TestRecomendacaoControle:
    def test_score_pct_fora_do_intervalo_rejeitado(self) -> None:
        with pytest.raises(ValidationError):
            RecomendacaoControle(control_name="x", categoria="Apps", descricao="d", score_pct=-1.0)
