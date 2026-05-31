"""Testes para SyncAPIClient.

Usa respx para mockar o httpx.Client sem fazer requests reais.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.utils.http_client import SyncAPIClient

BASE = "https://api.example.com"


@pytest.fixture()
def client() -> SyncAPIClient:
    return SyncAPIClient(base_url=BASE, timeout=5.0, max_retries=2)


class TestRequestSuccess:
    @respx.mock
    def test_get_returns_response(self, client: SyncAPIClient) -> None:
        respx.get(f"{BASE}/items").mock(return_value=httpx.Response(200, json={"ok": True}))
        resp = client.get("/items")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    @respx.mock
    def test_post_sends_json(self, client: SyncAPIClient) -> None:
        route = respx.post(f"{BASE}/events").mock(return_value=httpx.Response(201, json={"id": 1}))
        resp = client.post("/events", json={"name": "test"})
        assert resp.status_code == 201
        assert route.called

    @respx.mock
    def test_request_method_get(self, client: SyncAPIClient) -> None:
        respx.get(f"{BASE}/ping").mock(return_value=httpx.Response(200))
        resp = client.request("GET", "/ping")
        assert resp.status_code == 200


class TestRetryBehavior:
    @respx.mock
    def test_retries_on_5xx(self, client: SyncAPIClient) -> None:
        """Deve retentar em 5xx até max_retries, depois propagar."""
        route = respx.post(f"{BASE}/api").mock(
            side_effect=[
                httpx.Response(503),
                httpx.Response(200, json={"ok": True}),
            ]
        )
        resp = client.post("/api", json={})
        assert resp.status_code == 200
        assert route.call_count == 2

    @respx.mock
    def test_does_not_retry_on_4xx(self, client: SyncAPIClient) -> None:
        """Erro 4xx = bug de cliente, não deve retentar."""
        route = respx.get(f"{BASE}/protected").mock(return_value=httpx.Response(401))
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            client.get("/protected")
        assert exc_info.value.response.status_code == 401
        assert route.call_count == 1

    @respx.mock
    def test_retries_on_transport_error(self) -> None:
        """Erro de transporte (timeout, conexão recusada) deve retentar."""
        client = SyncAPIClient(base_url=BASE, max_retries=2)
        route = respx.get(f"{BASE}/flaky").mock(
            side_effect=[
                httpx.ConnectError("connection refused"),
                httpx.Response(200, json={"recovered": True}),
            ]
        )
        resp = client.get("/flaky")
        assert resp.status_code == 200
        assert route.call_count == 2

    @respx.mock
    def test_raises_after_all_retries_exhausted(self) -> None:
        """Após max_retries tentativas, propaga a última exceção."""
        client = SyncAPIClient(base_url=BASE, max_retries=2)
        respx.get(f"{BASE}/down").mock(side_effect=httpx.ConnectError("always down"))
        with pytest.raises(httpx.ConnectError):
            client.get("/down")


class TestContextManager:
    @respx.mock
    def test_context_manager_closes_client(self) -> None:
        respx.get(f"{BASE}/ping").mock(return_value=httpx.Response(200))
        with SyncAPIClient(base_url=BASE) as c:
            resp = c.get("/ping")
            assert resp.status_code == 200
        # Após __exit__, o client está fechado — nova request deve falhar
        with pytest.raises(Exception):
            c.get("/ping")

    def test_close_is_idempotent(self, client: SyncAPIClient) -> None:
        client.close()
        client.close()  # segunda chamada não deve lançar


class TestSubclassing:
    @respx.mock
    def test_subclass_can_override_base_url(self) -> None:
        """Subclasses devem poder especializar o cliente."""

        class ZabbixClient(SyncAPIClient):
            def __init__(self, url: str, token: str) -> None:
                super().__init__(
                    base_url=url,
                    headers={"Authorization": f"Bearer {token}"},
                )

            def ping(self) -> bool:
                resp = self.get("/api_jsonrpc.php")
                return resp.status_code == 200

        respx.get("https://zabbix.example.com/api_jsonrpc.php").mock(return_value=httpx.Response(200))
        c = ZabbixClient("https://zabbix.example.com", "secret")
        assert c.ping() is True
