"""WSGI entry-point for gunicorn and direct execution."""

from __future__ import annotations

from app import create_app
from app.config import get_settings

settings = get_settings()
application = create_app(settings)

if __name__ == "__main__":
    application.run(
        host=settings.app.host,
        port=settings.app.port,
        debug=settings.app.debug,
    )
