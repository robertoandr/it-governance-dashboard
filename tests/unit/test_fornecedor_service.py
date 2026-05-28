"""Unit tests for FornecedorService using the real Postgres test database.

Each test runs inside a transaction that is rolled back at the end, so no
test data leaks to other tests or persists in the database.
"""

import uuid
from collections.abc import AsyncGenerator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.models import Fornecedor
from app.models.fornecedor import FornecedorCreate, FornecedorUpdate
from app.services.exceptions import FornecedorAlreadyExists, FornecedorNotFound
from app.services.fornecedor_service import FornecedorService

DB_URL = "postgresql+asyncpg://itgov:Z66BroSKNH6LZcEFOghNRspeQH6oI2Bh@172.18.0.2:5432/itgov"

_engine = create_async_engine(DB_URL, poolclass=NullPool)
_factory = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture()
async def session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a session bound to a transaction that is rolled back after each test.

    This isolates every test from the database state without requiring a
    separate test schema or table truncation.
    """
    async with _engine.connect() as conn:
        await conn.begin()
        # Bind the session to the open connection so it participates in the same txn
        factory = async_sessionmaker(bind=conn, class_=AsyncSession, expire_on_commit=False)
        async with factory() as s:
            yield s
        await conn.rollback()


@pytest.fixture()
def service() -> FornecedorService:
    return FornecedorService()


@pytest.fixture()
def create_payload() -> FornecedorCreate:
    return FornecedorCreate(
        nome="Acme Testes",
        cnpj="11222333000181",
        categoria="Cloud",
        contato_nome="Ana",
    )


# ---------------------------------------------------------------------------
# create()
# ---------------------------------------------------------------------------
class TestCreate:
    async def test_create_returns_fornecedor(
        self,
        session: AsyncSession,
        service: FornecedorService,
        create_payload: FornecedorCreate,
    ) -> None:
        f = await service.create(session, create_payload)
        assert isinstance(f, Fornecedor)
        assert f.nome == "Acme Testes"
        assert f.cnpj == "11222333000181"
        assert f.status == "ativo"
        assert f.id is not None

    async def test_create_sets_timestamps(
        self,
        session: AsyncSession,
        service: FornecedorService,
        create_payload: FornecedorCreate,
    ) -> None:
        f = await service.create(session, create_payload)
        assert f.created_at is not None
        assert f.updated_at is not None

    async def test_create_duplicate_cnpj_raises(
        self,
        session: AsyncSession,
        service: FornecedorService,
        create_payload: FornecedorCreate,
    ) -> None:
        await service.create(session, create_payload)
        with pytest.raises(FornecedorAlreadyExists) as exc_info:
            await service.create(
                session,
                FornecedorCreate(nome="Outro", cnpj="11222333000181", categoria="Software"),
            )
        assert "11222333000181" in str(exc_info.value)


# ---------------------------------------------------------------------------
# get_by_id()
# ---------------------------------------------------------------------------
class TestGetById:
    async def test_get_existing(
        self,
        session: AsyncSession,
        service: FornecedorService,
        create_payload: FornecedorCreate,
    ) -> None:
        created = await service.create(session, create_payload)
        found = await service.get_by_id(session, created.id)
        assert found is not None
        assert found.id == created.id

    async def test_get_nonexistent_returns_none(
        self,
        session: AsyncSession,
        service: FornecedorService,
    ) -> None:
        result = await service.get_by_id(session, uuid.uuid4())
        assert result is None

    async def test_get_deleted_returns_none(
        self,
        session: AsyncSession,
        service: FornecedorService,
        create_payload: FornecedorCreate,
    ) -> None:
        created = await service.create(session, create_payload)
        await service.soft_delete(session, created.id)
        result = await service.get_by_id(session, created.id)
        assert result is None


# ---------------------------------------------------------------------------
# list()
# ---------------------------------------------------------------------------
class TestList:
    async def test_list_returns_active_only(
        self,
        session: AsyncSession,
        service: FornecedorService,
    ) -> None:
        f1 = await service.create(
            session,
            FornecedorCreate(nome="Alpha", cnpj="11222333000181", categoria="Cloud"),
        )
        f2 = await service.create(
            session,
            FornecedorCreate(nome="Beta", categoria="Software"),
        )
        await service.soft_delete(session, f2.id)

        items, total = await service.list(session)
        ids = [f.id for f in items]
        assert f1.id in ids
        assert f2.id not in ids

    async def test_list_filter_by_status(
        self,
        session: AsyncSession,
        service: FornecedorService,
    ) -> None:
        await service.create(
            session,
            FornecedorCreate(nome="Active", cnpj="11222333000181", categoria="Cloud"),
        )
        f2 = await service.create(
            session,
            FornecedorCreate(nome="Inactive", categoria="Software", status="inativo"),
        )
        items, total = await service.list(session, status="inativo")
        assert all(f.status == "inativo" for f in items)
        assert f2.id in [f.id for f in items]

    async def test_list_pagination(
        self,
        session: AsyncSession,
        service: FornecedorService,
    ) -> None:
        for i in range(3):
            await service.create(
                session,
                FornecedorCreate(nome=f"F{i}", categoria="Cloud"),
            )
        items, total = await service.list(session, skip=0, limit=2)
        assert len(items) <= 2


# ---------------------------------------------------------------------------
# update()
# ---------------------------------------------------------------------------
class TestUpdate:
    async def test_update_changes_field(
        self,
        session: AsyncSession,
        service: FornecedorService,
        create_payload: FornecedorCreate,
    ) -> None:
        created = await service.create(session, create_payload)
        updated = await service.update(
            session, created.id, FornecedorUpdate(status="inativo")
        )
        assert updated.status == "inativo"
        assert updated.nome == created.nome  # unchanged

    async def test_update_updates_timestamp(
        self,
        session: AsyncSession,
        service: FornecedorService,
        create_payload: FornecedorCreate,
    ) -> None:
        created = await service.create(session, create_payload)
        original_updated_at = created.updated_at
        import asyncio

        await asyncio.sleep(0.01)
        updated = await service.update(
            session, created.id, FornecedorUpdate(nome="Novo Nome")
        )
        assert updated.updated_at >= original_updated_at

    async def test_update_nonexistent_raises(
        self,
        session: AsyncSession,
        service: FornecedorService,
    ) -> None:
        with pytest.raises(FornecedorNotFound):
            await service.update(session, uuid.uuid4(), FornecedorUpdate(status="inativo"))


# ---------------------------------------------------------------------------
# soft_delete()
# ---------------------------------------------------------------------------
class TestSoftDelete:
    async def test_soft_delete_sets_deleted_at(
        self,
        session: AsyncSession,
        service: FornecedorService,
        create_payload: FornecedorCreate,
    ) -> None:
        created = await service.create(session, create_payload)
        result = await service.soft_delete(session, created.id)
        assert result is True
        # Verify by querying directly (bypassing the filter)
        from sqlalchemy import select

        stmt = select(Fornecedor).where(Fornecedor.id == created.id)
        raw = (await session.execute(stmt)).scalar_one()
        assert raw.deleted_at is not None

    async def test_soft_delete_nonexistent_raises(
        self,
        session: AsyncSession,
        service: FornecedorService,
    ) -> None:
        with pytest.raises(FornecedorNotFound):
            await service.soft_delete(session, uuid.uuid4())
