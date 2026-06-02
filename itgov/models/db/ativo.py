"""Database schema for IT Asset Inventory.

Mirrors the Pydantic Ativo model for persistence concerns.
Aligned with ADR-008 (Asset Inventory Strategy).

Design decisions:
- Unique compound on (nome, tipo) enforces the business invariant from
  the Pydantic edge contract (pair must be unique across the database).
- Soft delete via deleted_at keeps audit trail intact (ADR-0003, LGPD).
- tags stored as JSON array to match Pydantic list[str] contract.
- metadata stored as JSON dict for per-tipo flexible attributes.
- contrato_id is a nullable UUID FK stub — FK constraint added in migration
  once the fornecedores table is confirmed stable.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from itgov.models.db.base import Base

_TIPOS_VALIDOS = ("servidor", "switch", "app", "licenca", "endpoint")
_AMBIENTES_VALIDOS = ("prod", "hml", "dev")
_CRITICIDADES_VALIDAS = ("alta", "media", "baixa")


class AtivoDB(Base):
    """SQLAlchemy ORM model for IT Assets.

    Persists assets managed via the Inventário de Ativos module.
    Deduplication is enforced at the DB level via a unique index on
    (nome, tipo), matching the invariant documented in the Pydantic model.

    Attributes:
        id: Unique identifier (UUID v4), generated on insert.
        nome: Human-readable asset name (3-100 chars), indexed.
        tipo: Asset type — servidor | switch | app | licenca | endpoint.
        ambiente: Deployment environment — prod | hml | dev.
        criticidade: Business criticality — alta | media | baixa.
        owner: Email of the responsible engineer.
        descricao: Optional free-text description.
        tags: JSON array of lowercase string labels.
        metadata: JSON dict for tipo-specific attributes (e.g. IP, vendor).
        contrato_id: Optional FK to fornecedores/contratos (Hero V1.0).
        created_at: Audit timestamp — UTC creation time.
        updated_at: Audit timestamp — UTC last modification.
        deleted_at: Soft delete marker — NULL means active, datetime = removed.

    Example:
        >>> ativo = AtivoDB(
        ...     nome="srv-prod-01",
        ...     tipo="servidor",
        ...     ambiente="prod",
        ...     criticidade="alta",
        ...     owner="infra@example.com",
        ...     tags=["linux", "k8s"],
        ...     metadata={"ip": "10.0.0.1"},
        ... )
    """

    __tablename__ = "ativos"

    # ─── Identity ────────────────────────────────────────────────────
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    nome: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    descricao: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ─── Classification ──────────────────────────────────────────────
    tipo: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    ambiente: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    criticidade: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    owner: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    # ─── Flexible attributes ──────────────────────────────────────────
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSON,
        nullable=False,
        default=dict,
    )

    # ─── Relationships ────────────────────────────────────────────────
    contrato_id: Mapped[UUID | None] = mapped_column(nullable=True)

    # ─── Audit ───────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )

    # ─── Indexes & Constraints ────────────────────────────────────────
    __table_args__ = (
        Index("ix_ativos_nome_tipo", "nome", "tipo", unique=True),
        Index("ix_ativos_tipo_ambiente", "tipo", "ambiente"),
        Index("ix_ativos_deleted_at", "deleted_at"),
    )

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def __repr__(self) -> str:
        return f"<AtivoDB(id={self.id!s:.8}, nome={self.nome!r}, tipo={self.tipo!r}, ambiente={self.ambiente!r})>"
