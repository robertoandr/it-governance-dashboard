"""Testes para ZabbixService com respx (mock httpx)."""

from __future__ import annotations

import httpx
import pytest
import respx

from itgov.models.zabbix import AcknowledgeRequest, ZabbixSeverity
from itgov.services.zabbix_service import ZABBIX_JSONRPC_PATH, ZabbixService, _normalize_zabbix_base_url

ZABBIX_URL = "https://zabbix.example.com"
LOGIN_RESPONSE = {"jsonrpc": "2.0", "result": "fake-token-abc123", "id": 1}
PROBLEMS_RESPONSE = {
    "jsonrpc": "2.0",
    "result": [
        {
            "eventid": "101",
            "objectid": "t1",
            "name": "High CPU on web01",
            "severity": "4",
            "clock": "1748650000",
            "r_clock": "0",
            "acknowledged": "0",
        },
        {
            "eventid": "102",
            "objectid": "t2",
            "name": "Disk full on db01",
            "severity": "5",
            "clock": "1748651000",
            "r_clock": "0",
            "acknowledged": "1",
        },
    ],
    "id": 1,
}
TRIGGERS_RESPONSE = {
    "jsonrpc": "2.0",
    "result": [
        {"triggerid": "t1", "hosts": [{"name": "web01"}]},
        {"triggerid": "t2", "hosts": [{"name": "db01"}]},
    ],
    "id": 1,
}
HOSTS_RESPONSE = {
    "jsonrpc": "2.0",
    "result": [
        {"hostid": "1", "host": "web01", "name": "Web Server", "status": "0", "interfaces": [{"available": "1"}]},
        {"hostid": "2", "host": "db01", "name": "Database", "status": "0", "interfaces": [{"available": "2"}]},
        {"hostid": "3", "host": "backup", "name": "Backup", "status": "0", "interfaces": [{"available": "0"}]},
    ],
    "id": 1,
}


@pytest.fixture()
def svc() -> ZabbixService:
    return ZabbixService(url=ZABBIX_URL, user="Admin", password="secret", max_retries=1)


def _mock_rpc(url: str, responses: list[dict]) -> None:
    """Helper: mocka POST /api_jsonrpc.php com múltiplas respostas sequenciais."""
    respx.post(f"{url}/api_jsonrpc.php").mock(side_effect=[httpx.Response(200, json=r) for r in responses])


class TestAuthentication:
    @respx.mock
    def test_login_on_first_rpc_call(self, svc: ZabbixService) -> None:
        _mock_rpc(ZABBIX_URL, [LOGIN_RESPONSE, {"jsonrpc": "2.0", "result": [], "id": 1}])
        svc.get_problems()
        assert svc._token == "fake-token-abc123"

    @respx.mock
    def test_token_reused_across_calls(self, svc: ZabbixService) -> None:
        _mock_rpc(
            ZABBIX_URL,
            [
                LOGIN_RESPONSE,
                {"jsonrpc": "2.0", "result": [], "id": 1},
                {"jsonrpc": "2.0", "result": [], "id": 1},
            ],
        )
        svc.get_problems()
        svc.get_problems()
        # 3 calls: 1 login + 2 problem.get (sem segundo login)
        assert respx.calls.call_count == 3

    @respx.mock
    def test_session_renewal_on_expired_token(self, svc: ZabbixService) -> None:
        expired_response = {
            "jsonrpc": "2.0",
            "error": {"code": -32602, "message": "Invalid params.", "data": "Re-login please."},
            "id": 1,
        }
        new_token_response = {"jsonrpc": "2.0", "result": "new-token-xyz", "id": 1}
        _mock_rpc(
            ZABBIX_URL,
            [
                LOGIN_RESPONSE,  # login inicial
                expired_response,  # problem.get falha (session expirada)
                new_token_response,  # re-login
                {"jsonrpc": "2.0", "result": [], "id": 1},  # problem.get retry
            ],
        )
        problems = svc.get_problems()
        assert problems == []
        assert svc._token == "new-token-xyz"


