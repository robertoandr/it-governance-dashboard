"""Business logic for the Contrato (contract) resource."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import structlog
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Contrato, Fornecedor
from app.models.contrato import ContratoCreate, ContratoUpdate
from app.services.exceptions import (
    ContratoAlreadyExists,
    ContratoNotFound,
    FornecedorNotFound,
)

log = structlog.get_logger(__name__)


class ContratoService:
    """CRUD operations for Contrato records.

    All methods are async and expect a live AsyncSession. Session lifetime
    is managed by the caller via ``get_session`` context manager.
    """

    async def _assert_fornecedor_active(
        self,
        session: AsyncSession,
        fornecedor_id: uuid.UUID,
    ) -> Fornecedor:
        """Verify that a Fornecedor exists and is not soft-deleted.

        Args:
            session: Active async database session.
            fornecedor_id: UUID to verify.

        Returns:
            Fornecedor: The active ORM instance.

        Raises:
            FornecedorNotFound: If the fornecedor does not exist or was deleted.
        """
        stmt = select(Fornecedor).where(
            Fornecedor.id == fornecedor_id,
            Fornecedor.deleted_at.is_(None),
        )
        result = await session.execute(stmt)
        fornecedor = result.scalar_one_or_none()
        if fornecedor is None:
            raise FornecedorNotFound(fornecedor_id)
        return fornecedor

    async def create(
        self,
        session: AsyncSession,
        data: ContratoCreate,
    ) -> Contrato:
        """Persist a new Contrato.

        Args:
            session: Active async database session.
            data: Validated creation payload.

        Returns:
            Contrato: The newly created ORM instance.

        Raises:
            FornecedorNotFound: If the referenced fornecedor does not exist.
            ContratoAlreadyExists: If a contrato with the same numero_contrato exists.
        """
        await self._assert_fornecedor_active(session, data.fornecedor_id)

        contrato = Contrato(**data.model_dump())
        session.add(contrato)
        try:
            await session.flush()
            await session.refresh(contrato)
        except IntegrityError as exc:
            await session.rollback()
            log.warning(
                "contrato.create.duplicate_numero",
                numero=data.numero_contrato,
                error=str(exc),
            )
            raise ContratoAlreadyExists(data.numero_contrato) from exc

        log.info(
            "contrato.created",
            id=str(contrato.id),
            numero=contrato.numero_contrato,
            fornecedor_id=str(data.fornecedor_id),
        )
        return contrato

    async def get_by_id(
        self,
        session: AsyncSession,
        contrato_id: uuid.UUID,
        *,
        with_fornecedor: bool = False,
    ) -> Contrato | None:
        """Fetch a single active Contrato by primary key.

        Args:
            session: Active async database session.
            contrato_id: UUID of the contrato to retrieve.
            with_fornecedor: When True, eagerly loads the related Fornecedor.

        Returns:
            Contrato | None: The ORM instance, or None if not found / deleted.
        """
        stmt = select(Contrato).where(
            Contrato.id == contrato_id,
            Contrato.deleted_at.is_(None),
        )
        if with_fornecedor:
            stmt = stmt.options(selectinload(Contrato.fornecedor))

        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    async def list(
        self,
        session: AsyncSession,
        skip: int = 0,
        limit: int = 20,
        status: str | None = None,
        fornecedor_id: uuid.UUID | None = None,
        criticidade: str | None = None,
        vigente_em: date | None = None,
    ) -> tuple[list[Contrato], int]:
        """Return a paginated list of active Contratos with total count.

        Args:
            session: Active async database session.
            skip: Number of records to skip (offset).
            limit: Maximum number of records to return.
            status: Optional filter by lifecycle status.
            fornecedor_id: Optional filter by vendor UUID.
            criticidade: Optional filter by criticality level.
            vigente_em: Optional date filter — returns contracts whose period
                contains the given date (data_inicio <= date <= data_fim).

        Returns:
            tuple[list[Contrato], int]: Page of results and total matching count.
        """
        base = select(Contrato).where(Contrato.deleted_at.is_(None))

        if status is not None:
            base = base.where(Contrato.status == status)
        if fornecedor_id is not None:
            base = base.where(Contrato.fornecedor_id == fornecedor_id)
        if criticidade is not None:
            base = base.where(Contrato.criticidade == criticidade)
        if vigente_em is not None:
            base = base.where(
                Contrato.data_inicio <= vigente_em,
                Contrato.data_fim >= vigente_em,
            )

        count_stmt = select(func.count()).select_from(base.subquery())
        total: int = (await session.execute(count_stmt)).scalar_one()

        items_stmt = base.order_by(Contrato.data_fim).offset(skip).limit(limit)
        items = list((await session.execute(items_stmt)).scalars().all())

        log.debug("contrato.list", total=total, skip=skip, limit=limit)
        return items, total

    async def list_by_fornecedor(
        self,
        session: AsyncSession,
        fornecedor_id: uuid.UUID,
        skip: int = 0,
        limit: int = 20,
    ) -> tuple[list[Contrato], int]:
        """Return paginated active contracts for a specific fornecedor.

        Args:
            session: Active async database session.
            fornecedor_id: UUID of the vendor whose contracts to list.
            skip: Number of records to skip.
            limit: Maximum number of records to return.

        Returns:
            tuple[list[Contrato], int]: Page of results and total count.

        Raises:
            FornecedorNotFound: If the fornecedor does not exist or was deleted.
        """
        await self._assert_fornecedor_active(session, fornecedor_id)
        return await self.list(session, skip=skip, limit=limit, fornecedor_id=fornecedor_id)

    async def update(
        self,
        session: AsyncSession,
        contrato_id: uuid.UUID,
        data: ContratoUpdate,
    ) -> Contrato:
        """Partially update an existing Contrato (PATCH semantics).

        Only fields explicitly set in *data* are applied; unset fields are
        left unchanged (``model_dump(exclude_unset=True)``).

        Args:
            session: Active async database session.
            contrato_id: UUID of the contrato to update.
            data: Partial update payload.

        Returns:
            Contrato: The updated ORM instance.

        Raises:
            ContratoNotFound: If the contrato does not exist or was deleted.
            ContratoAlreadyExists: If the new numero_contrato conflicts with another.
        """
        contrato = await self.get_by_id(session, contrato_id)
        if contrato is None:
            raise ContratoNotFound(contrato_id)

        changes = data.model_dump(exclude_unset=True)
        for field, value in changes.items():
            setattr(contrato, field, value)

        try:
            await session.flush()
            await session.refresh(contrato)
        except IntegrityError as exc:
            await session.rollback()
            numero = data.numero_contrato or ""
            log.warning("contrato.update.duplicate_numero", numero=numero)
            raise ContratoAlreadyExists(numero) from exc

        log.info(
            "contrato.updated",
            id=str(contrato_id),
            fields=list(changes.keys()),
            fornecedor_id=str(contrato.fornecedor_id),
        )
        return contrato

    async def soft_delete(
        self,
        session: AsyncSession,
        contrato_id: uuid.UUID,
    ) -> bool:
        """Mark a Contrato as deleted by setting deleted_at.

        Args:
            session: Active async database session.
            contrato_id: UUID of the contrato to delete.

        Returns:
            bool: True if the record was found and marked deleted.

        Raises:
            ContratoNotFound: If the contrato does not exist or was already deleted.
        """
        contrato = await self.get_by_id(session, contrato_id)
        if contrato is None:
            raise ContratoNotFound(contrato_id)

        contrato.deleted_at = datetime.now(tz=UTC)
        await session.flush()

        log.info(
            "contrato.deleted",
            id=str(contrato_id),
            fornecedor_id=str(contrato.fornecedor_id),
        )
        return True


contrato_service = ContratoService()
