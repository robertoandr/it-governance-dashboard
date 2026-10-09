"""SQLAlchemy User model with bcrypt password hashing."""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime

import structlog
from flask_login import UserMixin
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import OperationalError

from app import permissoes as perm
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
    # Liberado para abrir o painel da TV (/gov/tv e /gov/v1…v6). Admin sempre pode.
    ver_painel_tv: bool = db.Column(db.Boolean, nullable=False, default=False, server_default="0")
    # Ajustes de permissão que fogem do perfil: {"cftv": "alterar", "zendesk": "nenhum"}
    permissoes_json: str = db.Column("permissoes", db.Text, nullable=False, default="{}", server_default="{}")
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

    @property
    def pode_ver_painel_tv(self) -> bool:
        """Admin sempre vê o painel da TV; os demais só se liberados no cadastro."""
        return self.role == "admin" or bool(self.ver_painel_tv)

    @property
    def permissoes(self) -> dict[str, str]:
        """Ajustes de permissão por página (só o que foge do perfil)."""
        try:
            dados = json.loads(self.permissoes_json or "{}")
        except ValueError:
            return {}
        return dados if isinstance(dados, dict) else {}

    @permissoes.setter
    def permissoes(self, ajustes: dict[str, str]) -> None:
        self.permissoes_json = json.dumps(perm.limpar(ajustes, self.role), sort_keys=True)

    def nivel(self, pagina: str) -> str:
        """Nível efetivo na página: ajuste do usuário, senão o padrão do perfil.

        Args:
            pagina: Chave em ``app.permissoes.PAGINAS``.

        Returns:
            ``nenhum``, ``ver`` ou ``alterar``.
        """
        if self.super_admin:
            return perm.ALTERAR
        ajuste = self.permissoes.get(pagina)
        if ajuste in perm.NIVEIS:
            return ajuste
        conhecida = perm.POR_CHAVE.get(pagina)
        return conhecida.padrao(self.role) if conhecida else perm.NENHUM

    def pode(self, pagina: str, nivel: str = perm.VER, padrao: Iterable[str] | None = None) -> bool:
        """O usuário tem ``nivel`` na página?

        Com ajuste para a página, vale o ajuste. Sem ajuste, vale o perfil:
        ``padrao`` (os perfis que a rota aceita) ou, sem ele, o catálogo.

        Args:
            pagina: Chave da página.
            nivel: ``ver`` ou ``alterar``.
            padrao: Perfis aceitos pela ação quando não há ajuste.

        Returns:
            True se pode.
        """
        if self.super_admin:
            return True
        ajuste = self.permissoes.get(pagina)
        if ajuste in perm.NIVEIS:
            return perm.basta(ajuste, nivel)
        if padrao is not None:
            return self.role in tuple(padrao)
        return perm.basta(self.nivel(pagina), nivel)

    def __repr__(self) -> str:
        return f"<User {self.email} ({self.role})>"


def _garantir_coluna(nome: str, engine: Engine | None = None, ddl: str = "BOOLEAN NOT NULL DEFAULT 0") -> None:
    """Cria a coluna ``users.<nome>`` se ainda não existir.

    Args:
        nome: Nome da coluna.
        engine: Banco a migrar; padrão é o do Flask-SQLAlchemy.
        ddl: Tipo e padrão da coluna (booleana falsa, se omitido).
    """
    engine = engine or db.engine
    if nome in {c["name"] for c in inspect(engine).get_columns(User.__tablename__)}:
        return
    try:
        with engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE {User.__tablename__} ADD COLUMN {nome} {ddl}"))
        log.info("users.coluna_adicionada", coluna=nome)
    except OperationalError as exc:
        # Outro worker criou a coluna entre a inspeção e o ALTER
        if "duplicate column" not in str(exc).lower():
            raise


def garantir_coluna_super_admin(engine: Engine | None = None) -> None:
    """Cria a coluna ``users.super_admin`` em bancos anteriores a ela (idempotente).

    Args:
        engine: Banco a migrar; padrão é o do Flask-SQLAlchemy.
    """
    _garantir_coluna("super_admin", engine)


def garantir_coluna_painel_tv(engine: Engine | None = None) -> None:
    """Cria a coluna ``users.ver_painel_tv`` em bancos anteriores a ela (idempotente).

    Args:
        engine: Banco a migrar; padrão é o do Flask-SQLAlchemy.
    """
    _garantir_coluna("ver_painel_tv", engine)


def garantir_coluna_permissoes(engine: Engine | None = None) -> None:
    """Cria a coluna ``users.permissoes`` (sem ajustes) em bancos anteriores a ela.

    Args:
        engine: Banco a migrar; padrão é o do Flask-SQLAlchemy.
    """
    _garantir_coluna("permissoes", engine, "TEXT NOT NULL DEFAULT '{}'")


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
