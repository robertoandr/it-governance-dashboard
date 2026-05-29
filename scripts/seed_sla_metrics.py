#!/usr/bin/env python3
"""Seed script — populate 30 days of synthetic SLA data into InfluxDB.

Usage
-----
    # From the project root (activate .venv first):
    uv run python scripts/seed_sla_metrics.py

    # Override env vars inline:
    INFLUX_URL=http://localhost:8086 \\
    INFLUX_TOKEN=<token> \\
    INFLUX_ORG=grupogadens \\
    INFLUX_BUCKET=metrics \\
    DATABASE_URL=postgresql+asyncpg://itgov:pass@172.18.0.2:5432/itgov \\
        uv run python scripts/seed_sla_metrics.py

What it does
------------
1. Reads all active contracts from PostgreSQL (with their committed SLA %).
2. For each contract, writes hourly data points for the last 30 days.
3. Generates realistic noise (±0.5 % Gaussian jitter on uptime).
4. Randomly marks ~20 % of high/critical contracts as "at risk"
   (actual uptime consistently 3–8 % below committed).
5. Simulates occasional incidents (≈3 % of hours) with downtime and > 0
   incident count.
6. Writes in batches of 500 points to avoid memory pressure.

Output
------
Prints a summary table of contracts seeded, total points written,
and which contracts were seeded as "at risk".
"""

from __future__ import annotations

import asyncio
import os
import random
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

# Allow running from any directory
sys.path.insert(0, str(Path(__file__).parent.parent))

# Minimal env bootstrap so Settings() can instantiate without a .env file
os.environ.setdefault("SECRET_KEY", "seed-script-placeholder-key-32-chars-ok")
os.environ.setdefault("INFLUX_TOKEN", os.environ.get("INFLUX_TOKEN", ""))
os.environ.setdefault("INFLUX_ORG", os.environ.get("INFLUX_ORG", "grupogadens"))
os.environ.setdefault("INFLUX_BUCKET", os.environ.get("INFLUX_BUCKET", "metrics"))
os.environ.setdefault("INFLUX_URL", os.environ.get("INFLUX_URL", "http://localhost:8086"))
os.environ.setdefault(
    "DATABASE_URL",
    os.environ.get(
        "DATABASE_URL",
        "postgresql+asyncpg://itgov:Z66BroSKNH6LZcEFOghNRspeQH6oI2Bh@172.18.0.2:5432/itgov",
    ),
)

from influxdb_client import Point  # noqa: E402
from influxdb_client.client.influxdb_client_async import InfluxDBClientAsync  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db.models import Contrato, Fornecedor  # noqa: E402

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DAYS = 30
BATCH_SIZE = 100   # keep batches small to avoid InfluxDB write timeouts
WRITE_DELAY_S = 0.2  # brief pause between batches to avoid storage pressure
MEASUREMENT = "sla_uptime"
INCIDENT_PROBABILITY = 0.03       # 3 % chance of an incident hour
AT_RISK_PROBABILITY = 0.20        # 20 % of alta/critica contracts seeded at-risk
RANDOM_SEED = 42                   # reproducible runs


# ---------------------------------------------------------------------------
# Data generation helpers
# ---------------------------------------------------------------------------

def _base_uptime(committed: float, is_at_risk: bool) -> float:
    """Return the baseline uptime for the seed run.

    Args:
        committed: SLA committed percentage (from contratos.sla_uptime_pct).
        is_at_risk: When True, generates uptime below the committed level.

    Returns:
        float: Baseline uptime percentage (may be < committed for at-risk).
    """
    if is_at_risk:
        deficit = random.uniform(3.0, 8.0)
        return max(0.0, committed - deficit)
    # Slightly above committed (well-performing contract)
    return min(100.0, committed + random.uniform(0.1, 1.5))


def _generate_hour_point(
    contrato: Contrato,
    fornecedor: Fornecedor,
    base: float,
    ts: datetime,
) -> Point:
    """Generate one InfluxDB data point for a single hour.

    Args:
        contrato: ORM Contrato instance (for tags and committed SLA).
        fornecedor: ORM Fornecedor instance (for fornecedor_id tag).
        base: Baseline uptime percentage for this contract.
        ts: Timestamp for the measurement point.

    Returns:
        Point: Fully constructed InfluxDB Point.
    """
    is_incident = random.random() < INCIDENT_PROBABILITY

    if is_incident:
        # Significant uptime dip during the incident hour
        uptime = max(0.0, base - random.uniform(10.0, 40.0))
        incidents = random.randint(1, 3)
        downtime_min = random.randint(20, 55)
    else:
        # Gaussian jitter around the baseline
        uptime = min(100.0, max(0.0, random.gauss(base, 0.5)))
        incidents = 0
        downtime_min = 0

    return (
        Point(MEASUREMENT)
        .tag("contrato_id", str(contrato.id))
        .tag("fornecedor_id", str(fornecedor.id))
        .tag("criticidade", contrato.criticidade)
        .field("uptime_pct", round(uptime, 4))
        .field("incidentes", incidents)
        .field("tempo_indisponivel_min", downtime_min)
        .time(ts)
    )


