"""SQLite database lifecycle management for the Flask app context."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from flask import Flask, g

if TYPE_CHECKING:
    from app.config import AppSettings

log = structlog.get_logger(__name__)

_SCHEMA_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "schema.sql"


def get_db() -> sqlite3.Connection:
    """Return the per-request database connection stored in Flask's g."""
    if "db" not in g:
        raise RuntimeError("Database not initialised — call init_db() first.")
    return g.db  # type: ignore[no-any-return]


def _open_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _apply_schema(conn: sqlite3.Connection) -> None:
    """Run schema.sql idempotently (CREATE TABLE IF NOT EXISTS)."""
    if not _SCHEMA_PATH.exists():
        log.warning("schema_not_found", path=str(_SCHEMA_PATH))
        return
    sql = _SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(sql)
    conn.commit()
    log.info("schema_applied", path=str(_SCHEMA_PATH))


def init_db(app: Flask, cfg: AppSettings) -> None:
    """Register SQLite lifecycle hooks on the Flask app.

    Args:
        app: Flask application instance.
        cfg: Application settings containing db.url.
    """
    db_url = cfg.db.url
    db_path = db_url[len("sqlite:///") :] if db_url.startswith("sqlite:///") else db_url

    # Ensure parent directory exists
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    # Apply schema once at startup
    init_conn = _open_connection(db_path)
    _apply_schema(init_conn)
    init_conn.close()

    log.info("db_initialised", path=db_path)

    @app.before_request
    def open_db() -> None:
        g.db = _open_connection(db_path)

    @app.teardown_request
    def close_db(exc: BaseException | None = None) -> None:
        db = g.pop("db", None)
        if db is not None:
            if exc is not None:
                db.rollback()
            db.close()
