"""Tests for AtivoService."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from itgov.models.db.base import Base
from itgov.services.ativo_service import (
    AtivoDuplicateError,
    AtivoNotFoundError,
    AtivoService,
)


@pytest.fixture
def session() -> Session:
    """Isolated in-memory SQLite session per test."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture
def svc(session: Session) -> AtivoService:
    return AtivoService(session)


@pytest.fixture
def payload() -> dict:
    return {
        "nome": "srv-prod-01",
        "tipo": "servidor",
        "ambiente": "prod",
        "criticidade": "alta",
        "owner": "infra@corp.com",
    }


def _p(base: dict, **overrides) -> dict:
    """Clone payload dict with field overrides."""
    return {**base, **overrides}


# ─── CREATE ──────────────────────────────────────────────────────────


class TestCreate:
    def test_create_returns_persisted_ativo(self, svc, payload):
        ativo = svc.create(payload)
        assert ativo.id is not None
        assert ativo.nome == "srv-prod-01"
        assert ativo.tipo == "servidor"
        assert ativo.deleted_at is None

    def test_create_defaults_tags_and_metadata(self, svc, payload):
        ativo = svc.create(payload)
        assert ativo.tags == []
        assert ativo.metadata_ == {}

    def test_create_with_optional_fields(self, svc, payload):
        ativo = svc.create(
            _p(
                payload,
                tags=["linux", "k8s"],
                metadata={"ip": "10.0.0.1"},
            )
        )
        assert ativo.tags == ["linux", "k8s"]
        assert ativo.metadata_["ip"] == "10.0.0.1"

    def test_create_duplicate_raises(self, svc, payload):
        svc.create(payload)
        with pytest.raises(AtivoDuplicateError):
            svc.create(payload)

    def test_create_invalid_tipo_raises(self, svc, payload):
        with pytest.raises(ValueError):
            svc.create(_p(payload, tipo="invalido"))

    def test_create_invalid_ambiente_raises(self, svc, payload):
        with pytest.raises(ValueError):
            svc.create(_p(payload, ambiente="producao"))

    def test_create_invalid_owner_raises(self, svc, payload):
        with pytest.raises(ValueError):
            svc.create(_p(payload, owner="nao-e-email"))


# ─── GET ─────────────────────────────────────────────────────────────


class TestGet:
    def test_get_existing(self, svc, payload):
        created = svc.create(payload)
        found = svc.get(created.id)
        assert found.id == created.id

    def test_get_nonexistent_raises(self, svc):
        with pytest.raises(AtivoNotFoundError):
            svc.get(uuid4())

    def test_get_soft_deleted_raises(self, svc, payload):
        created = svc.create(payload)
        svc.delete(created.id)
        with pytest.raises(AtivoNotFoundError):
            svc.get(created.id)


# ─── LIST ────────────────────────────────────────────────────────────


class TestList:
    def test_list_empty(self, svc):
        assert svc.list() == []

    def test_list_returns_all_active(self, svc, payload):
        svc.create(payload)
        svc.create(_p(payload, nome="srv-02"))
        assert len(svc.list()) == 2

    def test_list_filter_by_tipo(self, svc, payload):
        svc.create(payload)
        svc.create(_p(payload, nome="app-01", tipo="app"))
        assert len(svc.list(tipo="servidor")) == 1
        assert len(svc.list(tipo="app")) == 1

    def test_list_filter_by_ambiente(self, svc, payload):
        svc.create(payload)
        svc.create(_p(payload, nome="srv-hml", ambiente="hml"))
        assert len(svc.list(ambiente="prod")) == 1
        assert len(svc.list(ambiente="hml")) == 1

    def test_list_filter_by_criticidade(self, svc, payload):
        svc.create(payload)
        svc.create(_p(payload, nome="srv-02", criticidade="baixa"))
        assert len(svc.list(criticidade="alta")) == 1
        assert len(svc.list(criticidade="baixa")) == 1

    def test_list_filter_by_owner(self, svc, payload):
        svc.create(payload)
        svc.create(_p(payload, nome="srv-02", owner="ops@corp.com"))
        assert len(svc.list(owner="infra@corp.com")) == 1

    def test_list_excludes_deleted_by_default(self, svc, payload):
        created = svc.create(payload)
        svc.delete(created.id)
        assert svc.list() == []

    def test_list_include_deleted(self, svc, payload):
        created = svc.create(payload)
        svc.delete(created.id)
        assert len(svc.list(include_deleted=True)) == 1

    def test_list_pagination(self, svc, payload):
        for i in range(5):
            svc.create(_p(payload, nome=f"srv-{i:02d}"))
        page1 = svc.list(limit=2, offset=0)
        page2 = svc.list(limit=2, offset=2)
        assert len(page1) == 2
        assert len(page2) == 2
        assert {a.id for a in page1}.isdisjoint({a.id for a in page2})

    def test_list_ordered_by_nome(self, svc, payload):
        svc.create(_p(payload, nome="zzz-srv"))
        svc.create(_p(payload, nome="aaa-srv"))
        results = svc.list()
        assert results[0].nome == "aaa-srv"
        assert results[1].nome == "zzz-srv"


