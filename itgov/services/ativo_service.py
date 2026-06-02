"""Service layer for IT Asset Inventory.

Sits between the API/UI layer and AtivoDB ORM.
Validates input via Pydantic, enforces business rules, and
provides idempotent ingestion from external sources.

Aligned with ADR-008 (Asset Inventory Strategy) and ADR-0003 (LGPD).
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, NoResultFound
from sqlalchemy.orm import Session

from itgov.models.ativo import AtivoCreate, AtivoStats, AtivoUpdate
from itgov.models.db.ativo import AtivoDB

log = structlog.get_logger(__name__)


class AtivoServiceError(Exception):
    """Base exception for AtivoService."""


class AtivoNotFoundError(AtivoServiceError):
    """Asset not found or soft-deleted."""


class AtivoDuplicateError(AtivoServiceError):
    """Duplicate (nome, tipo) constraint violation."""


def _orm_kwargs(dto_dict: dict[str, Any]) -> dict[str, Any]:
    """Map Pydantic model_dump() output to AtivoDB constructor kwargs.

    Renames 'metadata' → 'metadata_' to avoid Python reserved attr clash.
    """
    result = dict(dto_dict)
    if "metadata" in result:
        result["metadata_"] = result.pop("metadata")
    return result


class AtivoService:
    """CRUD + business logic for IT Assets.

    Args:
        session: Active SQLAlchemy Session for all DB operations.

    Example:
        >>> engine = create_engine("sqlite:///itgov.db")
        >>> with Session(engine) as session:
        ...     svc = AtivoService(session)
        ...     ativo = svc.create({
        ...         "nome": "srv-prod-01", "tipo": "servidor",
        ...         "ambiente": "prod", "criticidade": "alta",
        ...         "owner": "infra@corp.com",
        ...     })
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self._log = log.bind(service="AtivoService")

    # ─── CREATE ──────────────────────────────────────────────────────

    def create(self, payload: dict[str, Any]) -> AtivoDB:
        """Create a new asset after Pydantic validation.

        Args:
            payload: Asset fields (validated via AtivoCreate).

        Returns:
            Persisted AtivoDB instance.

        Raises:
            AtivoDuplicateError: If (nome, tipo) already exists.
            ValueError: If Pydantic validation fails.
        """
        dto = AtivoCreate.model_validate(payload)
        ativo = AtivoDB(**_orm_kwargs(dto.model_dump()))
        try:
            self.session.add(ativo)
            self.session.commit()
            self.session.refresh(ativo)
            self._log.info("ativo.created", id=str(ativo.id), nome=ativo.nome, tipo=ativo.tipo)
            return ativo
        except IntegrityError as exc:
            self.session.rollback()
            self._log.warning("ativo.duplicate", nome=dto.nome, tipo=dto.tipo)
            raise AtivoDuplicateError(f"Ativo já existe: ({dto.nome!r}, {dto.tipo!r})") from exc

    # ─── READ ────────────────────────────────────────────────────────

    def get(self, ativo_id: UUID) -> AtivoDB:
        """Retrieve active (non-deleted) asset by ID.

        Args:
            ativo_id: Asset UUID.

        Returns:
            AtivoDB instance.

        Raises:
            AtivoNotFoundError: If not found or soft-deleted.
        """
        stmt = select(AtivoDB).where(AtivoDB.id == ativo_id, AtivoDB.deleted_at.is_(None))
        try:
            return self.session.execute(stmt).scalar_one()
        except NoResultFound as exc:
            raise AtivoNotFoundError(f"Ativo não encontrado: {ativo_id}") from exc

    def list(
        self,
        *,
        tipo: str | None = None,
        ambiente: str | None = None,
        criticidade: str | None = None,
        owner: str | None = None,
        include_deleted: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AtivoDB]:
        """List assets with optional filters and pagination.

        Args:
            tipo: Filter by asset type.
            ambiente: Filter by environment.
            criticidade: Filter by criticality.
            owner: Filter by owner email.
            include_deleted: Include soft-deleted records.
            limit: Max results (default 100).
            offset: Records to skip (for pagination).

        Returns:
            List of matching AtivoDB instances ordered by nome.
        """
        stmt = select(AtivoDB)
        if not include_deleted:
            stmt = stmt.where(AtivoDB.deleted_at.is_(None))
        if tipo:
            stmt = stmt.where(AtivoDB.tipo == tipo)
        if ambiente:
            stmt = stmt.where(AtivoDB.ambiente == ambiente)
        if criticidade:
            stmt = stmt.where(AtivoDB.criticidade == criticidade)
        if owner:
            stmt = stmt.where(AtivoDB.owner == owner)
        stmt = stmt.order_by(AtivoDB.nome).limit(limit).offset(offset)
        return list(self.session.execute(stmt).scalars())

    def count(
        self,
        *,
        tipo: str | None = None,
        include_deleted: bool = False,
    ) -> int:
        """Count assets matching filters.

        Args:
            tipo: Filter by asset type.
            include_deleted: Include soft-deleted records.

        Returns:
            Count of matching assets.
        """
        stmt = select(func.count(AtivoDB.id))
        if not include_deleted:
            stmt = stmt.where(AtivoDB.deleted_at.is_(None))
        if tipo:
            stmt = stmt.where(AtivoDB.tipo == tipo)
        return self.session.execute(stmt).scalar_one()

    def get_stats(self) -> AtivoStats:
        """Aggregate stats for the dashboard inventory widget.

        Returns:
            AtivoStats with counts by tipo, criticidade, ambiente,
            and alerts for sem_owner / sem_contrato.
        """
        ativos = self.list(limit=10_000)

        por_tipo: dict[str, int] = {}
        por_criticidade: dict[str, int] = {}
        por_ambiente: dict[str, int] = {}
        sem_owner = 0
        sem_contrato = 0

        for a in ativos:
            por_tipo[a.tipo] = por_tipo.get(a.tipo, 0) + 1
            por_criticidade[a.criticidade] = por_criticidade.get(a.criticidade, 0) + 1
            por_ambiente[a.ambiente] = por_ambiente.get(a.ambiente, 0) + 1
            if not a.owner:
                sem_owner += 1
            if a.contrato_id is None:
                sem_contrato += 1

        return AtivoStats(
            total=len(ativos),
            por_tipo=por_tipo,
            por_criticidade=por_criticidade,
            por_ambiente=por_ambiente,
            sem_owner=sem_owner,
            sem_contrato=sem_contrato,
        )

    # ─── UPDATE ──────────────────────────────────────────────────────

    def update(self, ativo_id: UUID, payload: dict[str, Any]) -> AtivoDB:
        """Partial update of an existing active asset.

        Args:
            ativo_id: Asset to update.
            payload: Fields to update (validated via AtivoUpdate).

        Returns:
            Updated AtivoDB instance.

        Raises:
            AtivoNotFoundError: If not found or soft-deleted.
            AtivoDuplicateError: If update would violate unique constraint.
            ValueError: If Pydantic validation fails.
        """
        ativo = self.get(ativo_id)
        dto = AtivoUpdate.model_validate(payload)
        updates = _orm_kwargs(dto.model_dump(exclude_unset=True))

        for field, value in updates.items():
            if hasattr(ativo, field):
                setattr(ativo, field, value)

        try:
            self.session.commit()
            self.session.refresh(ativo)
            self._log.info("ativo.updated", id=str(ativo_id), fields=list(updates.keys()))
            return ativo
        except IntegrityError as exc:
            self.session.rollback()
            raise AtivoDuplicateError("Update violaria unique constraint (nome, tipo)") from exc

    # ─── DELETE ──────────────────────────────────────────────────────

    def delete(self, ativo_id: UUID, *, soft: bool = True) -> None:
        """Delete an asset (soft by default per LGPD/ADR-0003).

        Args:
            ativo_id: Asset to delete.
            soft: If True, sets deleted_at; if False, physically removes.

        Raises:
            AtivoNotFoundError: If not found or already soft-deleted.
        """
        ativo = self.get(ativo_id)
        if soft:
            ativo.deleted_at = datetime.now(UTC)
            self.session.commit()
            self._log.info("ativo.soft_deleted", id=str(ativo_id))
        else:
            self.session.delete(ativo)
            self.session.commit()
            self._log.warning("ativo.hard_deleted", id=str(ativo_id))

    # ─── UPSERT ──────────────────────────────────────────────────────

    def upsert(self, payload: dict[str, Any]) -> tuple[AtivoDB, bool]:
        """Idempotent create-or-update by (nome, tipo).

        Intended for sync jobs from Zabbix, GitHub, etc.
        Reactivates soft-deleted assets if they reappear in source.

        Args:
            payload: Asset data (validated via AtivoCreate).

        Returns:
            (AtivoDB instance, was_created: bool)
        """
        dto = AtivoCreate.model_validate(payload)
        stmt = select(AtivoDB).where(AtivoDB.nome == dto.nome, AtivoDB.tipo == dto.tipo)
        existing = self.session.execute(stmt).scalar_one_or_none()

        if existing is None:
            return self.create(payload), True

        updates = _orm_kwargs(dto.model_dump(exclude={"nome", "tipo"}))
        for field, value in updates.items():
            if hasattr(existing, field):
                setattr(existing, field, value)

        if existing.deleted_at is not None:
            existing.deleted_at = None
            self._log.info("ativo.reactivated", id=str(existing.id))

        self.session.commit()
        self.session.refresh(existing)
        self._log.info("ativo.upserted", id=str(existing.id), created=False)
        return existing, False
