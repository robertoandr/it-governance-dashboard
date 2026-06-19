"""Testes para itgov/services/compliance_service.py — pilar Compliance."""

from __future__ import annotations

from itgov.services.compliance_service import calcular_resumo_compliance


def _secure_score(current: float = 121.0, maximo: float = 283.0, controles: list[dict] | None = None) -> dict:
    return {
        "currentScore": current,
        "maxScore": maximo,
        "controlScores": controles or [],
    }


def _controle(nome: str, categoria: str, score_pct: float, descricao: str = "desc") -> dict:
    return {
        "controlName": nome,
        "controlCategory": categoria,
        "scoreInPercentage": score_pct,
        "description": descricao,
    }


class TestCalcularResumoCompliance:
    def test_none_retorna_resumo_vazio(self) -> None:
        resumo = calcular_resumo_compliance(None)

        assert resumo.pct is None
        assert resumo.current_score is None
        assert resumo.recomendacoes == []

    def test_calcula_pct_corretamente(self) -> None:
        resumo = calcular_resumo_compliance(_secure_score(current=121.0, maximo=283.0))

        assert resumo.pct == 42.8
        assert resumo.current_score == 121.0
        assert resumo.max_score == 283.0

    def test_breakdown_por_categoria_eh_media(self) -> None:
        controles = [
            _controle("a", "Apps", 100.0),
            _controle("b", "Apps", 0.0),
            _controle("c", "Identity", 50.0),
        ]
        resumo = calcular_resumo_compliance(_secure_score(controles=controles))

        assert resumo.category_breakdown == {"Apps": 50.0, "Identity": 50.0}

    def test_recomendacoes_excluem_controles_100_pct(self) -> None:
        controles = [
            _controle("completo", "Apps", 100.0),
            _controle("pendente", "Identity", 30.0),
        ]
        resumo = calcular_resumo_compliance(_secure_score(controles=controles))

        nomes = [r.control_name for r in resumo.recomendacoes]
        assert "completo" not in nomes
        assert "pendente" in nomes

    def test_recomendacoes_ordenadas_pelo_menor_score_primeiro(self) -> None:
        controles = [
            _controle("medio", "Apps", 50.0),
            _controle("pior", "Identity", 0.0),
            _controle("razoavel", "Data", 80.0),
        ]
        resumo = calcular_resumo_compliance(_secure_score(controles=controles))

        nomes = [r.control_name for r in resumo.recomendacoes]
        assert nomes == ["pior", "medio", "razoavel"]

    def test_recomendacoes_limitadas_a_10(self) -> None:
        controles = [_controle(f"c{i}", "Apps", 0.0) for i in range(15)]
        resumo = calcular_resumo_compliance(_secure_score(controles=controles))

        assert len(resumo.recomendacoes) == 10

    def test_max_score_zero_nao_quebra_calculo(self) -> None:
        resumo = calcular_resumo_compliance(_secure_score(current=0.0, maximo=0.0))

        assert resumo.pct is None