# ─── COUNT ───────────────────────────────────────────────────────────


class TestCount:
    def test_count_empty(self, svc):
        assert svc.count() == 0

    def test_count_active(self, svc, payload):
        svc.create(payload)
        svc.create(_p(payload, nome="srv-02"))
        assert svc.count() == 2

    def test_count_by_tipo(self, svc, payload):
        svc.create(payload)
        svc.create(_p(payload, nome="app-01", tipo="app"))
        assert svc.count(tipo="servidor") == 1
        assert svc.count(tipo="app") == 1

    def test_count_excludes_deleted_by_default(self, svc, payload):
        created = svc.create(payload)
        svc.delete(created.id)
        assert svc.count() == 0
        assert svc.count(include_deleted=True) == 1


# ─── UPDATE ──────────────────────────────────────────────────────────


class TestUpdate:
    def test_update_field(self, svc, payload):
        created = svc.create(payload)
        updated = svc.update(created.id, {"criticidade": "baixa"})
        assert updated.criticidade == "baixa"
        assert updated.nome == "srv-prod-01"  # unchanged

    def test_update_multiple_fields(self, svc, payload):
        created = svc.create(payload)
        updated = svc.update(created.id, {"ambiente": "hml", "criticidade": "media"})
        assert updated.ambiente == "hml"
        assert updated.criticidade == "media"

    def test_update_nonexistent_raises(self, svc):
        with pytest.raises(AtivoNotFoundError):
            svc.update(uuid4(), {"criticidade": "baixa"})

    def test_update_invalid_value_raises(self, svc, payload):
        created = svc.create(payload)
        with pytest.raises(ValueError):
            svc.update(created.id, {"ambiente": "producao"})

    def test_update_metadata(self, svc, payload):
        created = svc.create(payload)
        updated = svc.update(created.id, {"metadata": {"ip": "10.0.0.1"}})
        assert updated.metadata_["ip"] == "10.0.0.1"


# ─── DELETE ──────────────────────────────────────────────────────────


class TestDelete:
    def test_soft_delete_sets_deleted_at(self, svc, payload):
        created = svc.create(payload)
        svc.delete(created.id)
        results = svc.list(include_deleted=True)
        assert results[0].deleted_at is not None
        assert results[0].is_deleted is True

    def test_soft_delete_hides_from_normal_list(self, svc, payload):
        created = svc.create(payload)
        svc.delete(created.id)
        assert svc.list() == []

    def test_hard_delete_removes_permanently(self, svc, payload):
        created = svc.create(payload)
        svc.delete(created.id, soft=False)
        assert svc.list(include_deleted=True) == []

    def test_delete_nonexistent_raises(self, svc):
        with pytest.raises(AtivoNotFoundError):
            svc.delete(uuid4())


# ─── UPSERT ──────────────────────────────────────────────────────────


class TestUpsert:
    def test_upsert_creates_when_new(self, svc, payload):
        ativo, created = svc.upsert(payload)
        assert created is True
        assert ativo.id is not None

    def test_upsert_updates_when_existing(self, svc, payload):
        svc.create(payload)
        updated, created = svc.upsert(_p(payload, criticidade="baixa"))
        assert created is False
        assert updated.criticidade == "baixa"

    def test_upsert_deduplication_by_nome_tipo(self, svc, payload):
        svc.upsert(payload)
        svc.upsert(payload)
        assert svc.count() == 1

    def test_upsert_reactivates_soft_deleted(self, svc, payload):
        created = svc.create(payload)
        svc.delete(created.id)
        assert svc.count() == 0

        reactivated, was_created = svc.upsert(payload)
        assert was_created is False
        assert reactivated.deleted_at is None
        assert svc.count() == 1

    def test_upsert_same_nome_different_tipo_creates_two(self, svc, payload):
        _, c1 = svc.upsert(payload)
        _, c2 = svc.upsert(_p(payload, tipo="app"))
        assert c1 is True
        assert c2 is True
        assert svc.count() == 2


# ─── GET_STATS ───────────────────────────────────────────────────────


class TestGetStats:
    def test_stats_empty(self, svc):
        stats = svc.get_stats()
        assert stats.total == 0
        assert stats.por_tipo == {}
        assert stats.sem_contrato == 0

    def test_stats_aggregates_correctly(self, svc, payload):
        svc.create(payload)
        svc.create(_p(payload, nome="srv-02", criticidade="baixa"))
        svc.create(_p(payload, nome="app-01", tipo="app"))

        stats = svc.get_stats()
        assert stats.total == 3
        assert stats.por_tipo["servidor"] == 2
        assert stats.por_tipo["app"] == 1
        assert stats.por_criticidade["alta"] == 2
        assert stats.por_criticidade["baixa"] == 1

    def test_stats_sem_contrato(self, svc, payload):
        svc.create(payload)
        stats = svc.get_stats()
        assert stats.sem_contrato == 1

    def test_stats_excludes_deleted(self, svc, payload):
        created = svc.create(payload)
        svc.delete(created.id)
        stats = svc.get_stats()
        assert stats.total == 0
