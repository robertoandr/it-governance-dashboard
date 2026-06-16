"""Async health checks for Kubernetes readiness probes.

Runs dependency checks in parallel with individual timeouts.
Designed to be called from sync Flask handlers via ``asyncio.run()``.
"""

from __future__ import annotations

import asyncio
import shutil
import sqlite3
import time
from collections.abc import Coroutine
from dataclasses import dataclass
from typing import Any

import httpx
import structlog

from app.config import get_settings

log = structlog.get_logger(__name__)


@dataclass
class CheckResult:
    """Result of a single health check.

    Attributes:
        ok: True if the dependency is reachable and healthy.
        latency_ms: Round-trip time in milliseconds.
        error: Human-readable error message when ``ok`` is False.
    """

    ok: bool
    latency_ms: float
    error: str | None = None


class HealthChecker:
    """Parallel async health checker for Kubernetes readiness probes.

    Example:
        checker = HealthChecker()
        results = asyncio.run(checker.check_all(timeout=2.0))
    """

    async def check_influxdb(self) -> CheckResult:
        """Ping InfluxDB v2 via GET /ping.

        Skipped (returns ok=True) when ``influx.enabled`` is False —
        treating a disabled integration as a non-dependency.

        Returns:
            CheckResult with connectivity status and latency.
        """
        settings = get_settings()

        if not settings.influx.enabled:
            log.debug("influxdb_check_skipped", reason="integration_disabled")
            return CheckResult(ok=True, latency_ms=0.0)

        url = f"{settings.influx.url.rstrip('/')}/ping"
        token = settings.influx.token.get_secret_value()
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    url,
                    headers={"Authorization": f"Token {token}"},
                    timeout=2.0,
                )
            latency_ms = (time.monotonic() - t0) * 1000
            if resp.status_code in (200, 204):
                log.debug("influxdb_ping_ok", latency_ms=round(latency_ms, 2))
                return CheckResult(ok=True, latency_ms=latency_ms)
            log.warning("influxdb_ping_bad_status", status=resp.status_code)
            return CheckResult(ok=False, latency_ms=latency_ms, error=f"HTTP {resp.status_code}")
        except Exception as exc:
            latency_ms = (time.monotonic() - t0) * 1000
            log.warning("influxdb_ping_failed", error=str(exc))
            return CheckResult(ok=False, latency_ms=latency_ms, error=str(exc))

    async def check_disk_space(self) -> CheckResult:
        """Check that at least 10% of disk space is free on the root filesystem.

        Uses ``run_in_executor`` so the blocking ``statvfs`` syscall does not
        occupy the event loop — important on NFS mounts or degraded disks where
        the kernel call can block indefinitely with no await point to cancel on.

        Returns:
            CheckResult with disk health status.
        """
        loop = asyncio.get_running_loop()
        t0 = time.monotonic()
        usage = await loop.run_in_executor(None, shutil.disk_usage, "/")
        latency_ms = (time.monotonic() - t0) * 1000
        pct_free = usage.free / usage.total
        if pct_free < 0.10:
            free_gb = usage.free / (1024**3)
            msg = f"Only {pct_free:.1%} free ({free_gb:.1f} GB)"
            log.warning("disk_space_critical", pct_free=round(pct_free, 4), free_bytes=usage.free)
            return CheckResult(ok=False, latency_ms=latency_ms, error=msg)
        log.debug("disk_space_ok", pct_free=round(pct_free, 4))
        return CheckResult(ok=True, latency_ms=latency_ms)

    async def check_sqlite(self) -> CheckResult:
        """Verifica conectividade e integridade básica do banco SQLite.

        Abre uma conexão isolada (NÃO usa g.db) e executa SELECT 1.
        Usar uma conexão própria garante que o check funciona fora do
        ciclo de vida de request do Flask (e.g. startup probes, cron).

        Returns:
            CheckResult com status e latência em ms.
        """
        settings = get_settings()
        db_url = settings.db.url
        db_path = db_url[len("sqlite:///") :] if db_url.startswith("sqlite:///") else db_url

        loop = asyncio.get_running_loop()
        t0 = time.monotonic()

        def _probe() -> None:
            conn = sqlite3.connect(db_path, timeout=2.0)
            try:
                conn.execute("SELECT 1")
            finally:
                conn.close()

        try:
            await loop.run_in_executor(None, _probe)
            latency_ms = (time.monotonic() - t0) * 1000
            log.debug("sqlite_check_ok", latency_ms=round(latency_ms, 2), path=db_path)
            return CheckResult(ok=True, latency_ms=latency_ms)
        except Exception as exc:
            latency_ms = (time.monotonic() - t0) * 1000
            log.error(
                "sqlite_check_failed",
                error=str(exc),
                latency_ms=round(latency_ms, 2),
                path=db_path,
            )
            return CheckResult(ok=False, latency_ms=latency_ms, error=str(exc))

    # TODO: add check_zabbix() once ZabbixConfig.enabled is wired to AppSettings
    # TODO: add check_zendesk() for Zendesk API connectivity
    # TODO: add check_github() for GitHub API connectivity

    async def check_all(self, timeout: float = 2.0) -> dict[str, CheckResult]:
        """Run all checks concurrently with per-check timeouts.

        Uses ``asyncio.gather(..., return_exceptions=True)`` so a single
        check failure or timeout does not abort the others.

        Args:
            timeout: Maximum seconds allowed per individual check.

        Returns:
            Mapping of check name to CheckResult. Timed-out checks are
            reported as ``ok=False`` with the timeout error message.
        """
        checks: dict[str, Coroutine[Any, Any, CheckResult]] = {
            "influxdb": self.check_influxdb(),
            "disk_space": self.check_disk_space(),
            "sqlite": self.check_sqlite(),
        }

        tasks = [asyncio.wait_for(coro, timeout=timeout) for coro in checks.values()]
        results_raw: list[CheckResult | BaseException] = await asyncio.gather(*tasks, return_exceptions=True)

        out: dict[str, CheckResult] = {}
        for name, raw in zip(checks.keys(), results_raw, strict=True):
            if isinstance(raw, CheckResult):
                out[name] = raw
            else:
                out[name] = CheckResult(ok=False, latency_ms=timeout * 1000, error=str(raw))

        return out
