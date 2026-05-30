"""Testes HTTP-level do GovernanceInfluxClient via respx.

Exercita o cliente real (não AsyncMock) para garantir que payload, headers
e tratamento de status codes estão corretos.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.storage.influxdb.client import GovernanceInfluxClient
from app.storage.influxdb.models import GovernancePoint

_URL = "http://influxdb.test:8086"
_ORG = "test-org"
_TOKEN = "test-token-not-real"
_WRITE_URL = f"{_URL}/api/v2/write"
_PING_URL = f"{_URL}/ping"


def _client(bucket: str = "governance_raw") -> GovernanceInfluxClient:
    return GovernanceInfluxClient(influx_url=_URL, token=_TOKEN, org=_ORG, bucket=bucket)


def _point(count: int = 1) -> GovernancePoint:
    return GovernancePoint(
        measurement="gov_test_metric",
        tags={"env": "test"},
        fields={"count": count},
        timestamp_ns=1748000000000000000,
    )


# ─────────────────────────────────────────────────────────────────────
# Constructor / bucket validation
# ─────────────────────────────────────────────────────────────────────


class TestClientInit:
    def test_rejects_unlisted_bucket(self) -> None:
        with pytest.raises(ValueError, match="allowlist"):
            GovernanceInfluxClient(_URL, _TOKEN, _ORG, bucket="other-bucket")

    def test_accepts_governance_raw(self) -> None:
        c = _client("governance_raw")
        assert c._bucket == "governance_raw"

    def test_accepts_governance_hourly(self) -> None:
        c = _client("governance_hourly")
        assert c._bucket == "governance_hourly"


# ─────────────────────────────────────────────────────────────────────
# write_points
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestWritePoints:
    async def test_success_204_returns_none(self) -> None:
        with respx.mock:
            respx.post(_WRITE_URL).mock(return_value=httpx.Response(204))
            await _client().write_points([_point()])

    async def test_empty_list_makes_no_http_call(self) -> None:
        with respx.mock:
            route = respx.post(_WRITE_URL).mock(return_value=httpx.Response(204))
            await _client().write_points([])
            assert not route.called

    async def test_payload_is_line_protocol(self) -> None:
        captured: list[bytes] = []

        def capture(request: httpx.Request) -> httpx.Response:
            captured.append(request.content)
            return httpx.Response(204)

        with respx.mock:
            respx.post(_WRITE_URL).mock(side_effect=capture)
            await _client().write_points([_point(42)])

        body = captured[0].decode()
        assert "gov_test_metric" in body
        assert "count=42i" in body

    async def test_auth_header_is_token(self) -> None:
        captured_headers: list[httpx.Headers] = []

        def capture(request: httpx.Request) -> httpx.Response:
            captured_headers.append(request.headers)
            return httpx.Response(204)

        with respx.mock:
            respx.post(_WRITE_URL).mock(side_effect=capture)
            await _client().write_points([_point()])

        assert captured_headers[0]["Authorization"] == f"Token {_TOKEN}"

    async def test_multiple_points_in_one_request(self) -> None:
        captured: list[bytes] = []

        def capture(request: httpx.Request) -> httpx.Response:
            captured.append(request.content)
            return httpx.Response(204)

        with respx.mock:
            respx.post(_WRITE_URL).mock(side_effect=capture)
            await _client().write_points([_point(1), _point(2), _point(3)])

        assert len(captured) == 1  # single batch POST
        lines = captured[0].decode().strip().split("\n")
        assert len(lines) == 3

    async def test_non_204_raises_http_error(self) -> None:
        with respx.mock:
            respx.post(_WRITE_URL).mock(return_value=httpx.Response(400, json={"message": "bad field"}))
            with pytest.raises(httpx.HTTPStatusError):
                await _client().write_points([_point()])

    async def test_401_raises_http_error(self) -> None:
        with respx.mock:
            respx.post(_WRITE_URL).mock(return_value=httpx.Response(401, text="Unauthorized"))
            with pytest.raises(httpx.HTTPStatusError) as exc_info:
                await _client().write_points([_point()])
        assert exc_info.value.response.status_code == 401

    async def test_500_raises_http_error(self) -> None:
        with respx.mock:
            respx.post(_WRITE_URL).mock(return_value=httpx.Response(500, text="Internal Error"))
            with pytest.raises(httpx.HTTPStatusError):
                await _client().write_points([_point()])

    async def test_precision_is_ns_in_url(self) -> None:
        captured_urls: list[str] = []

        def capture(request: httpx.Request) -> httpx.Response:
            captured_urls.append(str(request.url))
            return httpx.Response(204)

        with respx.mock:
            respx.post(_WRITE_URL).mock(side_effect=capture)
            await _client().write_points([_point()])

        assert "precision=ns" in captured_urls[0]


# ─────────────────────────────────────────────────────────────────────
# ping
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestPing:
    async def test_ping_200_returns_true(self) -> None:
        with respx.mock:
            respx.get(_PING_URL).mock(return_value=httpx.Response(200))
            assert await _client().ping() is True

    async def test_ping_204_returns_true(self) -> None:
        with respx.mock:
            respx.get(_PING_URL).mock(return_value=httpx.Response(204))
            assert await _client().ping() is True

    async def test_ping_503_returns_false(self) -> None:
        with respx.mock:
            respx.get(_PING_URL).mock(return_value=httpx.Response(503))
            assert await _client().ping() is False

    async def test_ping_connection_error_returns_false(self) -> None:
        with respx.mock:
            respx.get(_PING_URL).mock(side_effect=httpx.ConnectError("refused"))
            assert await _client().ping() is False

    async def test_ping_timeout_returns_false(self) -> None:
        with respx.mock:
            respx.get(_PING_URL).mock(side_effect=httpx.TimeoutException("timeout"))
            assert await _client().ping() is False
