"""Unit tests for Fornecedor Pydantic validators (CNPJ + status)."""

import pytest
from pydantic import ValidationError

from app.models.fornecedor import FornecedorCreate, FornecedorUpdate, _validate_cnpj


# ---------------------------------------------------------------------------
# _validate_cnpj — the core algorithm
# ---------------------------------------------------------------------------
class TestValidateCnpj:
    """Tests for the _validate_cnpj helper function."""

    def test_valid_cnpj_digits_only(self) -> None:
        assert _validate_cnpj("11222333000181") == "11222333000181"

    def test_valid_cnpj_with_formatting(self) -> None:
        assert _validate_cnpj("11.222.333/0001-81") == "11222333000181"

    def test_valid_cnpj_spaces_stripped(self) -> None:
        assert _validate_cnpj(" 11222333000181 ") == "11222333000181"

    def test_invalid_wrong_length(self) -> None:
        with pytest.raises(ValueError, match="14 dígitos"):
            _validate_cnpj("1122233300018")  # 13 digits

    def test_invalid_all_same_digits(self) -> None:
        with pytest.raises(ValueError, match="todos os dígitos iguais"):
            _validate_cnpj("00000000000000")

    def test_invalid_all_same_digits_ones(self) -> None:
        with pytest.raises(ValueError, match="todos os dígitos iguais"):
            _validate_cnpj("11111111111111")

    def test_invalid_wrong_check_digits(self) -> None:
        with pytest.raises(ValueError, match="dígitos verificadores"):
            _validate_cnpj("11222333000100")  # last two digits wrong

    def test_valid_various_cnpjs(self) -> None:
        valid_cnpjs = [
            "11222333000181",
            "45997418000153",
            "33000167000101",  # Petrobras
        ]
        for cnpj in valid_cnpjs:
            result = _validate_cnpj(cnpj)
            assert len(result) == 14
            assert result.isdigit()


# ---------------------------------------------------------------------------
# FornecedorCreate — Pydantic integration
# ---------------------------------------------------------------------------
class TestFornecedorCreate:
    """Tests for FornecedorCreate Pydantic model validation."""

    def test_valid_creation(self) -> None:
        data = FornecedorCreate(nome="Acme", cnpj="11222333000181", categoria="Cloud")
        assert data.cnpj == "11222333000181"
        assert data.status == "ativo"

    def test_cnpj_none_is_allowed(self) -> None:
        data = FornecedorCreate(nome="Acme", cnpj=None, categoria="Cloud")
        assert data.cnpj is None

    def test_cnpj_with_formatting_is_normalised(self) -> None:
        data = FornecedorCreate(nome="Acme", cnpj="11.222.333/0001-81", categoria="Cloud")
        assert data.cnpj == "11222333000181"

    def test_invalid_cnpj_raises_422(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            FornecedorCreate(nome="Acme", cnpj="00000000000000", categoria="Cloud")
        errors = exc_info.value.errors()
        assert any(e["loc"] == ("cnpj",) for e in errors)

    def test_invalid_status_raises_422(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            FornecedorCreate(nome="Acme", categoria="Cloud", status="bloqueado")
        errors = exc_info.value.errors()
        assert any(e["loc"] == ("status",) for e in errors)

    def test_nome_too_short_raises_422(self) -> None:
        with pytest.raises(ValidationError):
            FornecedorCreate(nome="A", categoria="Cloud")

    def test_defaults(self) -> None:
        data = FornecedorCreate(nome="Acme", categoria="Software")
        assert data.status == "ativo"
        assert data.suporte_24x7 is False
        assert data.contato_nome is None

    def test_valid_statuses(self) -> None:
        for status in ("ativo", "inativo", "suspenso"):
            data = FornecedorCreate(nome="Acme", categoria="Cloud", status=status)
            assert data.status == status


# ---------------------------------------------------------------------------
# FornecedorUpdate — PATCH semantics
# ---------------------------------------------------------------------------
class TestFornecedorUpdate:
    """Tests for FornecedorUpdate PATCH schema."""

    def test_empty_update_is_valid(self) -> None:
        data = FornecedorUpdate()
        assert data.model_dump(exclude_unset=True) == {}

    def test_partial_update_only_sets_provided_fields(self) -> None:
        data = FornecedorUpdate(status="inativo")
        dumped = data.model_dump(exclude_unset=True)
        assert dumped == {"status": "inativo"}

    def test_invalid_status_in_update(self) -> None:
        with pytest.raises(ValidationError):
            FornecedorUpdate(status="arquivado")

    def test_cnpj_validated_on_update(self) -> None:
        with pytest.raises(ValidationError):
            FornecedorUpdate(cnpj="00000000000000")

    def test_valid_cnpj_on_update(self) -> None:
        data = FornecedorUpdate(cnpj="11.222.333/0001-81")
        assert data.cnpj == "11222333000181"
