"""Singleton de poller via PostgreSQL advisory lock.

Garante que apenas UM worker Gunicorn execute o AlertPoller, mesmo com N workers
rodando em paralelo. A conexão que obtém o lock é mantida viva indefinidamente
(referência em _lock_conn) porque o pg_advisory_lock é automaticamente liberado
quando a conexão é fechada.

Em SQLite (dev/teste) não existe pg_try_advisory_lock — o lock é concedido
automaticamente (processo único, sem concorrência entre workers).
"""

from __future__ import annotations

import os
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.engine import Connection

from itgov.db.session import get_engine

log = structlog.get_logger(__name__)

# Chave fixa do poller — valor arbitrário, deve ser único no namespace da app.
LOCK_KEY = 916_273

# Referência global à conexão PG que segura o lock.
# Nunca fechar enquanto o worker viver.
_lock_conn: Connection | None = None


def try_acquire_poller_lock() -> bool:
    """Tenta adquirir o advisory lock global de sessão.

    Returns:
        True  → este worker é o dono do poller (lock adquirido ou SQLite).
        False → outro worker já tem o lock; este worker NÃO deve rodar o poller.

    A conexão PG é mantida aberta para preservar o lock enquanto o worker viver.
    Em SQLite retorna True imediatamente (dev/CI — processo único).
    """
    global _lock_conn

    engine = get_engine()

    # SQLite não possui advisory locks — bypass automático.
    if "sqlite" in str(engine.url).lower():
        log.info(
            "advisory_lock_sqlite_bypass",
            pid=os.getpid(),
            motivo="SQLite não suporta advisory locks — poller sempre ativo",
        )
        return True

    conn: Any = engine.connect()
    try:
        got: bool = conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY}).scalar()
    except Exception:
        conn.close()
        raise

    if got:
        _lock_conn = conn  # mantém conexão viva → lock persiste
        log.info(
            "advisory_lock_adquirido",
            pid=os.getpid(),
            lock_key=LOCK_KEY,
            mensagem="Este worker é o poller.",
        )
        return True

    conn.close()
    log.info(
        "advisory_lock_ocupado",
        pid=os.getpid(),
        lock_key=LOCK_KEY,
        mensagem="Worker não roda o poller.",
    )
    return False
