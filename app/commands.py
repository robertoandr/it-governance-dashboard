"""Flask CLI commands for database management."""

from __future__ import annotations

import os
import secrets
import string

import click
from flask import Flask
from flask.cli import with_appcontext


def register_commands(app: Flask) -> None:
    app.cli.add_command(create_db)
    app.cli.add_command(seed_admin)


@click.command("db")
@with_appcontext
def create_db() -> None:
    """Create all SQLAlchemy tables (app.db)."""
    from app.extensions import db

    db.create_all()
    click.echo("app.db: tabelas criadas.")


@click.command("seed-admin")
@with_appcontext
def seed_admin() -> None:
    """Create the initial admin user if it doesn't exist.

    The password comes from SEED_ADMIN_PASSWORD when set; otherwise a random
    20-character credential is generated and printed once (never stored in
    code, per CLAUDE.md regra 6).
    """
    from app.extensions import db
    from app.models.user import User

    existing = User.query.filter_by(email="admin@ti.local").first()
    if existing:
        click.echo("Admin já existe — nenhuma ação necessária.")
        return

    password = os.environ.get("SEED_ADMIN_PASSWORD")
    generated = password is None
    if generated:
        alphabet = string.ascii_letters + string.digits
        password = "".join(secrets.choice(alphabet) for _ in range(20))

    user = User(name="Admin", email="admin@ti.local", role="admin")
    user.set_password(password)
    db.session.add(user)
    db.session.commit()

    if generated:
        click.echo(f"Admin criado: admin@ti.local / {password}")
        click.echo("Senha gerada automaticamente — anote agora e troque no primeiro login.")
    else:
        click.echo("Admin criado: admin@ti.local (senha definida via SEED_ADMIN_PASSWORD)")
