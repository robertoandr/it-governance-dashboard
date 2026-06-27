"""Testes para itgov/services/data_governance_service.py — pilar Dados."""

from __future__ import annotations

from itgov.services.data_governance_service import calcular_resumo_dados


class TestCalcularResumoDados:
    def test_lista_vazia_retorna_zero_labels(self) -> None:
        resumo = calcular_resumo_dados([])

        assert resumo.total_labels == 0
        assert resumo.labels == []

    def test_converte_labels_corretamente(self) -> None:
        labels = [
            {"id": "1", "name": "Confidencial", "description": "Dados sensíveis", "isActive": True},
            {"id": "2", "name": "Público", "isActive": False},
        ]

        resumo = calcular_resumo_dados(labels)

        assert resumo.total_labels == 2
        assert resumo.labels[0].name == "Confidencial"
        assert resumo.labels[0].description == "Dados sensíveis"
        assert resumo.labels[1].is_active is False

    def test_label_sem_nome_usa_placeholder(self) -> None:
        resumo = calcular_resumo_dados([{"id": "1"}])

        assert resumo.labels[0].name == "(sem nome)"

    def test_isactive_ausente_assume_true(self) -> None:
        resumo = calcular_resumo_dados([{"id": "1", "name": "x"}])

        assert resumo.labels[0].is_active is True
