"""SQLAlchemy engine and session factory.

Engine is lazily initialized on first use so that unit tests can
create in-memory databases without triggering a real connection.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

_engine: Engine | None = None


def get_engine() -> Engine:
    """Return (or lazily create) the application SQLAlchemy engine."""
    global _engine
    if _engine is None:
        import os

        # Lê DATABASE_URL do ambiente sem passar por config.py (que exige ZABBIX_PASSWORD).
        # Default alinhado com app/config.py: data/govti.db relativo ao CWD (/app no container).
        db_url = os.environ.get("DATABASE_URL") or "sqlite:///data/govti.db"
        kwargs: dict = {}
        if "sqlite" in db_url:
            kwargs["connect_args"] = {"check_same_thread": False}
        _engine = create_engine(db_url, **kwargs)
    return _engine


def get_session() -> Session:
    """Return a new Session bound to the application engine."""
    return Session(get_engine())
