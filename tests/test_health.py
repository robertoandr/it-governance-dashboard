"""Unit and integration tests for health check endpoints and HealthChecker service.

Coverage targets:
- app/services/health_checker.py  → ≥ 90%
- app/api/health.py               → ≥ 90%

Run with:
    pytest -v --cov=app/services/health_checker --cov=app/api/health tests/test_health.py
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.health_checker import CheckResult, HealthChecker

# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def health_client(factory_app):
    """Test client scoped to module — health checks are stateless."""
    with factory_app.test_client() as c:
        yield c


# ─────────────────────────────────────────────────────────────────────────────
# HealthChecker — unit tests
# ─────────────────────────────────────────────────────────────────────────────


class TestCheckInfluxdb:
    async def test_skipped_when_disabled(self) -> None:
        """When influx.enabled=False, check returns ok=True without network calls."""
        checker = HealthChecker()
        result = await checker.check_influxdb()
        assert result.ok is True
        assert result.error is None

    async def test_success_on_204(self, monkeypatch) -> None:
        """Returns ok=True when InfluxDB /ping responds 204."""
        mock_response = MagicMock()
        mock_response.status_code = 204

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_response)

        mock_settings = MagicMock()
        mock_settings.influx.enabled = True
        mock_settings.influx.url = "http://influx:8086"
        mock_settings.influx.token.get_secret_value.return_value = "test-token"

        with (
            patch("app.services.health_checker.get_settings", return_value=mock_settings),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            result = await HealthChecker().check_influxdb()

        assert result.ok is True
        assert result.error is None
        assert result.latency_ms >= 0

    async def test_success_on_200(self, monkeypatch) -> None:
        """Returns ok=True when InfluxDB /ping responds 200."""
        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_response)

        mock_settings = MagicMock()
        mock_settings.influx.enabled = True
        mock_settings.influx.url = "http://influx:8086"
        mock_settings.influx.token.get_secret_value.return_value = "test-token"

        with (
            patch("app.services.health_checker.get_settings", return_value=mock_settings),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            result = await HealthChecker().check_influxdb()

        assert result.ok is True

    async def test_failure_on_5xx(self) -> None:
        """Returns ok=False when InfluxDB /ping responds 503."""
        mock_response = MagicMock()
        mock_response.status_code = 503

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_response)

        mock_settings = MagicMock()
        mock_settings.influx.enabled = True
        mock_settings.influx.url = "http://influx:8086"
        mock_settings.influx.token.get_secret_value.return_value = "test-token"

        with (
            patch("app.services.health_checker.get_settings", return_value=mock_settings),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            result = await HealthChecker().check_influxdb()

        assert result.ok is False
        assert "503" in (result.error or "")

    async def test_failure_on_connection_error(self) -> None:
        """Returns ok=False when InfluxDB is unreachable."""
        import httpx

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))

        mock_settings = MagicMock()
        mock_settings.influx.enabled = True
        mock_settings.influx.url = "http://influx:8086"
        mock_settings.influx.token.get_secret_value.return_value = "test-token"

        with (
            patch("app.services.health_checker.get_settings", return_value=mock_settings),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            result = await HealthChecker().check_influxdb()

        assert result.ok is False
        assert result.error is not None


class TestCheckDiskSpace:
    async def test_ok_when_space_available(self, monkeypatch) -> None:
        """Returns ok=True when > 10% disk space is free."""
        mock_usage = MagicMock()
        mock_usage.total = 100 * 1024**3  # 100 GB
        mock_usage.free = 20 * 1024**3  # 20 GB free = 20%

        monkeypatch.setattr("shutil.disk_usage", lambda _: mock_usage)
        result = await HealthChecker().check_disk_space()
        assert result.ok is True
        assert result.error is None

    async def test_fail_when_space_critical(self, monkeypatch) -> None:
        """Returns ok=False when < 10% disk space is free."""
        mock_usage = MagicMock()
        mock_usage.total = 100 * 1024**3  # 100 GB
        mock_usage.free = 5 * 1024**3  # 5 GB free = 5%

        monkeypatch.setattr("shutil.disk_usage", lambda _: mock_usage)
        result = await HealthChecker().check_disk_space()
        assert result.ok is False
        assert result.error is not None
        assert "5.0%" in result.error

    async def test_exactly_10pct_is_ok(self, monkeypatch) -> None:
        """Exactly 10% free must be considered healthy (boundary condition)."""
        mock_usage = MagicMock()
        mock_usage.total = 100 * 1024**3
        mock_usage.free = 10 * 1024**3  # exactly 10%

        monkeypatch.setattr("shutil.disk_usage", lambda _: mock_usage)
        result = await HealthChecker().check_disk_space()
        assert result.ok is True


class TestCheckAll:
    async def test_all_ok_aggregates_correctly(self) -> None:
        """check_all returns a mapping with all check names present."""
        checker = HealthChecker()
        with (
            patch.object(checker, "check_influxdb", new=AsyncMock(return_value=CheckResult(ok=True, latency_ms=1.0))),
            patch.object(checker, "check_disk_space", new=AsyncMock(return_value=CheckResult(ok=True, latency_ms=0.5))),
        ):
            results = await checker.check_all(timeout=2.0)

        assert "influxdb" in results
        assert "disk_space" in results
        assert all(r.ok for r in results.values())

    async def test_timeout_marks_check_failed(self) -> None:
        """A check that exceeds timeout is reported as ok=False."""
        checker = HealthChecker()

        async def _slow():
            await asyncio.sleep(10)
            return CheckResult(ok=True, latency_ms=0)

        with (
            patch.object(checker, "check_influxdb", new=_slow),
            patch.object(checker, "check_disk_space", new=AsyncMock(return_value=CheckResult(ok=True, latency_ms=0.1))),
        ):
            results = await checker.check_all(timeout=0.05)

        assert results["influxdb"].ok is False
        assert results["influxdb"].error is not None
        assert results["disk_space"].ok is True

    async def test_one_failure_does_not_abort_others(self) -> None:
        """A failing check must not prevent other checks from running."""
        checker = HealthChecker()

        with (
            patch.object(
                checker,
                "check_influxdb",
                new=AsyncMock(return_value=CheckResult(ok=False, latency_ms=50.0, error="refused")),
            ),
            patch.object(
                checker,
                "check_disk_space",
                new=AsyncMock(return_value=CheckResult(ok=True, latency_ms=0.2)),
            ),
        ):
            results = await checker.check_all(timeout=2.0)

        assert results["influxdb"].ok is False
        assert results["disk_space"].ok is True


# ─────────────────────────────────────────────────────────────────────────────
# Liveness endpoint — /api/health
# ─────────────────────────────────────────────────────────────────────────────


class TestLivenessProbe:
    def test_always_200(self, health_client) -> None:
        """GET /api/health must return 200 regardless of dependency state."""
        resp = health_client.get("/api/health")
        assert resp.status_code == 200

    def test_response_status_alive(self, health_client) -> None:
        resp = health_client.get("/api/health")
        import json

        data = json.loads(resp.data)
        assert data["status"] == "alive"

    def test_response_has_timestamp(self, health_client) -> None:
        resp = health_client.get("/api/health")
        import json

        data = json.loads(resp.data)
        assert "timestamp" in data
        assert "T" in data["timestamp"]  # ISO 8601 format

    def test_200_even_when_influxdb_would_fail(self, health_client) -> None:
        """Liveness must remain 200 even if InfluxDB is completely broken."""
        with patch("app.services.health_checker.HealthChecker.check_influxdb") as mock_check:
            mock_check.side_effect = Exception("InfluxDB down")
            resp = health_client.get("/api/health")
        # liveness does not invoke health_checker — must still be 200
        assert resp.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# Readiness endpoint — /api/health/ready
# ─────────────────────────────────────────────────────────────────────────────


class TestReadinessProbe:
    def test_200_when_all_deps_ok(self, health_client) -> None:
        """Returns 200 when all health checks pass."""
        ok_result = CheckResult(ok=True, latency_ms=5.0)
        with patch(
            "app.api.health.HealthChecker.check_all",
            new=AsyncMock(return_value={"influxdb": ok_result, "disk_space": ok_result}),
        ):
            resp = health_client.get("/api/health/ready")
        assert resp.status_code == 200

    def test_503_when_influxdb_down(self, health_client) -> None:
        """Returns 503 when InfluxDB is unreachable."""
        import json

        fail_result = CheckResult(ok=False, latency_ms=2000.0, error="Connection refused")
        ok_result = CheckResult(ok=True, latency_ms=1.0)
        with patch(
            "app.api.health.HealthChecker.check_all",
            new=AsyncMock(return_value={"influxdb": fail_result, "disk_space": ok_result}),
        ):
            resp = health_client.get("/api/health/ready")

        assert resp.status_code == 503
        data = json.loads(resp.data)
        assert data["status"] == "not_ready"
        assert data["checks"]["influxdb"]["ok"] is False
        assert data["checks"]["disk_space"]["ok"] is True

    def test_503_when_disk_critical(self, health_client) -> None:
        """Returns 503 when disk space is below threshold."""
        ok_result = CheckResult(ok=True, latency_ms=1.0)
        fail_result = CheckResult(ok=False, latency_ms=0.1, error="Only 4.0% free (0.4 GB)")
        with patch(
            "app.api.health.HealthChecker.check_all",
            new=AsyncMock(return_value={"influxdb": ok_result, "disk_space": fail_result}),
        ):
            resp = health_client.get("/api/health/ready")
        assert resp.status_code == 503

    def test_response_structure_on_failure(self, health_client) -> None:
        """Readiness response must include status, checks, and timestamp on failure."""
        import json

        fail = CheckResult(ok=False, latency_ms=99.0, error="timeout")
        with patch(
            "app.api.health.HealthChecker.check_all",
            new=AsyncMock(return_value={"influxdb": fail, "disk_space": fail}),
        ):
            resp = health_client.get("/api/health/ready")

        data = json.loads(resp.data)
        assert data["status"] == "not_ready"
        assert "checks" in data
        assert "timestamp" in data
        for check in data["checks"].values():
            assert "ok" in check
            assert "latency_ms" in check

    def test_503_when_both_deps_fail(self, health_client) -> None:
        """Returns 503 when all checks fail."""
        fail = CheckResult(ok=False, latency_ms=2000.0, error="unavailable")
        with patch(
            "app.api.health.HealthChecker.check_all",
            new=AsyncMock(return_value={"influxdb": fail, "disk_space": fail}),
        ):
            resp = health_client.get("/api/health/ready")
        assert resp.status_code == 503

    def test_200_default_with_no_deps_enabled(self, health_client) -> None:
        """In test env with no external deps enabled, readiness must be 200."""
        resp = health_client.get("/api/health/ready")
        # influx.enabled=False in test settings → skipped (ok=True)
        # disk_space checks real disk (expected to have > 10% free in CI)
        assert resp.status_code == 200
