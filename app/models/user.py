"""SQLAlchemy User model with bcrypt password hashing."""

from __future__ import annotations

from datetime import UTC, datetime

from flask_login import UserMixin

from app.extensions import bcrypt, db

ROLES = ("admin", "gestor", "operador", "visualizador")


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id: int = db.Column(db.Integer, primary_key=True)
    name: str = db.Column(db.String(120), nullable=False)
    email: str = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash: str = db.Column(db.String(255), nullable=False)
    role: str = db.Column(db.String(20), nullable=False, default="visualizador")
    is_active: bool = db.Column(db.Boolean, nullable=False, default=True)
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
        return self.role.capitalize()

    def __repr__(self) -> str:
        return f"<User {self.email} ({self.role})>"
