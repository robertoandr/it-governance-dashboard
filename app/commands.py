"""Flask CLI commands for database management."""

from __future__ import annotations

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
    """Create the initial admin user if it doesn't exist."""
    from app.extensions import db
    from app.models.user import User

    existing = User.query.filter_by(email="admin@ti.local").first()
    if existing:
        click.echo("Admin já existe — nenhuma ação necessária.")
        return

    user = User(name="Admin", email="admin@ti.local", role="admin")
    user.set_password("Admin@123")
    db.session.add(user)
    db.session.commit()
    click.echo("Admin criado: admin@ti.local / Admin@123")
