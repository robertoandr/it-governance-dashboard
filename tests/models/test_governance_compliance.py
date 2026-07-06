"""Testes para itgov/models/governance_compliance.py."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from itgov.models.governance_compliance import (
    ComplianceSummary,
    ControlePendente,
    HistoricoPonto,
    RecomendacaoControle,
)


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


class TestControlePendente:
    def test_campos_obrigatorios_aceitos(self) -> None:
        controle = ControlePendente(
            control_name="EnableMFA",
            title="Enable MFA",
            categoria="Identity",
            max_score=10.0,
            score=0.0,
            status="pendente",
            acao="Habilite MFA.",
        )

        assert controle.action_url is None

    def test_max_score_negativo_rejeitado(self) -> None:
        with pytest.raises(ValidationError):
            ControlePendente(
                control_name="x",
                title="x",
                categoria="Identity",
                max_score=-1.0,
                score=0.0,
                status="pendente",
                acao="",
            )


class TestHistoricoPonto:
    def test_pct_fora_do_intervalo_rejeitado(self) -> None:
        with pytest.raises(ValidationError):
            HistoricoPonto(time="2026-07-01T00:00:00Z", pct=150.0)

    def test_ponto_valido(self) -> None:
        ponto = HistoricoPonto(time="2026-07-01T00:00:00Z", pct=42.8)

        assert ponto.pct == 42.8


class TestComplianceSummaryNovosCampos:
    def test_defaults_novos_campos(self) -> None:
        resumo = ComplianceSummary()

        assert resumo.comparative_pct is None
        assert resumo.comparative_basis is None
        assert resumo.controles == []
        assert resumo.historico_90d == []
        assert resumo.variacao_30d is None
