"""SQLAlchemy declarative base for all DB models."""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy ORM models.

    Centralized here to enable Alembic autogenerate and consistent metadata.
    All persistent models in the application should inherit from this class.
    """
