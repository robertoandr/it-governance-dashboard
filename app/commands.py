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
    app.cli.add_command(importar_canais_cftv)


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


_SITUACOES_CANAL = ("ativo", "vazio", "outro")


@click.command("cftv-importar-canais")
@click.argument("arquivo", type=click.Path(exists=True, dir_okay=False))
@click.option("--origem", default="", help="De onde veio (ex.: reconferencia-2026-10-14).")
@with_appcontext
def importar_canais_cftv(arquivo: str, origem: str) -> None:
    """Importa a conferência de canais do CFTV (JSON exportado da página de conferência).

    Cada item tem ``ip_gravador``, ``canal``, ``marca`` (ativo/vazio/outro) e,
    opcionais, ``analogica``, ``ip_real``, ``equipamento``, ``imagem`` e ``em``.
    Canal já cadastrado é atualizado; os outros canais ficam como estão.
    """
    import json
    from datetime import datetime

    from app.extensions import db
    from app.models.unidade import CanalCftv

    with open(arquivo, encoding="utf-8") as f:
        marcas = json.load(f)
    existentes = {(c.ip_gravador, c.canal): c for c in CanalCftv.query.all()}
    novos = alterados = ignorados = 0
    for m in marcas:
        situacao = str(m.get("marca", "")).strip()
        ip = str(m.get("ip_gravador", "")).strip()
        canal = m.get("canal")
        if situacao not in _SITUACOES_CANAL or not ip or not isinstance(canal, int):
            ignorados += 1
            continue
        try:
            quando = datetime.fromisoformat(str(m["em"]).replace("Z", "+00:00")) if m.get("em") else None
        except ValueError:
            quando = None
        canal_db = existentes.get((ip, canal))
        if canal_db is None:
            canal_db = CanalCftv(ip_gravador=ip, canal=canal)
            db.session.add(canal_db)
            novos += 1
        else:
            alterados += 1
        canal_db.situacao = situacao
        canal_db.analogica = bool(m.get("analogica")) and situacao == "ativo"
        canal_db.ip_camera = str(m.get("ip_real") or "").strip()[:45] if situacao == "ativo" else ""
        canal_db.equipamento = str(m.get("equipamento") or "").strip()[:120]
        canal_db.imagem = m.get("imagem") if m.get("imagem") in ("ok", "sem") else ""
        canal_db.origem = origem[:40]
        canal_db.conferido_em = quando
    db.session.commit()
    click.echo(f"Canais: {novos} novos, {alterados} atualizados, {ignorados} ignorados.")
