"""Testes para itgov/services/compliance_service.py — pilar Compliance."""

from __future__ import annotations

from itgov.services.compliance_service import (
    _extract_comparative_score,
    calcular_resumo_compliance,
    calcular_variacao_30d,
    montar_tabela_controles,
)


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


def _perfil(
    nome: str = "EnableMFA",
    categoria: str = "Identity",
    max_score: float = 10.0,
    score: float = 0.0,
    status: str = "notImplemented",
    remediation: str = "Habilite MFA para todos os usuários.",
    action_url: str | None = "https://portal.example/action",
) -> dict:
    return {
        "controlName": nome,
        "title": nome,
        "controlCategory": categoria,
        "maxScore": max_score,
        "score": score,
        "implementationStatus": status,
        "remediation": remediation,
        "actionUrl": action_url,
    }


class TestExtractComparativeScore:
    def test_sem_averagecomparativescores_retorna_none(self) -> None:
        score, basis = _extract_comparative_score({})

        assert score is None
        assert basis is None

    def test_prioriza_totalseats_sobre_alltenants(self) -> None:
        secure_score = {
            "averageComparativeScores": [
                {"basis": "AllTenants", "averageScore": 55.2},
                {"basis": "TotalSeats", "averageScore": 61.7},
            ]
        }
        score, basis = _extract_comparative_score(secure_score)

        assert score == 61.7
        assert basis == "TotalSeats"

    def test_fallback_para_alltenants_quando_sem_totalseats(self) -> None:
        secure_score = {"averageComparativeScores": [{"basis": "AllTenants", "averageScore": 58.0}]}
        score, basis = _extract_comparative_score(secure_score)

        assert score == 58.0
        assert basis == "AllTenants"

    def test_basis_desconhecido_eh_ignorado(self) -> None:
        secure_score = {"averageComparativeScores": [{"basis": "IndustryTypeAllTenants", "averageScore": 40.0}]}
        score, basis = _extract_comparative_score(secure_score)

        assert score is None
        assert basis is None


class TestMontarTabelaControles:
    def test_lista_vazia_ou_none_retorna_vazio(self) -> None:
        assert montar_tabela_controles(None) == []
        assert montar_tabela_controles([]) == []

    def test_ordenado_por_max_score_desc(self) -> None:
        perfis = [_perfil(nome="baixo", max_score=5.0), _perfil(nome="alto", max_score=30.0)]
        tabela = montar_tabela_controles(perfis)

        assert [c.control_name for c in tabela] == ["alto", "baixo"]

    def test_status_implementado_quando_status_implemented(self) -> None:
        perfis = [_perfil(status="implemented", score=10.0, max_score=10.0)]
        tabela = montar_tabela_controles(perfis)

        assert tabela[0].status == "implementado"

    def test_status_pendente_quando_nao_implementado(self) -> None:
        perfis = [_perfil(status="notImplemented", score=0.0, max_score=10.0)]
        tabela = montar_tabela_controles(perfis)

        assert tabela[0].status == "pendente"

    def test_status_ignorado(self) -> None:
        perfis = [_perfil(status="ignored")]
        tabela = montar_tabela_controles(perfis)

        assert tabela[0].status == "ignorado"

    def test_acao_vem_do_campo_remediation(self) -> None:
        perfis = [_perfil(remediation="Faça X para corrigir.")]
        tabela = montar_tabela_controles(perfis)

        assert tabela[0].acao == "Faça X para corrigir."

    def test_action_url_ausente_vira_none(self) -> None:
        perfis = [_perfil(action_url=None)]
        tabela = montar_tabela_controles(perfis)

        assert tabela[0].action_url is None


class TestCalcularVariacao30d:
    def test_sem_historico_retorna_none(self) -> None:
        assert calcular_variacao_30d([], 50.0) is None
        assert calcular_variacao_30d(None, 50.0) is None

    def test_pct_atual_none_retorna_none(self) -> None:
        historico = [{"time": "2026-06-01T00:00:00Z", "pct": 40.0}]
        assert calcular_variacao_30d(historico, None) is None

    def test_calcula_diferenca_vs_ponto_mais_proximo_de_30d(self) -> None:
        from datetime import UTC, datetime, timedelta

        now = datetime.now(UTC)
        historico = [
            {"time": (now - timedelta(days=60)).isoformat(), "pct": 30.0},
            {"time": (now - timedelta(days=31)).isoformat(), "pct": 40.0},
            {"time": (now - timedelta(days=2)).isoformat(), "pct": 45.0},
        ]
        # O ponto de referência deve ser o mais recente com >= 30 dias (pct=40.0).
        variacao = calcular_variacao_30d(historico, 50.0)

        assert variacao == 10.0

    def test_sem_ponto_anterior_usa_o_mais_antigo_disponivel(self) -> None:
        from datetime import UTC, datetime, timedelta

        recente = (datetime.now(UTC) - timedelta(days=2)).isoformat()
        historico = [{"time": recente, "pct": 35.0}]

        variacao = calcular_variacao_30d(historico, 40.0)

        assert variacao == 5.0
