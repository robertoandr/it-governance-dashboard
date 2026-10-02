"""SQLAlchemy User model with bcrypt password hashing."""

from __future__ import annotations

from datetime import UTC, datetime

import structlog
from flask_login import UserMixin
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import OperationalError

from app.extensions import bcrypt, db

log = structlog.get_logger(__name__)

ROLES = ("admin", "gestor", "operador", "visualizador")


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id: int = db.Column(db.Integer, primary_key=True)
    name: str = db.Column(db.String(120), nullable=False)
    email: str = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash: str = db.Column(db.String(255), nullable=False)
    role: str = db.Column(db.String(20), nullable=False, default="visualizador")
    is_active: bool = db.Column(db.Boolean, nullable=False, default=True)
    # Admin que aprova as alterações de cadastro/configuração dos demais.
    # É uma marca sobre o papel admin (não um papel novo): tudo que admin
    # pode, o super admin também pode.
    super_admin: bool = db.Column(db.Boolean, nullable=False, default=False, server_default="0")
    created_at: datetime = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    def set_password(self, password: str) -> None:
        self.password_hash = bcrypt.generate_password_hash(password).decode("utf-8")

    def check_password(self, password: str) -> bool:
        return bcrypt.check_password_hash(self.password_hash, password)

    @property
    def role_label(self) -> str:
        return "Super admin" if self.super_admin else self.role.capitalize()

    def __repr__(self) -> str:
        return f"<User {self.email} ({self.role})>"


def garantir_coluna_super_admin(engine: Engine | None = None) -> None:
    """Cria a coluna ``users.super_admin`` em bancos anteriores a ela (idempotente).

    Args:
        engine: Banco a migrar; padrão é o do Flask-SQLAlchemy.
    """
    engine = engine or db.engine
    if "super_admin" in {c["name"] for c in inspect(engine).get_columns(User.__tablename__)}:
        return
    try:
        with engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE {User.__tablename__} ADD COLUMN super_admin BOOLEAN NOT NULL DEFAULT 0"))
        log.info("users.coluna_super_admin_adicionada")
    except OperationalError as exc:
        # Outro worker criou a coluna entre a inspeção e o ALTER
        if "duplicate column" not in str(exc).lower():
            raise


def definir_super_admin(email: str) -> None:
    """Marca como super admin a conta com este e-mail (e só ela).

    Só age quando a conta existe; garante papel admin e desmarca qualquer
    outra conta. E-mail vazio não muda nada.

    Args:
        email: E-mail do super admin (``SUPER_ADMIN_EMAIL``).
    """
    email = email.strip().lower()
    if not email:
        return
    alvo = User.query.filter_by(email=email).first()
    if alvo is None:
        log.warning("users.super_admin_sem_conta", email=email)
        return
    mudou = False
    for outro in User.query.filter_by(super_admin=True).filter(User.id != alvo.id):
        outro.super_admin = False
        mudou = True
    if not alvo.super_admin or alvo.role != "admin":
        alvo.super_admin, alvo.role = True, "admin"
        mudou = True
    if mudou:
        db.session.commit()
        log.info("users.super_admin_definido", email=email)
