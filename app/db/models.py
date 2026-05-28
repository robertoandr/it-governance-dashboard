"""SQLAlchemy ORM models for the IT Governance Dashboard."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


class TimestampMixin:
    """Adds created_at, updated_at, and deleted_at to a model."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )


class Fornecedor(TimestampMixin, Base):
    """Vendor master record.

    Attributes:
        id: UUID primary key.
        nome: Legal or commercial name.
        cnpj: Brazilian tax ID (unique, may be null for foreign vendors).
        categoria: Business category (Cloud, Software, Telecom, etc.).
        status: Lifecycle status — ativo, inativo, or suspenso.
        contato_nome: Primary contact name.
        contato_email: Primary contact email.
        contato_telefone: Primary contact phone.
        suporte_24x7: Whether the vendor provides 24/7 support.
        observacoes: Free-text notes.
        contratos: Related contracts (one-to-many).
    """

    __tablename__ = "fornecedores"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    nome: Mapped[str] = mapped_column(Text, nullable=False)
    cnpj: Mapped[str | None] = mapped_column(String(18), unique=True, nullable=True)
    categoria: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="ativo")
    contato_nome: Mapped[str | None] = mapped_column(Text, nullable=True)
    contato_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    contato_telefone: Mapped[str | None] = mapped_column(Text, nullable=True)
    suporte_24x7: Mapped[bool] = mapped_column(Boolean, default=False)
    observacoes: Mapped[str | None] = mapped_column(Text, nullable=True)

    contratos: Mapped[list["Contrato"]] = relationship(
        "Contrato",
        back_populates="fornecedor",
        lazy="select",
    )


class Contrato(TimestampMixin, Base):
    """Contract linked to a vendor.

    Attributes:
        id: UUID primary key.
        fornecedor_id: FK to fornecedores.
        numero: Contract reference number.
        descricao: Optional description.
        data_inicio: Contract start date.
        data_fim: Contract end date.
        valor_mensal: Monthly value in the contract currency.
        moeda: Currency code (default BRL).
        sla_prometido_pct: Promised SLA percentage (e.g. 99.90).
        renovacao_auto: Whether the contract auto-renews.
        anexo_path: Path to the attached PDF.
        status: Lifecycle status — ativo, inativo, encerrado.
        fornecedor: Related vendor (many-to-one).
    """

    __tablename__ = "contratos"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    fornecedor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("fornecedores.id", ondelete="RESTRICT"),
        nullable=False,
    )
    numero: Mapped[str] = mapped_column(Text, nullable=False)
    descricao: Mapped[str | None] = mapped_column(Text, nullable=True)
    data_inicio: Mapped[datetime] = mapped_column(Date, nullable=False)
    data_fim: Mapped[datetime] = mapped_column(Date, nullable=False)
    valor_mensal: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    moeda: Mapped[str] = mapped_column(String(3), default="BRL")
    sla_prometido_pct: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    renovacao_auto: Mapped[bool] = mapped_column(Boolean, default=False)
    anexo_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="ativo")

    fornecedor: Mapped["Fornecedor"] = relationship(
        "Fornecedor",
        back_populates="contratos",
    )


class ContratoEvento(Base):
    """Time-series event record for a contract (TimescaleDB hypertable).

    Attributes:
        time: Event timestamp (hypertable partition key).
        contrato_id: FK to contratos.
        tipo: Event type — alerta_vencimento, renovacao, sla_breach.
        payload: Optional JSON payload.
    """

    __tablename__ = "contratos_eventos"

    time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        primary_key=True,
        server_default=func.now(),
    )
    contrato_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("contratos.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    tipo: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
