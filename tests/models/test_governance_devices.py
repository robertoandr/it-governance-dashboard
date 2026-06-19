"""Testes para itgov/models/governance_devices.py."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from itgov.models.governance_devices import DeviceSummary


class TestDeviceSummary:
    def test_managed_pct_aceita_none(self) -> None:
        resumo = DeviceSummary(total_devices=10, stale_90d=1, managed_pct=None)

        assert resumo.managed_pct is None

    def test_managed_pct_fora_do_intervalo_rejeitado(self) -> None:
        with pytest.raises(ValidationError):
            DeviceSummary(total_devices=10, stale_90d=1, managed_pct=150.0)

    def test_total_devices_negativo_rejeitado(self) -> None:
        with pytest.raises(ValidationError):
            DeviceSummary(total_devices=-1, stale_90d=0)

    def test_defaults_de_distribuicao_sao_dicts_vazios(self) -> None:
        resumo = DeviceSummary(total_devices=0, stale_90d=0)

        assert resumo.os_distribution == {}
        assert resumo.trust_type_distribution == {}
