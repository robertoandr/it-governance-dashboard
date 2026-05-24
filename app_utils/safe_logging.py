"""Sanitização para logs (CWE-117)."""

from __future__ import annotations

from typing import Any

_MAX = 200


def safe(value: Any, max_len: int = _MAX) -> str:
    """Sanitiza valor para log seguro: escapa \r\n\t e trunca."""
    s = str(value)[:max_len]
    s = s.replace(chr(13) + chr(10), "\\r\\n")
    s = s.replace(chr(10), "\\n").replace(chr(13), "\\r")
    s = s.replace(chr(9), "\\t")
    return s