class TestGetProblems:
    @respx.mock
    def test_returns_problems_sorted_by_severity(self, svc: ZabbixService) -> None:
        _mock_rpc(ZABBIX_URL, [LOGIN_RESPONSE, PROBLEMS_RESPONSE, TRIGGERS_RESPONSE])
        problems = svc.get_problems()
        assert len(problems) == 2
        # Sorted by severity DESC: disaster (5) first, high (4) second
        assert problems[0].severity == ZabbixSeverity.DISASTER
        assert problems[1].severity == ZabbixSeverity.HIGH

    @respx.mock
    def test_enriches_problems_with_host_names(self, svc: ZabbixService) -> None:
        _mock_rpc(ZABBIX_URL, [LOGIN_RESPONSE, PROBLEMS_RESPONSE, TRIGGERS_RESPONSE])
        problems = svc.get_problems()
        hosts_flat = [h for p in problems for h in p.hosts]
        assert "web01" in hosts_flat
        assert "db01" in hosts_flat

    @respx.mock
    def test_empty_result_returns_empty_list(self, svc: ZabbixService) -> None:
        _mock_rpc(ZABBIX_URL, [LOGIN_RESPONSE, {"jsonrpc": "2.0", "result": [], "id": 1}])
        assert svc.get_problems() == []

    @respx.mock
    def test_acknowledged_flag_parsed_correctly(self, svc: ZabbixService) -> None:
        _mock_rpc(ZABBIX_URL, [LOGIN_RESPONSE, PROBLEMS_RESPONSE, TRIGGERS_RESPONSE])
        problems = svc.get_problems()
        acked = {p.eventid: p.acknowledged for p in problems}
        assert acked["101"] is False
        assert acked["102"] is True


class TestGetHosts:
    @respx.mock
    def test_returns_hosts_with_availability(self, svc: ZabbixService) -> None:
        _mock_rpc(ZABBIX_URL, [LOGIN_RESPONSE, HOSTS_RESPONSE])
        hosts = svc.get_hosts()
        assert len(hosts) == 3
        up_hosts = [h for h in hosts if h.is_up]
        assert len(up_hosts) == 1
        assert up_hosts[0].host == "web01"

    @respx.mock
    def test_host_summary_counts(self, svc: ZabbixService) -> None:
        _mock_rpc(ZABBIX_URL, [LOGIN_RESPONSE, HOSTS_RESPONSE])
        summary = svc.get_host_summary()
        assert summary.total == 3
        assert summary.up == 1
        assert summary.down == 1
        assert summary.unknown == 1
        assert summary.up_pct == pytest.approx(33.3, abs=0.1)


class TestSeverityDistribution:
    @respx.mock
    def test_distribution_includes_all_severities(self, svc: ZabbixService) -> None:
        _mock_rpc(ZABBIX_URL, [LOGIN_RESPONSE, PROBLEMS_RESPONSE, TRIGGERS_RESPONSE])
        dist = svc.get_severity_distribution()
        assert "disaster" in dist
        assert "high" in dist
        assert dist["disaster"] == 1
        assert dist["high"] == 1
        assert dist["warning"] == 0


class TestAcknowledge:
    @respx.mock
    def test_ack_sends_correct_payload(self, svc: ZabbixService) -> None:
        ack_response = {"jsonrpc": "2.0", "result": {"eventids": ["101"]}, "id": 1}
        _mock_rpc(ZABBIX_URL, [LOGIN_RESPONSE, ack_response])
        req = AcknowledgeRequest(eventid="101", message="Fixed")
        result = svc.acknowledge_event(req)
        assert result is True

    @respx.mock
    def test_ack_propagates_rpc_error(self, svc: ZabbixService) -> None:
        error_response = {
            "jsonrpc": "2.0",
            "error": {"code": -32500, "message": "Error.", "data": "No permission."},
            "id": 1,
        }
        _mock_rpc(ZABBIX_URL, [LOGIN_RESPONSE, error_response])
        req = AcknowledgeRequest(eventid="999")
        with pytest.raises(RuntimeError, match="Zabbix API error"):
            svc.acknowledge_event(req)


