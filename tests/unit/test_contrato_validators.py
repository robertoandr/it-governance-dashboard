"""Unit tests for Contrato Pydantic validators (dates, valor, SLA, enums)."""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.models.contrato import ContratoCreate, ContratoUpdate


# Helpers for constructing valid base payloads
_VALID_FID = "00000000-0000-0000-0000-000000000001"

_BASE_CREATE = {
    "fornecedor_id": _VALID_FID,
    "numero_contrato": "CTR-001",
    "objeto": "Serviço de Cloud Computing",
    "valor_mensal": "1500.00",
    "data_inicio": "2026-01-01",
    "data_fim": "2027-01-01",
    "sla_uptime_pct": 99.5,
    "criticidade": "alta",
}


# ---------------------------------------------------------------------------
# date ordering — data_fim must be strictly after data_inicio
# ---------------------------------------------------------------------------
class TestDateValidation:
    def test_valid_dates_accepted(self) -> None:
        c = ContratoCreate.model_validate(_BASE_CREATE)
        assert c.data_fim > c.data_inicio

    def test_data_fim_equal_to_inicio_raises(self) -> None:
        payload = {**_BASE_CREATE, "data_inicio": "2026-06-01", "data_fim": "2026-06-01"}
        with pytest.raises(ValidationError, match="data_fim deve ser posterior"):
            ContratoCreate.model_validate(payload)

    def test_data_fim_before_inicio_raises(self) -> None:
        payload = {**_BASE_CREATE, "data_inicio": "2026-06-01", "data_fim": "2026-01-01"}
        with pytest.raises(ValidationError, match="data_fim deve ser posterior"):
            ContratoCreate.model_validate(payload)

    def test_update_both_dates_invalid_raises(self) -> None:
        with pytest.raises(ValidationError, match="data_fim deve ser posterior"):
            ContratoUpdate.model_validate(
                {"data_inicio": "2026-06-01", "data_fim": "2026-01-01"}
            )

    def test_update_only_one_date_skips_cross_validation(self) -> None:
        # When only data_fim is provided, cross-check is skipped
        u = ContratoUpdate.model_validate({"data_fim": "2025-01-01"})
        assert u.data_fim == date(2025, 1, 1)


# ---------------------------------------------------------------------------
# valor_mensal — must be > 0
# ---------------------------------------------------------------------------
class TestValorMensal:
    def test_positive_value_accepted(self) -> None:
        c = ContratoCreate.model_validate(_BASE_CREATE)
        assert c.valor_mensal == Decimal("1500.00")

    def test_zero_raises(self) -> None:
        with pytest.raises(ValidationError, match="maior que zero"):
            ContratoCreate.model_validate({**_BASE_CREATE, "valor_mensal": "0"})

    def test_negative_raises(self) -> None:
        with pytest.raises(ValidationError, match="maior que zero"):
            ContratoCreate.model_validate({**_BASE_CREATE, "valor_mensal": "-100"})

    def test_update_zero_raises(self) -> None:
        with pytest.raises(ValidationError, match="maior que zero"):
            ContratoUpdate.model_validate({"valor_mensal": "0.00"})

    def test_update_none_skipped(self) -> None:
        u = ContratoUpdate.model_validate({})
        assert u.valor_mensal is None


# ---------------------------------------------------------------------------
# sla_uptime_pct — must be in [0, 100]
# ---------------------------------------------------------------------------
class TestSlaUptime:
    def test_zero_is_valid(self) -> None:
        c = ContratoCreate.model_validate({**_BASE_CREATE, "sla_uptime_pct": 0.0})
        assert c.sla_uptime_pct == 0.0

    def test_hundred_is_valid(self) -> None:
        c = ContratoCreate.model_validate({**_BASE_CREATE, "sla_uptime_pct": 100.0})
        assert c.sla_uptime_pct == 100.0

    def test_above_hundred_raises(self) -> None:
        with pytest.raises(ValidationError, match="entre 0 e 100"):
            ContratoCreate.model_validate({**_BASE_CREATE, "sla_uptime_pct": 100.1})

    def test_negative_raises(self) -> None:
        with pytest.raises(ValidationError, match="entre 0 e 100"):
            ContratoCreate.model_validate({**_BASE_CREATE, "sla_uptime_pct": -1.0})

    def test_update_out_of_range_raises(self) -> None:
        with pytest.raises(ValidationError, match="entre 0 e 100"):
            ContratoUpdate.model_validate({"sla_uptime_pct": 200.0})


# ---------------------------------------------------------------------------
# status enum
# ---------------------------------------------------------------------------
class TestStatusEnum:
    def test_valid_statuses_accepted(self) -> None:
        for s in ("vigente", "encerrado", "suspenso", "renovacao"):
            c = ContratoCreate.model_validate({**_BASE_CREATE, "status": s})
            assert c.status == s

    def test_invalid_status_raises(self) -> None:
        with pytest.raises(ValidationError, match="status deve ser um de"):
            ContratoCreate.model_validate({**_BASE_CREATE, "status": "ativo"})

    def test_update_invalid_status_raises(self) -> None:
        with pytest.raises(ValidationError, match="status deve ser um de"):
            ContratoUpdate.model_validate({"status": "bloqueado"})


# ---------------------------------------------------------------------------
# criticidade enum
# ---------------------------------------------------------------------------
class TestCriticidadeEnum:
    def test_valid_criticidades_accepted(self) -> None:
        for c_val in ("baixa", "media", "alta", "critica"):
            c = ContratoCreate.model_validate({**_BASE_CREATE, "criticidade": c_val})
            assert c.criticidade == c_val

    def test_invalid_criticidade_raises(self) -> None:
        with pytest.raises(ValidationError, match="criticidade deve ser um de"):
            ContratoCreate.model_validate({**_BASE_CREATE, "criticidade": "urgente"})

    def test_update_invalid_criticidade_raises(self) -> None:
        with pytest.raises(ValidationError, match="criticidade deve ser um de"):
            ContratoUpdate.model_validate({"criticidade": "maxima"})
