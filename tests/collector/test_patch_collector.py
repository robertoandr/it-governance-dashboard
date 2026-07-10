"""Tests for collector/jobs/patch_collector.py."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import config as _cfg

# Patch config before importing the module under test
_mock_settings = MagicMock()
_mock_settings.LDAP_SERVER = "ldap://dc.test.local"
_mock_settings.LDAP_BASE_DN = "DC=test,DC=local"
_mock_settings.LDAP_BIND_DN = "TEST\\svc-patch-ro"
_mock_settings.LDAP_BIND_PASSWORD = "pw"
_mock_settings.WINRM_USER = "TEST\\svc-patch-ro"
_mock_settings.WINRM_PASSWORD = "pw"
_mock_settings.WINRM_TIMEOUT_S = 10
_mock_settings.PATCH_THRESHOLD_DAYS = 35
_mock_settings.PATCH_MAX_WORKERS = 5
_mock_settings.PATCH_STALE_MACHINE_DAYS = 60
_mock_settings.INFLUX_URL = "http://localhost:8086"
_mock_settings.INFLUX_TOKEN = "influx-tok"
_mock_settings.INFLUX_ORG = "testorg"
_mock_settings.INFLUX_BUCKET_RAW = "governance_raw"
_cfg.settings = _mock_settings

from collector.jobs.patch_collector import (  # noqa: E402
    _build_host_points,
    _collect_all,
    _enum_domain_computers,
    _HostResult,
    _query_host,
    collect,
)

_PS_OK = {
    "Reachable": True,
    "IsCompliant": True,
    "LastPatchDate": "2026-07-01",
    "DaysSinceLast": 9,
    "HotfixCount": 42,
    "NullDateCount": 0,
    "OsVersion": "Microsoft Windows Server 2022 Standard",
    "RebootRequired": False,
    "PendingUpdates": 3,
    "CriticalUpdates": 1,
}


def _mock_run_ps_result(status_code=0, std_out=b"", std_err=b""):
    r = MagicMock()
    r.status_code = status_code
    r.std_out = std_out
    r.std_err = std_err
    return r


# ── _query_host ────────────────────────────────────────────────────────────


def test_query_host_sessao_lanca_excecao_retorna_warning_sem_propagar():
    """Host inacessível: erro vira warning, não exceção fatal."""
    with patch(
        "collector.jobs.patch_collector.winrm.Session",
        side_effect=ConnectionError("no route to host"),
    ):
        result = _query_host("srv-offline.test.local")

    assert result.reachable is False
    assert result.error == "winrm_exception"
    assert result.computer_name == "srv-offline.test.local"


def test_query_host_powershell_erro_nao_propaga():
    mock_session = MagicMock()
    mock_session.run_ps.return_value = _mock_run_ps_result(status_code=1, std_err=b"Access is denied")

    with patch("collector.jobs.patch_collector.winrm.Session", return_value=mock_session):
        result = _query_host("srv-denied.test.local")

    assert result.reachable is False
    assert "Access is denied" in result.error


def test_query_host_ps_reporta_unreachable():
    payload = json.dumps({"Reachable": False, "Error": "Get-HotFix falhou"}).encode()
    mock_session = MagicMock()
    mock_session.run_ps.return_value = _mock_run_ps_result(std_out=payload)

    with patch("collector.jobs.patch_collector.winrm.Session", return_value=mock_session):
        result = _query_host("srv-noaccess.test.local")

    assert result.reachable is False
    assert result.error == "Get-HotFix falhou"


def test_query_host_sucesso_preenche_todos_os_campos():
    mock_session = MagicMock()
    mock_session.run_ps.return_value = _mock_run_ps_result(std_out=json.dumps(_PS_OK).encode())

    with patch("collector.jobs.patch_collector.winrm.Session", return_value=mock_session):
        result = _query_host("srv-ok.test.local")

    assert result.reachable is True
    assert result.is_compliant is True
    assert result.hotfix_count == 42
    assert result.os_version == "Microsoft Windows Server 2022 Standard"
    assert result.reboot_required is False
    assert result.pending_updates == 3
    assert result.critical_updates == 1


def test_query_host_wua_error_nao_torna_host_inalcancavel():
    """Busca de updates pendentes (WUA) pode falhar sem derrubar o resultado do host."""
    payload = dict(_PS_OK, PendingUpdates=None, CriticalUpdates=None, WuaError="COM object blocked")
    mock_session = MagicMock()
    mock_session.run_ps.return_value = _mock_run_ps_result(std_out=json.dumps(payload).encode())

    with patch("collector.jobs.patch_collector.winrm.Session", return_value=mock_session):
        result = _query_host("srv-wua-fail.test.local")

    assert result.reachable is True
    assert result.pending_updates is None
    assert result.critical_updates is None


# ── _collect_all ─────────────────────────────────────────────────────────────


def test_collect_all_lista_vazia_nao_quebra():
    with patch("collector.jobs.patch_collector._enum_domain_computers", return_value=[]):
        stats, results = _collect_all()

    assert stats["total_machines"] == 0
    assert stats["reachable"] == 0
    assert stats["compliance_pct"] == 0.0
    assert results == []


def test_collect_all_host_com_falha_nao_interrompe_outros():
    hosts = ["srv-a.test.local", "srv-b.test.local", "srv-c.test.local"]

    def _fake_query(host):
        if host == "srv-b.test.local":
            raise RuntimeError("should never propagate")  # sanity: _query_host protege internamente
        return _HostResult(host, reachable=True, is_compliant=True)

    def _safe_query(host):
        try:
            return _fake_query(host)
        except RuntimeError:
            return _HostResult(host, error="winrm_exception")

    with (
        patch("collector.jobs.patch_collector._enum_domain_computers", return_value=hosts),
        patch("collector.jobs.patch_collector._query_host", side_effect=_safe_query),
    ):
        stats, results = _collect_all()

    assert stats["total_machines"] == 3
    assert stats["reachable"] == 2
    assert stats["unreachable"] == 1
    assert {r.computer_name for r in results} == set(hosts)


def test_enum_domain_computers_filtro_restrito_a_windows_server():
    """Regressão: o filtro LDAP precisa mirar Windows Server, não todo computer object."""
    mock_conn = MagicMock()
    mock_conn.entries = []

    with (
        patch("collector.jobs.patch_collector.Server"),
        patch("collector.jobs.patch_collector.Connection", return_value=mock_conn),
    ):
        _enum_domain_computers()

    args, kwargs = mock_conn.search.call_args
    ldap_filter = args[1] if len(args) > 1 else kwargs.get("search_filter", args[0])
    assert "Windows Server" in ldap_filter


# ── _build_host_points ────────────────────────────────────────────────────────


def test_build_host_points_ignora_hosts_inalcancaveis():
    results = [
        _HostResult(
            "srv-ok.test.local",
            reachable=True,
            os_version="Windows Server 2022",
            pending_updates=5,
            critical_updates=2,
            reboot_required=True,
        ),
        _HostResult("srv-down.test.local", reachable=False, error="winrm_exception"),
    ]

    points = _build_host_points(results)

    assert len(points) == 1
    lp = points[0].to_line_protocol()
    assert "gov_windows_patches" in lp
    assert "host=srv-ok.test.local" in lp
    assert "os=Windows\\ Server\\ 2022" in lp
    assert "pending_updates=5i" in lp
    assert "critical_updates=2i" in lp
    assert "reboot_required=true" in lp


def test_build_host_points_sem_wua_omite_campos_none():
    results = [
        _HostResult(
            "srv-partial.test.local",
            reachable=True,
            os_version="Windows Server 2019",
            pending_updates=None,
            critical_updates=None,
        ),
    ]

    lp = _build_host_points(results)[0].to_line_protocol()

    assert "pending_updates" not in lp
    assert "critical_updates" not in lp
    assert "last_scan=" in lp


# ── collect ──────────────────────────────────────────────────────────────────


def test_collect_escreve_agregado_e_por_host():
    hosts = ["srv-1.test.local"]
    mock_session = MagicMock()
    mock_session.run_ps.return_value = _mock_run_ps_result(std_out=json.dumps(_PS_OK).encode())

    mock_client = MagicMock()
    mock_write_api = MagicMock()
    mock_client.write_api.return_value = mock_write_api
    mock_client.__enter__.return_value = mock_client

    with (
        patch("collector.jobs.patch_collector._enum_domain_computers", return_value=hosts),
        patch("collector.jobs.patch_collector.winrm.Session", return_value=mock_session),
        patch("collector.jobs.patch_collector.InfluxDBClient", return_value=mock_client),
    ):
        collect()

    mock_write_api.write.assert_called_once()
    _, kwargs = mock_write_api.write.call_args
    assert kwargs["bucket"] == "governance_raw"
    records = kwargs["record"]
    measurements = {r.to_line_protocol().split(",")[0].split(" ")[0] for r in records}
    assert "gov_patch_compliance" in measurements
    assert "gov_windows_patches" in measurements


def test_collect_lista_vazia_ainda_escreve_agregado_zerado():
    mock_client = MagicMock()
    mock_write_api = MagicMock()
    mock_client.write_api.return_value = mock_write_api
    mock_client.__enter__.return_value = mock_client

    with (
        patch("collector.jobs.patch_collector._enum_domain_computers", return_value=[]),
        patch("collector.jobs.patch_collector.InfluxDBClient", return_value=mock_client),
    ):
        collect()

    _, kwargs = mock_write_api.write.call_args
    records = kwargs["record"]
    assert len(records) == 1  # só o agregado, sem pontos por host
    assert "gov_patch_compliance" in records[0].to_line_protocol()


def test_collect_falha_de_escrita_influx_propaga_e_loga():
    with (
        patch("collector.jobs.patch_collector._enum_domain_computers", return_value=[]),
        patch(
            "collector.jobs.patch_collector.InfluxDBClient",
            side_effect=RuntimeError("influx down"),
        ),
    ):
        try:
            collect()
            raised = False
        except RuntimeError:
            raised = True

    assert raised, "collect() deve propagar falha de escrita para o scheduler tratar (run() captura)"
