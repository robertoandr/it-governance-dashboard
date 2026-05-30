"""Safety guards para o InfluxDB compartilhado.

O InfluxDB nativo (itgov-dev :8086) é compartilhado com Grafana e outras stacks.
Estes guards evitam escrita acidental em buckets de outros projetos.
"""

import logging
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)

ALLOWED_BUCKETS: frozenset[str] = frozenset(
    {
        "governance_raw",
        "governance_hourly",
        "governance_monthly",
    }
)

MEASUREMENT_PREFIX = "gov_"


@dataclass(frozen=True)
class TokenScopeResult:
    """Resultado da validação de escopo do token."""

    ok: bool
    auth_id: str  # ID da autorização (ou placeholder para tokens escopados)
    allowed_buckets: frozenset[str]
    extra_buckets: frozenset[str]
    message: str


class TokenScopeError(RuntimeError):
    """Levantado quando o token tem acesso além dos buckets permitidos."""


async def validate_token_scope(influx_url: str, token: str) -> TokenScopeResult:
    """Valida que o token acessa APENAS os buckets de governança.

    Usa GET /api/v2/buckets — endpoint acessível por tokens escopados e que
    retorna exatamente os buckets visíveis para o token. Tokens admin retornam
    todos os buckets da org; tokens escopados retornam apenas os autorizados.

    Aborta se o token enxergar buckets fora de ALLOWED_BUCKETS, prevenindo
    escrita acidental em dados de outros projetos no InfluxDB compartilhado.

    Args:
        influx_url: URL base do InfluxDB (ex: http://localhost:8086).
        token: Token de autenticação InfluxDB v2.

    Returns:
        TokenScopeResult com detalhes dos buckets visíveis.

    Raises:
        TokenScopeError: Se o token enxergar buckets fora do projeto.
        httpx.HTTPError: Se a API do InfluxDB não responder.
    """
    url = influx_url.rstrip("/")
    headers = {"Authorization": f"Token {token}"}

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{url}/api/v2/buckets", headers=headers)

    if resp.status_code == 401:
        raise TokenScopeError(f"Token inválido — 401 Unauthorized: {url}")

    resp.raise_for_status()
    data = resp.json()

    visible: set[str] = {
        b["name"]
        for b in data.get("buckets", [])
        if not b["name"].startswith("_")  # ignora _monitoring, _tasks (sistema)
    }

    extra = frozenset(visible) - ALLOWED_BUCKETS
    allowed_found = frozenset(visible) & ALLOWED_BUCKETS

    if extra:
        msg = (
            f"Token enxerga buckets fora do projeto: {sorted(extra)}. "
            f"Permitidos: {sorted(ALLOWED_BUCKETS)}. "
            "Use um token com escopo restrito aos buckets de governança."
        )
        log.critical("influxdb.token_scope_violation extra_buckets=%s", sorted(extra))
        raise TokenScopeError(msg)

    log.info("influxdb.token_scope_ok visible_buckets=%s", sorted(allowed_found))
    return TokenScopeResult(
        ok=True,
        auth_id="(scoped)",
        allowed_buckets=allowed_found,
        extra_buckets=frozenset(),
        message=f"Escopo OK — buckets visíveis: {sorted(allowed_found)}",
    )


def validate_bucket(bucket: str) -> None:
    """Garante que o bucket alvo é um dos buckets de governança.

    Args:
        bucket: Nome do bucket de destino.

    Raises:
        ValueError: Se o bucket não estiver na allowlist.
    """
    if bucket not in ALLOWED_BUCKETS:
        raise ValueError(f"Bucket {bucket!r} não está na allowlist do projeto. Permitidos: {sorted(ALLOWED_BUCKETS)}")


def validate_measurement(measurement: str) -> None:
    """Garante que o measurement usa o prefixo obrigatório 'gov_'.

    Measurements sem o prefixo são rejeitados para evitar poluição do namespace
    compartilhado com outros projetos no mesmo InfluxDB.

    Args:
        measurement: Nome do measurement a ser escrito.

    Raises:
        ValueError: Se o measurement não começar com 'gov_'.
    """
    if not measurement.startswith(MEASUREMENT_PREFIX):
        raise ValueError(
            f"Measurement {measurement!r} rejeitado. "
            f"Todos os measurements devem começar com {MEASUREMENT_PREFIX!r}. "
            "Ex: gov_github_prs, gov_zabbix_incidents, gov_zendesk_tickets"
        )
