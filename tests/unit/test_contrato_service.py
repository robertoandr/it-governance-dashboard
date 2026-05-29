"""Unit tests for ContratoService using the real Postgres test database.

Each test runs inside a transaction that is rolled back at the end, isolating
test data without truncation or teardown overhead.
"""

import uuid
from collections.abc import AsyncGenerator
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.models import Contrato, Fornecedor
from app.models.contrato import ContratoCreate, ContratoUpdate
from app.models.fornecedor import FornecedorCreate
from app.services.contrato_service import ContratoService
from app.services.exceptions import (
    ContratoAlreadyExists,
    ContratoNotFound,
    FornecedorNotFound,
)
from app.services.fornecedor_service import FornecedorService

DB_URL = "postgresql+asyncpg://itgov:Z66BroSKNH6LZcEFOghNRspeQH6oI2Bh@172.18.0.2:5432/itgov"

_engine = create_async_engine(DB_URL, poolclass=NullPool)
_factory = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture()
async def session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a session bound to a rolled-back transaction for test isolation."""
    async with _engine.connect() as conn:
        await conn.begin()
        factory = async_sessionmaker(bind=conn, class_=AsyncSession, expire_on_commit=False)
        async with factory() as s:
            yield s
        await conn.rollback()


@pytest.fixture()
def service() -> ContratoService:
    return ContratoService()


@pytest.fixture()
def f_service() -> FornecedorService:
    return FornecedorService()


@pytest.fixture()
async def fornecedor(session: AsyncSession, f_service: FornecedorService) -> Fornecedor:
    """Create and return a test Fornecedor for use in Contrato fixtures."""
    return await f_service.create(
        session,
        FornecedorCreate(nome="Acme Contratos", categoria="Cloud"),
    )


def _contrato_payload(fornecedor_id: uuid.UUID, numero: str = "CTR-UNIT-001") -> ContratoCreate:
    return ContratoCreate(
        fornecedor_id=fornecedor_id,
        numero_contrato=numero,
        objeto="Serviço de hosting",
        valor_mensal=Decimal("2500.00"),
        data_inicio=date(2026, 1, 1),
        data_fim=date(2027, 1, 1),
        sla_uptime_pct=99.5,
        criticidade="alta",
    )


# ---------------------------------------------------------------------------
# create()
# ---------------------------------------------------------------------------
class TestCreate:
    async def test_create_returns_contrato(
        self,
        session: AsyncSession,
        service: ContratoService,
        fornecedor: Fornecedor,
    ) -> None:
        c = await service.create(session, _contrato_payload(fornecedor.id))
        assert isinstance(c, Contrato)
        assert c.numero_contrato == "CTR-UNIT-001"
        assert c.fornecedor_id == fornecedor.id
        assert c.status == "vigente"
        assert c.id is not None

    async def test_create_sets_timestamps(
        self,
        session: AsyncSession,
        service: ContratoService,
        fornecedor: Fornecedor,
    ) -> None:
        c = await service.create(session, _contrato_payload(fornecedor.id))
        assert c.created_at is not None
        assert c.updated_at is not None

    async def test_create_duplicate_numero_raises(
        self,
        session: AsyncSession,
        service: ContratoService,
        fornecedor: Fornecedor,
    ) -> None:
        await service.create(session, _contrato_payload(fornecedor.id, "CTR-DUP"))
        with pytest.raises(ContratoAlreadyExists) as exc_info:
            await service.create(session, _contrato_payload(fornecedor.id, "CTR-DUP"))
        assert "CTR-DUP" in str(exc_info.value)

    async def test_create_with_nonexistent_fornecedor_raises(
        self,
        session: AsyncSession,
        service: ContratoService,
    ) -> None:
        with pytest.raises(FornecedorNotFound):
            await service.create(session, _contrato_payload(uuid.uuid4()))


# ---------------------------------------------------------------------------
# get_by_id()
# ---------------------------------------------------------------------------
class TestGetById:
    async def test_get_existing(
        self,
        session: AsyncSession,
        service: ContratoService,
        fornecedor: Fornecedor,
    ) -> None:
        created = await service.create(session, _contrato_payload(fornecedor.id))
        found = await service.get_by_id(session, created.id)
        assert found is not None
        assert found.id == created.id

    async def test_get_nonexistent_returns_none(
        self,
        session: AsyncSession,
        service: ContratoService,
    ) -> None:
        result = await service.get_by_id(session, uuid.uuid4())
        assert result is None

    async def test_get_deleted_returns_none(
        self,
        session: AsyncSession,
        service: ContratoService,
        fornecedor: Fornecedor,
    ) -> None:
        created = await service.create(session, _contrato_payload(fornecedor.id))
        await service.soft_delete(session, created.id)
        result = await service.get_by_id(session, created.id)
        assert result is None

    async def test_get_with_fornecedor_loads_relationship(
        self,
        session: AsyncSession,
        service: ContratoService,
        fornecedor: Fornecedor,
    ) -> None:
        created = await service.create(session, _contrato_payload(fornecedor.id))
        found = await service.get_by_id(session, created.id, with_fornecedor=True)
        assert found is not None
        assert found.fornecedor is not None
        assert found.fornecedor.id == fornecedor.id


# ---------------------------------------------------------------------------
# list()
# ---------------------------------------------------------------------------
class TestList:
    async def test_list_excludes_soft_deleted(
        self,
        session: AsyncSession,
        service: ContratoService,
        fornecedor: Fornecedor,
    ) -> None:
        c1 = await service.create(session, _contrato_payload(fornecedor.id, "CTR-A"))
        c2 = await service.create(session, _contrato_payload(fornecedor.id, "CTR-B"))
        await service.soft_delete(session, c2.id)

        items, total = await service.list(session)
        ids = [c.id for c in items]
        assert c1.id in ids
        assert c2.id not in ids

    async def test_list_filter_by_status(
        self,
        session: AsyncSession,
        service: ContratoService,
        fornecedor: Fornecedor,
    ) -> None:
        c1 = await service.create(session, _contrato_payload(fornecedor.id, "CTR-VIG"))
        c2 = await service.create(session, _contrato_payload(fornecedor.id, "CTR-ENC"))
        await service.update(session, c2.id, ContratoUpdate(status="encerrado"))

        items, _ = await service.list(session, status="encerrado")
        ids = [c.id for c in items]
        assert c2.id in ids
        assert c1.id not in ids

    async def test_list_filter_by_fornecedor(
        self,
        session: AsyncSession,
        service: ContratoService,
        fornecedor: Fornecedor,
        f_service: FornecedorService,
    ) -> None:
        other = await f_service.create(
            session, FornecedorCreate(nome="Other Vendor", categoria="Software")
        )
        c1 = await service.create(session, _contrato_payload(fornecedor.id, "CTR-F1"))
        c2 = await service.create(session, _contrato_payload(other.id, "CTR-F2"))

        items, _ = await service.list(session, fornecedor_id=fornecedor.id)
        ids = [c.id for c in items]
        assert c1.id in ids
        assert c2.id not in ids

    async def test_list_filter_vigente_em(
        self,
        session: AsyncSession,
        service: ContratoService,
        fornecedor: Fornecedor,
    ) -> None:
        # Contract active in 2026
        c_active = await service.create(
            session,
            ContratoCreate(
                fornecedor_id=fornecedor.id,
                numero_contrato="CTR-VIG-DATE",
                objeto="Active",
                valor_mensal=Decimal("100"),
                data_inicio=date(2026, 1, 1),
                data_fim=date(2026, 12, 31),
                sla_uptime_pct=99.0,
                criticidade="media",
            ),
        )
        # Contract in a different period
        c_expired = await service.create(
            session,
            ContratoCreate(
                fornecedor_id=fornecedor.id,
                numero_contrato="CTR-EXP-DATE",
                objeto="Expired",
                valor_mensal=Decimal("100"),
                data_inicio=date(2024, 1, 1),
                data_fim=date(2024, 12, 31),
                sla_uptime_pct=99.0,
                criticidade="media",
            ),
        )

        items, _ = await service.list(session, vigente_em=date(2026, 6, 15))
        ids = [c.id for c in items]
        assert c_active.id in ids
        assert c_expired.id not in ids

    async def test_list_pagination(
        self,
        session: AsyncSession,
        service: ContratoService,
        fornecedor: Fornecedor,
    ) -> None:
        for i in range(4):
            await service.create(
                session, _contrato_payload(fornecedor.id, f"CTR-PAG-{i}")
            )
        items, total = await service.list(session, skip=0, limit=2)
        assert len(items) <= 2


# ---------------------------------------------------------------------------
# list_by_fornecedor()
# ---------------------------------------------------------------------------
class TestListByFornecedor:
    async def test_returns_only_that_fornecedor(
        self,
        session: AsyncSession,
        service: ContratoService,
        fornecedor: Fornecedor,
        f_service: FornecedorService,
    ) -> None:
        other = await f_service.create(
            session, FornecedorCreate(nome="Other Vendor LBF", categoria="Software")
        )
        c1 = await service.create(session, _contrato_payload(fornecedor.id, "CTR-LBF1"))
        await service.create(session, _contrato_payload(other.id, "CTR-LBF2"))

        items, _ = await service.list_by_fornecedor(session, fornecedor.id)
        ids = [c.id for c in items]
        assert c1.id in ids
        for item in items:
            assert item.fornecedor_id == fornecedor.id

    async def test_raises_for_nonexistent_fornecedor(
        self,
        session: AsyncSession,
        service: ContratoService,
    ) -> None:
        with pytest.raises(FornecedorNotFound):
            await service.list_by_fornecedor(session, uuid.uuid4())


# ---------------------------------------------------------------------------
# update()
# ---------------------------------------------------------------------------
class TestUpdate:
    async def test_update_changes_status(
        self,
        session: AsyncSession,
        service: ContratoService,
        fornecedor: Fornecedor,
    ) -> None:
        created = await service.create(session, _contrato_payload(fornecedor.id))
        updated = await service.update(
            session, created.id, ContratoUpdate(status="encerrado")
        )
        assert updated.status == "encerrado"
        assert updated.numero_contrato == created.numero_contrato  # unchanged

    async def test_update_only_supplied_fields(
        self,
        session: AsyncSession,
        service: ContratoService,
        fornecedor: Fornecedor,
    ) -> None:
        created = await service.create(session, _contrato_payload(fornecedor.id))
        updated = await service.update(
            session, created.id, ContratoUpdate(criticidade="critica")
        )
        assert updated.criticidade == "critica"
        assert updated.sla_uptime_pct == created.sla_uptime_pct  # unchanged

    async def test_update_nonexistent_raises(
        self,
        session: AsyncSession,
        service: ContratoService,
    ) -> None:
        with pytest.raises(ContratoNotFound):
            await service.update(
                session, uuid.uuid4(), ContratoUpdate(status="encerrado")
            )

    async def test_update_duplicate_numero_raises(
        self,
        session: AsyncSession,
        service: ContratoService,
        fornecedor: Fornecedor,
    ) -> None:
        c1 = await service.create(session, _contrato_payload(fornecedor.id, "CTR-DUP-A"))
        c2 = await service.create(session, _contrato_payload(fornecedor.id, "CTR-DUP-B"))
        with pytest.raises(ContratoAlreadyExists):
            await service.update(
                session, c2.id, ContratoUpdate(numero_contrato="CTR-DUP-A")
            )


# ---------------------------------------------------------------------------
# soft_delete()
# ---------------------------------------------------------------------------
class TestSoftDelete:
    async def test_soft_delete_sets_deleted_at(
        self,
        session: AsyncSession,
        service: ContratoService,
        fornecedor: Fornecedor,
    ) -> None:
        from sqlalchemy import select

        created = await service.create(session, _contrato_payload(fornecedor.id))
        result = await service.soft_delete(session, created.id)
        assert result is True

        stmt = select(Contrato).where(Contrato.id == created.id)
        raw = (await session.execute(stmt)).scalar_one()
        assert raw.deleted_at is not None

    async def test_soft_delete_nonexistent_raises(
        self,
        session: AsyncSession,
        service: ContratoService,
    ) -> None:
        with pytest.raises(ContratoNotFound):
            await service.soft_delete(session, uuid.uuid4())

    async def test_double_delete_raises(
        self,
        session: AsyncSession,
        service: ContratoService,
        fornecedor: Fornecedor,
    ) -> None:
        created = await service.create(session, _contrato_payload(fornecedor.id))
        await service.soft_delete(session, created.id)
        with pytest.raises(ContratoNotFound):
            await service.soft_delete(session, created.id)
