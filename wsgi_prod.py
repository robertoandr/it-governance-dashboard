"""WSGI entry point for production (gunicorn).

Python resolves 'app' as the app/ package before app.py when both exist.
This module loads app.py explicitly by file path to avoid the collision.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "app_legacy",
    Path(__file__).parent / "app.py",
)
_mod = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]

app = _mod.app
