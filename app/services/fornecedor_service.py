"""Business logic for the Fornecedor (vendor) resource."""

import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Fornecedor
from app.models.fornecedor import FornecedorCreate, FornecedorUpdate
from app.services.exceptions import FornecedorAlreadyExists, FornecedorNotFound

log = structlog.get_logger(__name__)


class FornecedorService:
    """CRUD operations for Fornecedor records.

    All methods are async and expect a live AsyncSession. The session
    lifetime (commit/rollback) is managed by the caller (typically the
    ``get_session`` context manager in ``app.db.session``).
    """

    async def create(
        self,
        session: AsyncSession,
        data: FornecedorCreate,
    ) -> Fornecedor:
        """Persist a new Fornecedor.

        Args:
            session: Active async database session.
            data: Validated creation payload.

        Returns:
            Fornecedor: The newly created ORM instance.

        Raises:
            FornecedorAlreadyExists: If another fornecedor with the same CNPJ exists.
        """
        fornecedor = Fornecedor(**data.model_dump())
        session.add(fornecedor)
        try:
            await session.flush()
            # Refresh to load server-side defaults (created_at, updated_at, id)
            await session.refresh(fornecedor)
        except IntegrityError as exc:
            await session.rollback()
            cnpj = data.cnpj or ""
            log.warning("fornecedor.create.duplicate_cnpj", cnpj=cnpj, error=str(exc))
            raise FornecedorAlreadyExists(cnpj) from exc

        log.info("fornecedor.created", id=str(fornecedor.id), nome=fornecedor.nome)
        return fornecedor

    async def get_by_id(
        self,
        session: AsyncSession,
        fornecedor_id: uuid.UUID,
    ) -> Fornecedor | None:
        """Fetch a single active Fornecedor by primary key.

        Args:
            session: Active async database session.
            fornecedor_id: UUID of the fornecedor to retrieve.

        Returns:
            Fornecedor | None: The ORM instance, or None if not found / deleted.
        """
        stmt = select(Fornecedor).where(
            Fornecedor.id == fornecedor_id,
            Fornecedor.deleted_at.is_(None),
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    async def list(
        self,
        session: AsyncSession,
        skip: int = 0,
        limit: int = 20,
        status: str | None = None,
        categoria: str | None = None,
    ) -> tuple[list[Fornecedor], int]:
        """Return a paginated list of active Fornecedores with total count.

        Args:
            session: Active async database session.
            skip: Number of records to skip (offset).
            limit: Maximum number of records to return.
            status: Optional filter by lifecycle status.
            categoria: Optional filter by business category.

        Returns:
            tuple[list[Fornecedor], int]: Page of results and total matching count.
        """
        base = select(Fornecedor).where(Fornecedor.deleted_at.is_(None))

        if status is not None:
            base = base.where(Fornecedor.status == status)
        if categoria is not None:
            base = base.where(Fornecedor.categoria == categoria)

        count_stmt = select(func.count()).select_from(base.subquery())
        total: int = (await session.execute(count_stmt)).scalar_one()

        items_stmt = base.order_by(Fornecedor.nome).offset(skip).limit(limit)
        items = list((await session.execute(items_stmt)).scalars().all())

        log.debug("fornecedor.list", total=total, skip=skip, limit=limit)
        return items, total

    async def update(
        self,
        session: AsyncSession,
        fornecedor_id: uuid.UUID,
        data: FornecedorUpdate,
    ) -> Fornecedor:
        """Partially update an existing Fornecedor (PATCH semantics).

        Only fields explicitly set in *data* are applied; unset fields are
        left unchanged (``model_dump(exclude_unset=True)``).

        Args:
            session: Active async database session.
            fornecedor_id: UUID of the fornecedor to update.
            data: Partial update payload.

        Returns:
            Fornecedor: The updated ORM instance.

        Raises:
            FornecedorNotFound: If the fornecedor does not exist or was deleted.
            FornecedorAlreadyExists: If the new CNPJ conflicts with an existing record.
        """
        fornecedor = await self.get_by_id(session, fornecedor_id)
        if fornecedor is None:
            raise FornecedorNotFound(fornecedor_id)

        changes = data.model_dump(exclude_unset=True)
        for field, value in changes.items():
            setattr(fornecedor, field, value)

        try:
            await session.flush()
            # Refresh to load server-side trigger values (updated_at)
            await session.refresh(fornecedor)
        except IntegrityError as exc:
            await session.rollback()
            cnpj = data.cnpj or ""
            log.warning("fornecedor.update.duplicate_cnpj", cnpj=cnpj)
            raise FornecedorAlreadyExists(cnpj) from exc

        log.info("fornecedor.updated", id=str(fornecedor_id), fields=list(changes.keys()))
        return fornecedor

    async def soft_delete(
        self,
        session: AsyncSession,
        fornecedor_id: uuid.UUID,
    ) -> bool:
        """Mark a Fornecedor as deleted by setting deleted_at.

        Args:
            session: Active async database session.
            fornecedor_id: UUID of the fornecedor to delete.

        Returns:
            bool: True if the record was found and marked deleted, False otherwise.

        Raises:
            FornecedorNotFound: If the fornecedor does not exist or was already deleted.
        """
        fornecedor = await self.get_by_id(session, fornecedor_id)
        if fornecedor is None:
            raise FornecedorNotFound(fornecedor_id)

        fornecedor.deleted_at = datetime.now(tz=UTC)
        await session.flush()

        log.info("fornecedor.deleted", id=str(fornecedor_id))
        return True


fornecedor_service = FornecedorService()
