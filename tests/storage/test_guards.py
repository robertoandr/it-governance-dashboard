"""Testes dos guards InfluxDB — validate_bucket, validate_measurement, validate_token_scope."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.storage.influxdb.guards import (
    ALLOWED_BUCKETS,
    TokenScopeError,
    validate_bucket,
    validate_measurement,
    validate_token_scope,
)

_URL = "http://influxdb.test:8086"
_TOKEN = "test-token"
_BUCKETS_URL = f"{_URL}/api/v2/buckets"


# ─────────────────────────────────────────────────────────────────────
# validate_bucket
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("bucket", sorted(ALLOWED_BUCKETS))
def test_validate_bucket_accepts_allowed(bucket: str) -> None:
    validate_bucket(bucket)  # deve não levantar


def test_validate_bucket_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="allowlist"):
        validate_bucket("other-bucket")


def test_validate_bucket_rejects_empty() -> None:
    with pytest.raises(ValueError):
        validate_bucket("")


def test_validate_bucket_rejects_system_bucket() -> None:
    with pytest.raises(ValueError):
        validate_bucket("_monitoring")


# ─────────────────────────────────────────────────────────────────────
# validate_measurement
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "measurement",
    ["gov_zabbix_problem", "gov_github_pr", "gov_zendesk_ticket", "gov_"],
)
def test_validate_measurement_accepts_prefix(measurement: str) -> None:
    validate_measurement(measurement)


@pytest.mark.parametrize(
    "measurement",
    ["zabbix_problem", "github_pr", "", "GOV_uppercase", "governance_raw"],
)
def test_validate_measurement_rejects_no_prefix(measurement: str) -> None:
    with pytest.raises(ValueError, match="gov_"):
        validate_measurement(measurement)


# ─────────────────────────────────────────────────────────────────────
# validate_token_scope
# ─────────────────────────────────────────────────────────────────────


def _buckets_response(*names: str) -> dict:
    return {"buckets": [{"name": n} for n in names]}


@pytest.mark.asyncio
async def test_scope_ok_when_only_governance_buckets() -> None:
    with respx.mock:
        respx.get(_BUCKETS_URL).mock(return_value=httpx.Response(200, json=_buckets_response(*ALLOWED_BUCKETS)))
        result = await validate_token_scope(_URL, _TOKEN)

    assert result.ok is True
    assert result.allowed_buckets == ALLOWED_BUCKETS
    assert result.extra_buckets == frozenset()


@pytest.mark.asyncio
async def test_scope_ok_ignores_system_buckets() -> None:
    with respx.mock:
        respx.get(_BUCKETS_URL).mock(
            return_value=httpx.Response(
                200,
                json=_buckets_response("governance_raw", "_monitoring", "_tasks"),
            )
        )
        result = await validate_token_scope(_URL, _TOKEN)

    assert result.ok is True
    assert "governance_raw" in result.allowed_buckets


@pytest.mark.asyncio
async def test_scope_error_when_extra_buckets_visible() -> None:
    with respx.mock:
        respx.get(_BUCKETS_URL).mock(
            return_value=httpx.Response(
                200,
                json=_buckets_response("governance_raw", "other-team-data"),
            )
        )
        with pytest.raises(TokenScopeError, match="other-team-data"):
            await validate_token_scope(_URL, _TOKEN)


@pytest.mark.asyncio
async def test_scope_error_on_401() -> None:
    with respx.mock:
        respx.get(_BUCKETS_URL).mock(return_value=httpx.Response(401))
        with pytest.raises(TokenScopeError, match="401"):
            await validate_token_scope(_URL, _TOKEN)


@pytest.mark.asyncio
async def test_scope_propagates_http_error_on_5xx() -> None:
    with respx.mock:
        respx.get(_BUCKETS_URL).mock(return_value=httpx.Response(503))
        with pytest.raises(httpx.HTTPStatusError):
            await validate_token_scope(_URL, _TOKEN)


@pytest.mark.asyncio
async def test_scope_strips_trailing_slash_from_url() -> None:
    captured: list[str] = []

    def capture(req: httpx.Request) -> httpx.Response:
        captured.append(str(req.url))
        return httpx.Response(200, json=_buckets_response(*ALLOWED_BUCKETS))

    with respx.mock:
        respx.get(_BUCKETS_URL).mock(side_effect=capture)
        await validate_token_scope(_URL + "/", _TOKEN)

    assert not captured[0].startswith(_URL + "//")
