"""Testes do transformer Zabbix → GovernancePoint."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.collectors.zabbix.models import ZabbixHost, ZabbixProblem
from app.collectors.zabbix.transformer import problem_to_point


def _make_problem(**overrides) -> ZabbixProblem:
    defaults = {
        "eventid": "1001",
        "objectid": "501",
        "name": "Host unavailable",
        "severity": 4,
        "clock": 1748000000,
        "acknowledged": "0",
        "r_eventid": None,
        "r_clock": None,
        "hosts": [ZabbixHost(hostid="201", host="srv-core-01", name="SRV-CORE-01")],
    }
    return ZabbixProblem(**{**defaults, **overrides})


class TestTransformOpenProblem:
    def test_state_is_open(self) -> None:
        point = problem_to_point(_make_problem())
        assert point.tags["state"] == "open"

    def test_duration_is_zero_when_open(self) -> None:
        point = problem_to_point(_make_problem())
        assert point.fields["duration_seconds"] == 0

    def test_measurement_has_gov_prefix(self) -> None:
        point = problem_to_point(_make_problem())
        assert point.measurement.startswith("gov_")
        assert point.measurement == "gov_zabbix_problem"

    def test_timestamp_ns_from_clock(self) -> None:
        point = problem_to_point(_make_problem(clock=1748000000))
        assert point.timestamp_ns == 1748000000 * 1_000_000_000

    def test_count_field_is_one(self) -> None:
        point = problem_to_point(_make_problem())
        assert point.fields["count"] == 1

    def test_severity_mapped_to_label(self) -> None:
        point = problem_to_point(_make_problem(severity=5))
        assert point.tags["severity"] == "disaster"

    def test_acknowledged_false(self) -> None:
        point = problem_to_point(_make_problem(acknowledged="0"))
        assert point.tags["acknowledged"] == "false"


class TestTransformResolvedProblem:
    def test_state_is_resolved(self) -> None:
        point = problem_to_point(_make_problem(r_eventid="2001", r_clock=1748001000))
        assert point.tags["state"] == "resolved"

    def test_duration_calculated(self) -> None:
        point = problem_to_point(_make_problem(clock=1748000000, r_eventid="2001", r_clock=1748001000))
        assert point.fields["duration_seconds"] == 1000

    def test_acknowledged_true(self) -> None:
        point = problem_to_point(_make_problem(r_eventid="2001", r_clock=1748001000, acknowledged="1"))
        assert point.tags["acknowledged"] == "true"


class TestTransformEdgeCases:
    def test_no_host_fallback_to_unknown(self) -> None:
        point = problem_to_point(_make_problem(hosts=[]))
        assert point.tags["host"] == "unknown"

    def test_first_host_used_when_multiple(self) -> None:
        hosts = [
            ZabbixHost(hostid="1", host="host-a", name="HOST-A"),
            ZabbixHost(hostid="2", host="host-b", name="HOST-B"),
        ]
        point = problem_to_point(_make_problem(hosts=hosts))
        assert point.tags["host"] == "HOST-A"

    def test_severity_zero_label(self) -> None:
        point = problem_to_point(_make_problem(severity=0))
        assert point.tags["severity"] == "not_classified"

    def test_eventid_in_fields(self) -> None:
        point = problem_to_point(_make_problem(eventid="9999"))
        assert point.fields["eventid"] == "9999"

    def test_trigger_name_in_fields(self) -> None:
        point = problem_to_point(_make_problem(name="Disk full on /var"))
        assert point.fields["trigger_name"] == "Disk full on /var"


class TestZabbixProblemValidation:
    def test_rejects_severity_above_5(self) -> None:
        with pytest.raises(ValidationError):
            ZabbixProblem(
                eventid="1",
                objectid="1",
                name="test",
                severity=99,
                clock=1000,
                acknowledged="0",
            )

    def test_rejects_severity_below_0(self) -> None:
        with pytest.raises(ValidationError):
            ZabbixProblem(
                eventid="1",
                objectid="1",
                name="test",
                severity=-1,
                clock=1000,
                acknowledged="0",
            )
