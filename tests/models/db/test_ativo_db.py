"""Tests for AtivoDB SQLAlchemy model."""

from __future__ import annotations

import time
from datetime import datetime
from uuid import UUID

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from itgov.models.db.ativo import AtivoDB
from itgov.models.db.base import Base


@pytest.fixture
def db_session() -> Session:
    """Provide an isolated in-memory SQLite session for each test."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _minimal(nome: str = "srv-prod-01", **kwargs) -> AtivoDB:
    """Helper — build a minimal valid AtivoDB instance."""
    defaults = {
        "tipo": "servidor",
        "ambiente": "prod",
        "criticidade": "alta",
        "owner": "infra@example.com",
    }
    defaults.update(kwargs)
    return AtivoDB(nome=nome, **defaults)


class TestAtivoDBCreation:
    def test_create_minimal(self, db_session: Session) -> None:
        """Required fields only — defaults should kick in."""
        ativo = _minimal()
        db_session.add(ativo)
        db_session.commit()
        db_session.refresh(ativo)

        assert isinstance(ativo.id, UUID)
        assert ativo.nome == "srv-prod-01"
        assert ativo.tipo == "servidor"
        assert ativo.tags == []
        assert ativo.metadata_ == {}
        assert ativo.contrato_id is None
        assert ativo.deleted_at is None
        assert ativo.is_deleted is False

    def test_create_full(self, db_session: Session) -> None:
        """All optional fields should persist correctly."""
        ativo = AtivoDB(
            nome="app-checkout",
            tipo="app",
            ambiente="prod",
            criticidade="alta",
            owner="payments@example.com",
            descricao="Microservice de checkout",
            tags=["critical", "payments"],
            metadata_={"url": "https://checkout.internal", "replicas": 3},
            contrato_id=UUID("12345678-1234-5678-1234-567812345678"),
        )
        db_session.add(ativo)
        db_session.commit()
        db_session.refresh(ativo)

        assert ativo.descricao == "Microservice de checkout"
        assert ativo.tags == ["critical", "payments"]
        assert ativo.metadata_["replicas"] == 3
        assert ativo.contrato_id == UUID("12345678-1234-5678-1234-567812345678")

    def test_repr(self, db_session: Session) -> None:
        ativo = _minimal()
        db_session.add(ativo)
        db_session.commit()
        r = repr(ativo)
        assert "srv-prod-01" in r
        assert "servidor" in r
        assert "prod" in r


class TestAtivoDBConstraints:
    def test_unique_nome_tipo(self, db_session: Session) -> None:
        """(nome, tipo) must be unique across the table."""
        db_session.add(_minimal("srv-dup"))
        db_session.add(_minimal("srv-dup"))  # duplicate
        with pytest.raises(IntegrityError):
            db_session.commit()

    def test_same_nome_different_tipo_allowed(self, db_session: Session) -> None:
        """Same nome with different tipo should be allowed."""
        db_session.add(_minimal("frontend", tipo="app"))
        db_session.add(_minimal("frontend", tipo="endpoint"))
        db_session.commit()  # must not raise

    def test_nome_required(self, db_session: Session) -> None:
        """Inserting without nome should raise IntegrityError."""
        ativo = AtivoDB(
            tipo="servidor",
            ambiente="prod",
            criticidade="alta",
            owner="infra@example.com",
        )
        db_session.add(ativo)
        with pytest.raises(IntegrityError):
            db_session.commit()

    def test_tipo_required(self, db_session: Session) -> None:
        """Inserting without tipo should raise IntegrityError."""
        ativo = AtivoDB(
            nome="srv-no-tipo",
            ambiente="prod",
            criticidade="alta",
            owner="infra@example.com",
        )
        db_session.add(ativo)
        with pytest.raises(IntegrityError):
            db_session.commit()


class TestAtivoDBTimestamps:
    def test_timestamps_auto_populated_on_create(self, db_session: Session) -> None:
        ativo = _minimal("ts-create")
        db_session.add(ativo)
        db_session.commit()
        db_session.refresh(ativo)

        assert isinstance(ativo.created_at, datetime)
        assert isinstance(ativo.updated_at, datetime)

    def test_updated_at_changes_on_update(self, db_session: Session) -> None:
        ativo = _minimal("ts-update")
        db_session.add(ativo)
        db_session.commit()
        db_session.refresh(ativo)
        original = ativo.updated_at

        time.sleep(0.01)
        ativo.nome = "ts-update-renamed"
        db_session.commit()
        db_session.refresh(ativo)

        assert ativo.updated_at > original

    def test_deleted_at_defaults_to_none(self, db_session: Session) -> None:
        ativo = _minimal("ts-soft")
        db_session.add(ativo)
        db_session.commit()
        db_session.refresh(ativo)

        assert ativo.deleted_at is None
        assert ativo.is_deleted is False

    def test_soft_delete_sets_deleted_at(self, db_session: Session) -> None:
        from datetime import UTC, datetime

        ativo = _minimal("ts-delete")
        db_session.add(ativo)
        db_session.commit()

        ativo.deleted_at = datetime.now(UTC)
        db_session.commit()
        db_session.refresh(ativo)

        assert ativo.deleted_at is not None
        assert ativo.is_deleted is True


class TestAtivoDBJSON:
    def test_tags_persist_as_list(self, db_session: Session) -> None:
        ativo = _minimal("json-tags", tags=["linux", "k8s", "prod"])
        db_session.add(ativo)
        db_session.commit()
        db_session.refresh(ativo)

        assert ativo.tags == ["linux", "k8s", "prod"]

    def test_tags_default_empty_list(self, db_session: Session) -> None:
        ativo = _minimal("json-tags-empty")
        db_session.add(ativo)
        db_session.commit()
        db_session.refresh(ativo)

        assert ativo.tags == []
        assert isinstance(ativo.tags, list)

    def test_metadata_persist_nested(self, db_session: Session) -> None:
        meta = {"ip": "10.0.0.1", "specs": {"cpu": 8, "ram_gb": 32}}
        ativo = _minimal("json-meta", metadata_=meta)
        db_session.add(ativo)
        db_session.commit()
        db_session.refresh(ativo)

        assert ativo.metadata_["specs"]["cpu"] == 8

    def test_metadata_default_empty_dict(self, db_session: Session) -> None:
        ativo = _minimal("json-meta-empty")
        db_session.add(ativo)
        db_session.commit()
        db_session.refresh(ativo)

        assert ativo.metadata_ == {}
        assert isinstance(ativo.metadata_, dict)
