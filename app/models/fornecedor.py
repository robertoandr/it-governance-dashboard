"""Pydantic schemas for the Fornecedor (vendor) resource."""

import re
import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---------------------------------------------------------------------------
# Enums as literals — avoids import overhead of Python enum
# ---------------------------------------------------------------------------
CATEGORIAS_VALIDAS = frozenset(
    {"Cloud", "Software", "Telecom", "Hardware", "Consultoria", "Outros"}
)
STATUS_VALIDOS = frozenset({"ativo", "inativo", "suspenso"})


def _validate_cnpj(cnpj: str) -> str:
    """Validate a Brazilian CNPJ using the modulo-11 algorithm.

    Strips non-digit characters before checking, so both "11.222.333/0001-81"
    and "11222333000181" are accepted.

    Args:
        cnpj: Raw CNPJ string (with or without formatting).

    Returns:
        str: Digits-only CNPJ (14 characters).

    Raises:
        ValueError: If the CNPJ is structurally invalid or has wrong check digits.
    """
    digits = re.sub(r"\D", "", cnpj)

    if len(digits) != 14:
        raise ValueError("CNPJ deve ter 14 dígitos")

    if len(set(digits)) == 1:
        raise ValueError("CNPJ inválido (todos os dígitos iguais)")

    def _calc_digit(digits_slice: str, weights: list[int]) -> int:
        total = sum(int(d) * w for d, w in zip(digits_slice, weights, strict=False))
        remainder = total % 11
        return 0 if remainder < 2 else 11 - remainder

    weights_1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    weights_2 = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]

    first_digit = _calc_digit(digits[:12], weights_1)
    second_digit = _calc_digit(digits[:13], weights_2)

    if digits[12] != str(first_digit) or digits[13] != str(second_digit):
        raise ValueError("CNPJ inválido (dígitos verificadores incorretos)")

    return digits


# ---------------------------------------------------------------------------
# Base schema — shared fields
# ---------------------------------------------------------------------------
class FornecedorBase(BaseModel):
    """Shared fields for Fornecedor create and update operations."""

    nome: Annotated[str, Field(min_length=2, max_length=255)]
    categoria: Annotated[str, Field(min_length=1, max_length=100)]
    status: Annotated[str, Field(default="ativo")]
    contato_nome: str | None = None
    contato_email: str | None = None
    contato_telefone: str | None = None
    suporte_24x7: bool = False
    observacoes: str | None = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        """Ensure status is one of the allowed values.

        Args:
            v: Raw status string.

        Returns:
            str: Validated status.

        Raises:
            ValueError: If status is not in STATUS_VALIDOS.
        """
        if v not in STATUS_VALIDOS:
            raise ValueError(f"status deve ser um de: {sorted(STATUS_VALIDOS)}")
        return v


# ---------------------------------------------------------------------------
# Create schema
# ---------------------------------------------------------------------------
class FornecedorCreate(FornecedorBase):
    """Input schema for creating a new Fornecedor.

    CNPJ is required on creation and must pass the modulo-11 check.
    """

    cnpj: str | None = None

    @field_validator("cnpj")
    @classmethod
    def validate_cnpj(cls, v: str | None) -> str | None:
        """Validate CNPJ when provided.

        Args:
            v: Raw CNPJ string or None.

        Returns:
            str | None: Digits-only CNPJ or None.

        Raises:
            ValueError: If the CNPJ fails the modulo-11 check.
        """
        if v is None:
            return None
        return _validate_cnpj(v)


# ---------------------------------------------------------------------------
# Update schema — all fields optional (PATCH semantics)
# ---------------------------------------------------------------------------
class FornecedorUpdate(BaseModel):
    """Input schema for partial update of a Fornecedor (PATCH semantics)."""

    nome: Annotated[str, Field(min_length=2, max_length=255)] | None = None
    cnpj: str | None = None
    categoria: str | None = None
    status: str | None = None
    contato_nome: str | None = None
    contato_email: str | None = None
    contato_telefone: str | None = None
    suporte_24x7: bool | None = None
    observacoes: str | None = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str | None) -> str | None:
        """Ensure status is valid when provided.

        Args:
            v: Raw status string or None.

        Returns:
            str | None: Validated status or None.
        """
        if v is not None and v not in STATUS_VALIDOS:
            raise ValueError(f"status deve ser um de: {sorted(STATUS_VALIDOS)}")
        return v

    @field_validator("cnpj")
    @classmethod
    def validate_cnpj(cls, v: str | None) -> str | None:
        """Validate CNPJ when provided on update.

        Args:
            v: Raw CNPJ string or None.

        Returns:
            str | None: Digits-only CNPJ or None.
        """
        if v is None:
            return None
        return _validate_cnpj(v)


# ---------------------------------------------------------------------------
# Response schema
# ---------------------------------------------------------------------------
class FornecedorResponse(BaseModel):
    """Output schema for Fornecedor responses (read model)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    nome: str
    cnpj: str | None
    categoria: str
    status: str
    contato_nome: str | None
    contato_email: str | None
    contato_telefone: str | None
    suporte_24x7: bool
    observacoes: str | None
    created_at: datetime
    updated_at: datetime


class FornecedorListResponse(BaseModel):
    """Paginated list of Fornecedores."""

    model_config = ConfigDict(from_attributes=True)

    items: list[FornecedorResponse]
    total: int
    skip: int
    limit: int
