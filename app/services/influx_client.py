"""Async InfluxDB v2 client wrapper with retry and connection management.

Design notes
------------
* Flask-RESTX Resource methods are sync; each request calls asyncio.run().
  A persistent InfluxDBClientAsync would span event loops — not allowed.
  Instead we open a new client context per operation (aiohttp handles the
  underlying TCP connection pooling at the OS level via keep-alive).

* Retry: exponential back-off (0.5s → 1s → 2s) on transient errors.
  Permanent errors (4xx) are NOT retried.

Documented Flux queries
-----------------------
QUERY_MOVING_AVG_24H  — rolling mean uptime per contract, 1-hour windows.
QUERY_AVG_PER_CONTRATO — mean uptime per contract over a configurable range,
                         sorted ascending (worst first). Used by the dashboard
                         endpoint to join with PostgreSQL committed-SLA data.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from influxdb_client import Point
from influxdb_client.client.influxdb_client_async import InfluxDBClientAsync

from app.config import get_settings

log = structlog.get_logger(__name__)

MEASUREMENT = "sla_uptime"

# ---------------------------------------------------------------------------
# Documented Flux queries
# ---------------------------------------------------------------------------

QUERY_MOVING_AVG_24H = """
// Média móvel de uptime nas últimas 24h, janela de 1h, por contrato.
// Parâmetros interpolados: {bucket}, {contrato_id}
from(bucket: "{bucket}")
  |> range(start: -24h)
  |> filter(fn: (r) => r._measurement == "sla_uptime")
  |> filter(fn: (r) => r._field == "uptime_pct")
  |> filter(fn: (r) => r.contrato_id == "{contrato_id}")
  |> aggregateWindow(every: 1h, fn: mean, createEmpty: false)
  |> yield(name: "moving_avg_24h")
"""

QUERY_AVG_PER_CONTRATO = """
// Média de uptime por contrato em um intervalo configurável.
// Resultado ordenado por uptime_pct crescente (piores primeiro).
// O join com SLA contratado (sla_uptime_pct) é feito em Python
// consultando a tabela `contratos` no PostgreSQL.
// Parâmetros interpolados: {bucket}, {range}
from(bucket: "{bucket}")
  |> range(start: -{range})
  |> filter(fn: (r) => r._measurement == "sla_uptime")
  |> filter(fn: (r) => r._field == "uptime_pct")
  |> group(columns: ["contrato_id", "fornecedor_id", "criticidade"])
  |> mean()
  |> group()
  |> sort(columns: ["_value"], desc: false)
  |> yield(name: "avg_uptime_por_contrato")
