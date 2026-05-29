"""Async SQLAlchemy engine and session factory."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

log = structlog.get_logger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _get_engine() -> AsyncEngine:
    """Return the module-level engine, creating it on first call.

    Returns:
        AsyncEngine: SQLAlchemy async engine.
    """
    global _engine  # noqa: PLW0603
    if _engine is None:
        from app.config import get_settings

        settings = get_settings()
        url = str(settings.database_url)
        # NullPool avoids cross-event-loop errors when each Flask request
        # creates its own asyncio.run() loop (Flask-RESTX sync Resource pattern).
        _engine = create_async_engine(
            url,
            poolclass=NullPool,
            echo=settings.debug,
        )
        log.info("db.engine.created", url=url.split("@")[-1])
    return _engine


def _get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the module-level session factory, creating it on first call.

    Returns:
        async_sessionmaker: configured async session factory.
    """
    global _session_factory  # noqa: PLW0603
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=_get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _session_factory


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Async context manager that yields a database session.

    Commits on success, rolls back on exception, and always closes the session.

    Yields:
        AsyncSession: an active SQLAlchemy async session.

    Raises:
        Exception: re-raises any exception after rollback.
    """
    factory = _get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            log.exception("db.session.rollback")
            raise


def reset_engine() -> None:
    """Reset the module-level engine and session factory (for tests).

    This is called from test fixtures to ensure each test suite gets a
    fresh engine pointed at the correct test database URL.
    """
    global _engine, _session_factory  # noqa: PLW0603
    _engine = None
    _session_factory = None
