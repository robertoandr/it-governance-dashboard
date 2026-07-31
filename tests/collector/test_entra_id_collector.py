"""Tests for collector/jobs/entra_id_collector.py — CA policies fail-loud behavior.

Regression coverage for the incident where a 403 from Graph on
/identity/conditionalAccess/policies made the collector silently write
ca_total=0 to InfluxDB, and the dashboard rendered "0 CA policies"
indistinguishable from a real zero.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import requests

import config as _cfg

_mock_settings = MagicMock()
_mock_settings.AZURE_TENANT_ID = "tenant"
_mock_settings.AZURE_CLIENT_ID = "client"
_mock_settings.AZURE_CLIENT_SECRET = "secret"
_mock_settings.INFLUX_URL = "http://localhost:8086"
_mock_settings.INFLUX_TOKEN = "influx-tok"
_mock_settings.INFLUX_ORG = "testorg"
_mock_settings.INFLUX_BUCKET_RAW = "governance_raw"
_cfg.settings = _mock_settings

from collector.jobs.entra_id_collector import EntraIdCollector  # noqa: E402


def _make_collector() -> EntraIdCollector:
    collector = EntraIdCollector.__new__(EntraIdCollector)
    return collector


def _http_error(status_code: int, body: dict) -> requests.exceptions.HTTPError:
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = body
    return requests.exceptions.HTTPError(response=response)


class TestCaPoliciesBreakdown:
    def test_sucesso_retorna_contagens_reais_e_error_none(self) -> None:
        collector = _make_collector()
        collector._paginate = MagicMock(
            return_value=iter(
                [
                    {"id": "1", "state": "enabled"},
                    {"id": "2", "state": "enabledForReportingButNotEnforced"},
                ]
            )
        )

        result = collector._ca_policies_breakdown()

        assert result == {
            "total": 2,
            "enabled": 1,
            "report_only": 1,
            "disabled": 0,
            "error": None,
        }

    def test_403_retorna_none_em_vez_de_zero(self) -> None:
        """Regressão: 403 Forbidden não pode virar ca_total=0 silencioso."""
        collector = _make_collector()

        def _raise(*_args, **_kwargs):
            raise _http_error(
                403,
                {
                    "error": {
                        "code": "AccessDenied",
                        "message": "You cannot perform the requested operation, "
                        "required scopes are missing in the token.",
                    }
                },
            )
            yield  # pragma: no cover - makes this a generator function

        collector._paginate = _raise

        result = collector._ca_policies_breakdown()

        assert result["total"] is None
        assert result["enabled"] is None
        assert result["report_only"] is None
        assert result["disabled"] is None
        assert result["error"] == "HTTP 403 AccessDenied"

    def test_erro_inesperado_tambem_retorna_none_com_error(self) -> None:
        collector = _make_collector()

        def _raise(*_args, **_kwargs):
            raise RuntimeError("boom")
            yield  # pragma: no cover

        collector._paginate = _raise

        result = collector._ca_policies_breakdown()

        assert result["total"] is None
        assert result["error"] == "unexpected_error"