"""

# ---------------------------------------------------------------------------
# Retry constants
# ---------------------------------------------------------------------------
_MAX_RETRIES = 3
_BASE_DELAY_S = 0.5
# HTTP 4xx errors are permanent — not retried
_PERMANENT_STATUS_CODES = frozenset({400, 401, 403, 404, 422})


def _is_permanent(exc: Exception) -> bool:
    """Return True if *exc* signals a permanent (non-retriable) error.

    Args:
        exc: Exception raised during an InfluxDB operation.

    Returns:
        bool: True when the error should not be retried.
    """
    msg = str(exc).lower()
    for code in _PERMANENT_STATUS_CODES:
        if str(code) in msg:
            return True
    return False


async def _retry(label: str, coro_fn: Any) -> Any:
    """Execute *coro_fn* with exponential back-off on transient failures.

    Args:
        label: Structlog label for observability.
        coro_fn: Zero-argument async callable that returns a coroutine.

    Returns:
        Any: Return value of *coro_fn* on success.

    Raises:
        Exception: Re-raises the last exception after all retries are exhausted.
    """
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            return await coro_fn()
        except Exception as exc:
            last_exc = exc
            if _is_permanent(exc):
                log.error("influx.permanent_error", label=label, error=str(exc))
                raise
            if attempt < _MAX_RETRIES - 1:
                delay = _BASE_DELAY_S * (2**attempt)
                log.warning(
                    "influx.retry",
                    label=label,
                    attempt=attempt + 1,
                    delay_s=delay,
                    error=str(exc),
                )
                await asyncio.sleep(delay)
    raise last_exc  # type: ignore[misc]


def _make_client() -> InfluxDBClientAsync:
    """Build a fresh async InfluxDB client from current settings.

    A new client is created per async operation to avoid cross-event-loop
    issues with Flask's asyncio.run() pattern.

    Returns:
        InfluxDBClientAsync: Configured client (use as async context manager).
    """
    s = get_settings()
    return InfluxDBClientAsync(
        url=s.influx_url,
        token=s.influx_token.get_secret_value(),
        org=s.influx_org,
        timeout=15_000,  # ms — generous for CI/test environments
    )


# ---------------------------------------------------------------------------
# Data models (local — avoids importing app.models in service layer)
# ---------------------------------------------------------------------------

class SlaPoint:
    """Raw value object for a single SLA measurement.

    Attributes:
        contrato_id: Contract UUID (InfluxDB tag).
        fornecedor_id: Vendor UUID (InfluxDB tag).
        criticidade: Business criticality level (InfluxDB tag).
        uptime_pct: Observed uptime percentage (field, 0–100).
        incidentes: Number of incidents in the window (field, ≥ 0).
        tempo_indisponivel_min: Total downtime minutes in the window (field, ≥ 0).
        timestamp: Measurement timestamp; defaults to UTC now.
    """

    __slots__ = (
        "contrato_id",
        "fornecedor_id",
        "criticidade",
        "uptime_pct",
        "incidentes",
        "tempo_indisponivel_min",
        "timestamp",
    )

    def __init__(
        self,
        contrato_id: uuid.UUID,
        fornecedor_id: uuid.UUID,
        criticidade: str,
        uptime_pct: float,
        incidentes: int,
        tempo_indisponivel_min: int,
        timestamp: datetime | None = None,
    ) -> None:
        self.contrato_id = contrato_id
        self.fornecedor_id = fornecedor_id
        self.criticidade = criticidade
        self.uptime_pct = uptime_pct
        self.incidentes = incidentes
        self.tempo_indisponivel_min = tempo_indisponivel_min
        self.timestamp = timestamp or datetime.now(UTC)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class InfluxService:
    """Async CRUD operations for InfluxDB SLA metrics.

    All methods open a fresh client context and must be awaited.
    Call from Flask endpoints via asyncio.run().
    """

    async def write_point(self, point: SlaPoint) -> None:
        """Write a single SLA measurement point to the metrics bucket.

        Args:
            point: SlaPoint value object with measurement data.

        Raises:
            Exception: On permanent API errors or exhausted retries.
        """
        s = get_settings()
        influx_point = (
            Point(MEASUREMENT)
            .tag("contrato_id", str(point.contrato_id))
            .tag("fornecedor_id", str(point.fornecedor_id))
            .tag("criticidade", point.criticidade)
            .field("uptime_pct", float(point.uptime_pct))
            .field("incidentes", int(point.incidentes))
            .field("tempo_indisponivel_min", int(point.tempo_indisponivel_min))
            .time(point.timestamp)
        )

        async def _write() -> None:
            async with _make_client() as client:
                await client.write_api().write(
                    bucket=s.influx_bucket,
                    org=s.influx_org,
                    record=influx_point,
                )

        await _retry("write_point", _write)
        log.info(
            "influx.point.written",
            contrato_id=str(point.contrato_id),
            uptime_pct=point.uptime_pct,
            ts=point.timestamp.isoformat(),
        )

    async def query_sla_series(
        self,
        contrato_id: uuid.UUID,
        range_str: str = "7d",
        window: str = "1h",
    ) -> list[dict[str, Any]]:
        """Query uptime timeseries for a contract with windowed aggregation.

        Uses QUERY_MOVING_AVG_24H pattern, parameterised for any range/window.

        Args:
            contrato_id: Contract UUID to filter on (InfluxDB tag).
            range_str: InfluxDB duration string for look-back (e.g. "7d", "30d").
            window: Aggregation window size (e.g. "1h", "6h", "1d").

        Returns:
            list[dict]: Each dict has ``timestamp`` (datetime) and ``uptime_pct`` (float).
        """
        s = get_settings()
        flux = f"""
from(bucket: "{s.influx_bucket}")
  |> range(start: -{range_str})
  |> filter(fn: (r) => r._measurement == "{MEASUREMENT}")
  |> filter(fn: (r) => r._field == "uptime_pct")
  |> filter(fn: (r) => r.contrato_id == "{contrato_id!s}")
  |> aggregateWindow(every: {window}, fn: mean, createEmpty: false)
  |> yield(name: "uptime_series")
"""

        async def _query() -> list[dict[str, Any]]:
            async with _make_client() as client:
                tables = await client.query_api().query(flux, org=s.influx_org)
                return [
                    {
                        "timestamp": record.get_time(),
                        "uptime_pct": record.get_value(),
                    }
                    for table in tables
                    for record in table.records
                ]

        results: list[dict[str, Any]] = await _retry("query_sla_series", _query)
        log.debug(
            "influx.sla_series.queried",
            contrato_id=str(contrato_id),
            range=range_str,
            window=window,
            points=len(results),
        )
        return results

    async def query_avg_per_contrato(
        self,
        range_str: str = "30d",
    ) -> list[dict[str, Any]]:
        """Query average uptime per contract sorted ascending (worst first).

        Used by the dashboard endpoint. The result is joined in Python with
        committed SLA values from the ``contratos`` PostgreSQL table.

        See QUERY_AVG_PER_CONTRATO for the full Flux query.

        Args:
            range_str: InfluxDB duration string for look-back (e.g. "30d").

        Returns:
            list[dict]: Each dict has ``contrato_id``, ``fornecedor_id``,
                ``criticidade``, and ``uptime_pct`` (float, ascending order).
        """
        s = get_settings()
        flux = QUERY_AVG_PER_CONTRATO.format(
            bucket=s.influx_bucket,
            range=range_str,
        )

        async def _query() -> list[dict[str, Any]]:
            async with _make_client() as client:
                tables = await client.query_api().query(flux, org=s.influx_org)
                return [
                    {
                        "contrato_id": record.values.get("contrato_id"),
                        "fornecedor_id": record.values.get("fornecedor_id"),
                        "criticidade": record.values.get("criticidade"),
                        "uptime_pct": record.get_value(),
                    }
                    for table in tables
                    for record in table.records
                ]

        results: list[dict[str, Any]] = await _retry("query_avg_per_contrato", _query)
        log.debug(
            "influx.avg_per_contrato.queried",
            range=range_str,
            contratos=len(results),
        )
        return results


influx_service = InfluxService()