class TestNormalizeZabbixBaseURL:
    @pytest.mark.parametrize(
        "input_url,expected",
        [
            ("http://localhost/api_jsonrpc.php", "http://localhost"),
            ("http://localhost/api_jsonrpc.php/", "http://localhost"),
            ("http://localhost", "http://localhost"),
            ("http://localhost/", "http://localhost"),
            ("https://zbx.corp.com/api_jsonrpc.php", "https://zbx.corp.com"),
            ("http://srv/zabbix/api_jsonrpc.php", "http://srv/zabbix"),
            ("  https://zbx.corp.com/api_jsonrpc.php  ", "https://zbx.corp.com"),
            ("https://zbx.corp.com", "https://zbx.corp.com"),
        ],
    )
    def test_normalize_variants(self, input_url: str, expected: str) -> None:
        assert _normalize_zabbix_base_url(input_url) == expected

    def test_empty_string_raises(self) -> None:
        with pytest.raises(ValueError, match="vazia"):
            _normalize_zabbix_base_url("")

    def test_whitespace_only_raises(self) -> None:
        with pytest.raises(ValueError, match="vazia"):
            _normalize_zabbix_base_url("   ")

    def test_invalid_scheme_raises(self) -> None:
        with pytest.raises(ValueError, match="Scheme inválido"):
            _normalize_zabbix_base_url("ftp://zbx.corp.com/api_jsonrpc.php")

    def test_empty_netloc_raises(self) -> None:
        with pytest.raises(ValueError, match="host válido"):
            _normalize_zabbix_base_url("http:///api_jsonrpc.php")

    def test_credentials_not_leaked_in_log(self, caplog: pytest.LogCaptureFixture) -> None:
        import logging

        with caplog.at_level(logging.WARNING):
            _normalize_zabbix_base_url("http://user:secret@zbx.corp.com/api_jsonrpc.php")
        for record in caplog.records:
            assert "secret" not in str(record.__dict__), "Credenciais vazadas no log"

    def test_idempotent(self) -> None:
        url = "https://zbx.corp.com"
        assert _normalize_zabbix_base_url(_normalize_zabbix_base_url(url)) == _normalize_zabbix_base_url(url)


class TestZabbixClientURLConstruction:
    """Testes de regressão para issue #72: double-path URL."""

    @respx.mock
    def test_no_double_path_when_config_has_suffix(self) -> None:
        """Regressão #72: base_url com sufixo não deve duplicar o path."""
        url_with_suffix = "https://zabbix.example.com/api_jsonrpc.php"
        svc = ZabbixService(url=url_with_suffix, user="Admin", password="secret", max_retries=1)

        _mock_rpc(ZABBIX_URL, [LOGIN_RESPONSE, {"jsonrpc": "2.0", "result": [], "id": 1}])

        svc.get_problems()

        for call in respx.calls:
            url_str = str(call.request.url)
            assert "api_jsonrpc.php/api_jsonrpc.php" not in url_str, f"Double-path detectado (issue #72): {url_str}"
            assert url_str.endswith("/api_jsonrpc.php"), f"Path inesperado: {url_str}"

    @respx.mock
    def test_works_with_clean_config(self) -> None:
        """Configs sem sufixo continuam funcionando corretamente."""
        svc = ZabbixService(url=ZABBIX_URL, user="Admin", password="secret", max_retries=1)

        _mock_rpc(ZABBIX_URL, [LOGIN_RESPONSE, {"jsonrpc": "2.0", "result": [], "id": 1}])
        svc.get_problems()

        for call in respx.calls:
            url_str = str(call.request.url)
            assert "api_jsonrpc.php/api_jsonrpc.php" not in url_str
            assert ZABBIX_JSONRPC_PATH in url_str
