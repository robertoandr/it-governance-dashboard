"""Modelo SQLAlchemy para overrides manuais de classificação de conta M365.

Persiste APENAS as exceções manuais (override da régua automática).
Contas sem registro aqui são classificadas pela régua em tempo de execução.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from itgov.models.db.base import Base


class AccountClassificationOverride(Base):
    """Exceções manuais de classificação de conta M365.

    PK = upn (string normalizada em minúsculas).
    Guarda quem reclassificou e quando para auditoria.
    """

    __tablename__ = "account_classification_override"

    upn: Mapped[str] = mapped_column(String(320), primary_key=True)
    classificacao: Mapped[str] = mapped_column(String(20), nullable=False)
    revisado_por: Mapped[str] = mapped_column(String(200), nullable=False)
    revisado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    def __repr__(self) -> str:
        return f"<AccountClassificationOverride upn={self.upn!r} classificacao={self.classificacao!r}>"
