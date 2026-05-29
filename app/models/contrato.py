"""Pydantic schemas for the Contrato (contract) resource."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.fornecedor import FornecedorResponse

# ---------------------------------------------------------------------------
# Allowed enum values
# ---------------------------------------------------------------------------
STATUS_VALIDOS = frozenset({"vigente", "encerrado", "suspenso", "renovacao"})
CRITICIDADE_VALIDAS = frozenset({"baixa", "media", "alta", "critica"})


# ---------------------------------------------------------------------------
# Base schema — shared fields and cross-field validators
# ---------------------------------------------------------------------------
class ContratoBase(BaseModel):
    """Shared fields for Contrato create and update operations."""

    numero_contrato: Annotated[str, Field(min_length=1, max_length=100)]
    objeto: Annotated[str, Field(min_length=1)]
    valor_mensal: Decimal
    data_inicio: date
    data_fim: date
    status: Annotated[str, Field(default="vigente")]
    sla_uptime_pct: float
    criticidade: Annotated[str, Field(default="media")]

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        """Ensure status is one of the allowed lifecycle values.

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

    @field_validator("criticidade")
    @classmethod
    def validate_criticidade(cls, v: str) -> str:
        """Ensure criticidade is one of the allowed values.

        Args:
            v: Raw criticidade string.

        Returns:
            str: Validated criticidade.

        Raises:
            ValueError: If criticidade is not in CRITICIDADE_VALIDAS.
        """
        if v not in CRITICIDADE_VALIDAS:
            raise ValueError(f"criticidade deve ser um de: {sorted(CRITICIDADE_VALIDAS)}")
        return v

    @field_validator("valor_mensal")
    @classmethod
    def validate_valor_mensal(cls, v: Decimal) -> Decimal:
        """Ensure valor_mensal is strictly positive.

        Args:
            v: Decimal value.

        Returns:
            Decimal: Validated value.

        Raises:
            ValueError: If value is zero or negative.
        """
        if v <= Decimal("0"):
            raise ValueError("valor_mensal deve ser maior que zero")
        return v

    @field_validator("sla_uptime_pct")
    @classmethod
    def validate_sla(cls, v: float) -> float:
        """Ensure sla_uptime_pct is between 0 and 100 inclusive.

        Args:
            v: Raw float value.

        Returns:
            float: Validated SLA percentage.

        Raises:
            ValueError: If value is outside [0, 100].
        """
        if not (0.0 <= v <= 100.0):
            raise ValueError("sla_uptime_pct deve estar entre 0 e 100")
        return v

    @model_validator(mode="after")
    def validate_dates(self) -> "ContratoBase":
        """Ensure data_fim is strictly after data_inicio.

        Returns:
            ContratoBase: Self, after validation.

        Raises:
            ValueError: If data_fim is not after data_inicio.
        """
        if self.data_fim <= self.data_inicio:
            raise ValueError("data_fim deve ser posterior a data_inicio")
        return self


# ---------------------------------------------------------------------------
# Create schema
# ---------------------------------------------------------------------------
class ContratoCreate(ContratoBase):
    """Input schema for creating a new Contrato.

    fornecedor_id is required and must reference an existing active Fornecedor.
    """

    fornecedor_id: uuid.UUID


# ---------------------------------------------------------------------------
# Update schema — all fields optional (PATCH semantics)
# ---------------------------------------------------------------------------
class ContratoUpdate(BaseModel):
    """Input schema for partial update of a Contrato (PATCH semantics)."""

    numero_contrato: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    objeto: str | None = None
    valor_mensal: Decimal | None = None
    data_inicio: date | None = None
    data_fim: date | None = None
    status: str | None = None
    sla_uptime_pct: float | None = None
    criticidade: str | None = None

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

    @field_validator("criticidade")
    @classmethod
    def validate_criticidade(cls, v: str | None) -> str | None:
        """Ensure criticidade is valid when provided.

        Args:
            v: Raw criticidade string or None.

        Returns:
            str | None: Validated criticidade or None.
        """
        if v is not None and v not in CRITICIDADE_VALIDAS:
            raise ValueError(f"criticidade deve ser um de: {sorted(CRITICIDADE_VALIDAS)}")
        return v

    @field_validator("valor_mensal")
    @classmethod
    def validate_valor_mensal(cls, v: Decimal | None) -> Decimal | None:
        """Ensure valor_mensal is positive when provided.

        Args:
            v: Decimal value or None.

        Returns:
            Decimal | None: Validated value or None.
        """
        if v is not None and v <= Decimal("0"):
            raise ValueError("valor_mensal deve ser maior que zero")
        return v

    @field_validator("sla_uptime_pct")
    @classmethod
    def validate_sla(cls, v: float | None) -> float | None:
        """Ensure sla_uptime_pct is in [0, 100] when provided.

        Args:
            v: Raw float or None.

        Returns:
            float | None: Validated value or None.
        """
        if v is not None and not (0.0 <= v <= 100.0):
            raise ValueError("sla_uptime_pct deve estar entre 0 e 100")
        return v

    @model_validator(mode="after")
    def validate_dates(self) -> "ContratoUpdate":
        """Validate date ordering only when both dates are explicitly set.

        Returns:
            ContratoUpdate: Self, after validation.

        Raises:
            ValueError: If both dates are provided and data_fim is not after data_inicio.
        """
        if self.data_inicio is not None and self.data_fim is not None:
            if self.data_fim <= self.data_inicio:
                raise ValueError("data_fim deve ser posterior a data_inicio")
        return self


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------
class ContratoResponse(BaseModel):
    """Output schema for Contrato list/write responses (no embedded fornecedor)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    fornecedor_id: uuid.UUID
    numero_contrato: str
    objeto: str
    valor_mensal: Decimal
    data_inicio: date
    data_fim: date
    status: str
    sla_uptime_pct: float
    criticidade: str
    created_at: datetime
    updated_at: datetime


class ContratoDetailResponse(ContratoResponse):
    """Output schema for GET /contratos/{id} — includes embedded fornecedor."""

    fornecedor: FornecedorResponse | None = None


class ContratoListResponse(BaseModel):
    """Paginated list of Contratos."""

    model_config = ConfigDict(from_attributes=True)

    items: list[ContratoResponse]
    total: int
    skip: int
    limit: int
