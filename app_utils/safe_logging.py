"""Sanitização para logs (CWE-117 — Log Injection).

Padrão reconhecido pelo CodeQL como sanitizer de log-injection:
usa literais "\\r", "\\n", "\\t" em sequência de .replace().
"""

from __future__ import annotations

from typing import Any

_MAX = 200


def safe(value: Any, max_len: int = _MAX) -> str:
    """Sanitiza valor para log seguro.

    - Converte para str.
    - Escapa CR/LF/TAB (previne injeção de linhas no log).
    - Trunca para max_len caracteres.
    """
    s = "" if value is None else str(value)
    # ORDEM IMPORTA: \r\n primeiro pra não duplicar escape.
    s = s.replace("\r\n", "\\r\\n")
    s = s.replace("\n", "\\n")
    s = s.replace("\r", "\\r")
    s = s.replace("\t", "\\t")
    return s[:max_len]
