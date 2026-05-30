"""Valida que secrets carregados não são placeholders óbvios.

Roda no startup da aplicação e do smoke test. Fail-fast se detectar lixo.
"""

from __future__ import annotations

import logging

import structlog
from pydantic import SecretStr

# Fallback handler garante visibilidade em ambientes sem structlog.configure()
# (ex: testes unitários, CLI standalone, container sem configuração de log)
if not structlog.is_configured():
    logging.basicConfig(level=logging.WARNING)

log = structlog.get_logger(__name__)


class PlaceholderSecretError(ValueError):
    """Levantado quando um secret parece ser placeholder/exemplo."""


# Padrões reconhecidos de token por provider
TOKEN_PATTERNS: dict[str, dict[str, int | str]] = {
    "github_classic": {"prefix": "ghp_", "min_len": 40},
    "github_fine_grained": {"prefix": "github_pat_", "min_len": 80},
    "zabbix": {"prefix": "", "min_len": 64},  # tokens Zabbix são hex de 64 chars
}

# Substrings que denunciam placeholder
PLACEHOLDER_MARKERS = (
    "SEU_TOKEN",
    "YOUR_TOKEN",
    "XXXX",
    "PLACEHOLDER",
    "CHANGEME",
    "EXAMPLE",
    "...",
)


def validate_github_token(token: SecretStr, field_name: str = "GITHUB_TOKEN") -> None:
    """Valida formato de token GitHub.

    Args:
        token: Token a validar (Pydantic SecretStr).
        field_name: Nome do campo (pra mensagem de erro).

    Raises:
        PlaceholderSecretError: Se token parece placeholder ou tem formato inválido.
    """
    value = token.get_secret_value()

    # Guard 1: marcadores óbvios de placeholder
    for marker in PLACEHOLDER_MARKERS:
        if marker in value.upper():
            raise PlaceholderSecretError(
                f"{field_name} contém marcador de placeholder ({marker!r}). Configure um token real no .env."
            )

    # Guard 2: comprimento mínimo
    if len(value) < 40:
        raise PlaceholderSecretError(
            f"{field_name} tem apenas {len(value)} chars. Tokens GitHub válidos têm ≥ 40 chars."
        )

    # Guard 3: prefixo conhecido (warning, não erro — GitHub muda formato às vezes)
    valid_prefixes = ("ghp_", "github_pat_", "gho_", "ghu_", "ghs_")
    if not value.startswith(valid_prefixes):
        log.warning(
            "secret.unknown_prefix",
            field=field_name,
            prefix=value[:8] + "...",
            expected=valid_prefixes,
        )

    log.info("secret.validated", field=field_name, length=len(value))
