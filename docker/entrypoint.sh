#!/bin/sh
set -e

# Cria tabelas do app.db (idempotente — usa db.create_all, nao Alembic)
flask db

# Cria admin@ti.local se ainda nao existir (idempotente)
flask seed-admin || true

exec gunicorn \
    --bind 0.0.0.0:5000 \
    --workers 4 \
    --worker-class gthread \
    --threads 2 \
    --timeout 30 \
    --access-logfile - \
    --error-logfile - \
    --log-level info \
    "app:create_app()"
