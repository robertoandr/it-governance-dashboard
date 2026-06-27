"""Testes para itgov/models/governance_data.py."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from itgov.models.governance_data import DataGovernanceSummary, SensitivityLabelInfo


class TestSensitivityLabelInfo:
    def test_is_active_default_true(self) -> None:
        label = SensitivityLabelInfo(label_id="1", name="Confidencial")

        assert label.is_active is True

    def test_description_aceita_none(self) -> None:
        label = SensitivityLabelInfo(label_id="1", name="Confidencial", description=None)

        assert label.description is None


class TestDataGovernanceSummary:
    def test_total_labels_negativo_rejeitado(self) -> None:
        with pytest.raises(ValidationError):
            DataGovernanceSummary(total_labels=-1)

    def test_zero_labels_eh_resultado_valido(self) -> None:
        resumo = DataGovernanceSummary(total_labels=0)

        assert resumo.total_labels == 0
        assert resumo.labels == []
