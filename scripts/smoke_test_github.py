"""Smoke test E2E do coletor GitHub → InfluxDB.

Executa validate_github_token() antes de qualquer request — fail-fast se
o token for placeholder ou tiver formato inválido.
"""

from __future__ import annotations

import asyncio
import os
import time

import httpx
from dotenv import load_dotenv

load_dotenv(override=True)  # sobrescreve env vars do processo com valores do .env


def _influx_count(measurement: str, token: str, org: str, hours: int = 24) -> str:
    q = (
        f'from(bucket:"governance_raw") '
        f"|> range(start: -{hours}h) "
        f'|> filter(fn: (r) => r._measurement == "{measurement}") '
        f"|> group() |> count()"
    )
    headers = {
        "Authorization": f"Token {token}",
        "Accept": "application/csv",
        "Content-Type": "application/vnd.flux",
    }
    r = httpx.post(
        f"http://localhost:8086/api/v2/query?org={org}",
        headers=headers,
        content=q.encode(),
        timeout=10,
    )
    line = next((row for row in r.text.splitlines() if ",_result," in row), "")
    return line.split(",")[-1].strip() if line else "0"


def _influx_cardinality(tag: str, token: str, org: str) -> str:
    q = f'import "influxdata/influxdb/schema" schema.tagValues(bucket:"governance_raw", tag:"{tag}") |> count()'
    headers = {
        "Authorization": f"Token {token}",
        "Accept": "application/csv",
        "Content-Type": "application/vnd.flux",
    }
    r = httpx.post(
        f"http://localhost:8086/api/v2/query?org={org}",
        headers=headers,
        content=q.encode(),
        timeout=10,
    )
    line = next((row for row in r.text.splitlines() if ",_result," in row), "")
    return line.split(",")[-1].strip() if line else "0"


async def main() -> None:
    from pydantic import SecretStr

    from app.collectors.github.client import GitHubCollectorClient
    from app.collectors.github.collector import GitHubCollector
    from app.collectors.github.config import GitHubCollectorSettings
    from app.storage.influxdb.client import GovernanceInfluxClient
    from app.utils.secrets_validator import validate_github_token

    # Fail-fast: rejeita placeholder antes de qualquer request
    raw_token = os.environ.get("GITHUB_TOKEN", "")
    validate_github_token(SecretStr(raw_token))
    print(f"[OK] Token validado ({len(raw_token)} chars)")

    settings = GitHubCollectorSettings()
    influx_token = os.environ["INFLUX_TOKEN"]
    influx_org = os.environ["INFLUX_ORG"]
    influx = GovernanceInfluxClient(
        influx_url=os.environ["INFLUX_URL"],
        token=influx_token,
        org=influx_org,
    )

    t0 = time.perf_counter()
    async with GitHubCollectorClient(settings) as client:
        collector = GitHubCollector(client=client, influx=influx, settings=settings)
        count = await collector.collect()
    duration_ms = int((time.perf_counter() - t0) * 1000)

    print(f"[OK] {count} pontos gravados em {duration_ms}ms")
    print()

    # Contagem nos measurements
    print("=== Pontos nas últimas 24h ===")
    for m in ("gov_github_pr", "gov_github_workflow"):
        n = _influx_count(m, influx_token, influx_org)
        print(f"  {m}: {n}")

    print()
    print("=== Cardinality por tag ===")
    for tag in ("author", "branch", "workflow_name", "repo", "host"):
        n = _influx_cardinality(tag, influx_token, influx_org)
        print(f"  {tag}: {n} valores únicos")


if __name__ == "__main__":
    asyncio.run(main())
