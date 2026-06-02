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
        import config

        kwargs: dict = {}
        if "sqlite" in config.DATABASE_URL:
            kwargs["connect_args"] = {"check_same_thread": False}
        _engine = create_engine(config.DATABASE_URL, **kwargs)
    return _engine


def get_session() -> Session:
    """Return a new Session bound to the application engine."""
    return Session(get_engine())