# ---------------------------------------------------------------------------
# Core async routines
# ---------------------------------------------------------------------------

async def _load_contratos(db_url: str) -> list[tuple[Contrato, Fornecedor]]:
    """Load all active contracts with their fornecedor from PostgreSQL.

    Args:
        db_url: Async PostgreSQL connection URL.

    Returns:
        list[tuple[Contrato, Fornecedor]]: Active contracts with their vendor.
    """
    engine = create_async_engine(db_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        stmt = (
            select(Contrato, Fornecedor)
            .join(Fornecedor, Contrato.fornecedor_id == Fornecedor.id)
            .where(Contrato.deleted_at.is_(None))
        )
        rows = (await session.execute(stmt)).all()

    await engine.dispose()
    return [(row.Contrato, row.Fornecedor) for row in rows]


async def _write_batches(
    client: InfluxDBClientAsync,
    bucket: str,
    org: str,
    points: list[Point],
) -> None:
    """Write a list of points to InfluxDB in BATCH_SIZE chunks.

    Uses orgID (hex) instead of org name to avoid 401/500 errors with
    bucket-scoped tokens on some InfluxDB configurations.

    Args:
        client: Active async InfluxDB client.
        bucket: Target bucket name.
        org: Target org name or ID.
        points: List of Point objects to write.
    """
    write_api = client.write_api()
    n_batches = (len(points) + BATCH_SIZE - 1) // BATCH_SIZE
    for i in range(0, len(points), BATCH_SIZE):
        batch = points[i : i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        for attempt in range(3):
            try:
                await write_api.write(bucket=bucket, org=org, record=batch)
                break
            except Exception as exc:
                if attempt == 2:
                    raise
                await asyncio.sleep(1.0 * (attempt + 1))
                print(f"    … retry {attempt+1} for batch {batch_num}", flush=True)
        print(f"    … batch {batch_num}/{n_batches} ({len(batch)} pts)", flush=True)
        if i + BATCH_SIZE < len(points):
            await asyncio.sleep(WRITE_DELAY_S)


async def seed(settings_override: dict | None = None) -> None:
    """Main seed coroutine.

    Args:
        settings_override: Optional env-var overrides (for testing).
    """
    random.seed(RANDOM_SEED)

    s = get_settings()
    db_url = str(s.database_url)
    influx_url = s.influx_url
    influx_token = s.influx_token.get_secret_value()
    influx_org = s.influx_org
    influx_bucket = s.influx_bucket

    print(f"[seed] Connecting to InfluxDB at {influx_url} (bucket={influx_bucket})")
    print(f"[seed] Loading contracts from {db_url.split('@')[-1]}")

    pairs = await _load_contratos(db_url)
    if not pairs:
        print("[seed] ⚠ No active contracts found — run Alembic migration and create test data first.")
        return

    print(f"[seed] Found {len(pairs)} active contracts. Generating {DAYS * 24} points/contract …\n")

    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    at_risk_ids: list[str] = []
    total_points = 0

    async with InfluxDBClientAsync(
        url=influx_url, token=influx_token, org=influx_org, timeout=30_000
    ) as client:
        for contrato, fornecedor in pairs:
            committed = float(contrato.sla_uptime_pct)

            # Determine if this contract will be seeded at-risk
            is_at_risk = (
                contrato.criticidade in ("alta", "critica")
                and random.random() < AT_RISK_PROBABILITY
            )
            base = _base_uptime(committed, is_at_risk)

            if is_at_risk:
                at_risk_ids.append(contrato.numero_contrato)

            points: list[Point] = []
            for day_offset in range(DAYS, 0, -1):
                for hour in range(24):
                    ts = now - timedelta(days=day_offset, hours=hour)
                    points.append(_generate_hour_point(contrato, fornecedor, base, ts))

            print(
                f"  [{contrato.numero_contrato}] "
                f"committed={committed}% base={base:.2f}% "
                f"{'⚠ AT RISK' if is_at_risk else '✓ OK'} "
                f"({len(points)} pts)"
            )
            await _write_batches(client, influx_bucket, influx_org, points)
            total_points += len(points)

    print(f"\n[seed] Done. {total_points:,} points written for {len(pairs)} contracts.")
    if at_risk_ids:
        print(f"[seed] Contracts seeded as at-risk: {', '.join(at_risk_ids)}")
    else:
        print("[seed] No at-risk contracts this run (run again for a different seed).")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    asyncio.run(seed())
