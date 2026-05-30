"""Testes de conformidade com InfluxDB Line Protocol para GovernancePoint.

Referência: https://docs.influxdata.com/influxdb/v2/reference/syntax/line-protocol/
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.storage.influxdb.models import GovernancePoint, _esc, _esc_measurement, _fmt_field


def _point(**kwargs) -> GovernancePoint:
    defaults = {
        "measurement": "gov_test",
        "tags": {"host": "srv-01"},
        "fields": {"count": 1},
    }
    return GovernancePoint(**{**defaults, **kwargs})


# ─────────────────────────────────────────────────────────────────────
# Measurement validation
# ─────────────────────────────────────────────────────────────────────


class TestMeasurementValidation:
    def test_rejects_missing_prefix(self) -> None:
        with pytest.raises(ValidationError, match="gov_"):
            _point(measurement="zabbix_problem")

    def test_accepts_valid_prefix(self) -> None:
        p = _point(measurement="gov_zabbix_problem")
        assert p.measurement == "gov_zabbix_problem"

    def test_rejects_empty_measurement(self) -> None:
        with pytest.raises(ValidationError):
            _point(measurement="")

    def test_rejects_fields_empty(self) -> None:
        with pytest.raises(ValidationError, match="field"):
            _point(fields={})


# ─────────────────────────────────────────────────────────────────────
# Line protocol structure
# ─────────────────────────────────────────────────────────────────────


class TestLineProtocolStructure:
    def test_basic_format(self) -> None:
        p = _point(tags={"env": "prod"}, fields={"count": 1})
        line = p.to_line_protocol()
        assert line.startswith("gov_test,")
        assert " count=1i" in line

    def test_no_timestamp_omits_ts(self) -> None:
        p = _point(timestamp_ns=None)
        assert not p.to_line_protocol().endswith(" ")
        parts = p.to_line_protocol().split(" ")
        assert len(parts) == 2  # measurement,tags + fields

    def test_timestamp_ns_appended(self) -> None:
        p = _point(timestamp_ns=1748000000000000000)
        assert p.to_line_protocol().endswith(" 1748000000000000000")

    def test_tags_sorted_alphabetically(self) -> None:
        p = _point(tags={"z": "last", "a": "first"}, fields={"v": 1})
        tag_part = p.to_line_protocol().split(" ")[0]
        assert tag_part.index("a=") < tag_part.index("z=")

    def test_no_tags_no_leading_comma(self) -> None:
        p = _point(tags={}, fields={"v": 1})
        line = p.to_line_protocol()
        assert not line.startswith("gov_test,")
        assert line.startswith("gov_test ")


# ─────────────────────────────────────────────────────────────────────
# Tag escaping
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("simple", "simple"),
        ("with space", r"with\ space"),
        ("with,comma", r"with\,comma"),
        ("with=equals", r"with\=equals"),
        ("space,comma=all", r"space\,comma\=all"),
    ],
)
def test_esc_tag(raw: str, expected: str) -> None:
    assert _esc(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("gov_test", "gov_test"),
        ("gov_has space", r"gov_has\ space"),
        ("gov_has,comma", r"gov_has\,comma"),
        # measurement does NOT escape '='
        ("gov_has=equals", "gov_has=equals"),
    ],
)
def test_esc_measurement(raw: str, expected: str) -> None:
    assert _esc_measurement(raw) == expected


def test_tag_escaping_in_line_protocol() -> None:
    p = _point(tags={"host name": "my,server"}, fields={"v": 1})
    line = p.to_line_protocol()
    # split(" ") não pode ser usado — espaços escapados dentro de tags
    assert r"host\ name" in line
    assert r"my\,server" in line


# ─────────────────────────────────────────────────────────────────────
# Field formatting
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value, expected",
    [
        (42, "42i"),
        (0, "0i"),
        (-1, "-1i"),
        (3.14, "3.14"),
        (0.0, "0.0"),
        ("hello", '"hello"'),
        ('say "hi"', '"say \\"hi\\""'),
        ("", '""'),
    ],
)
def test_fmt_field(value: int | float | str, expected: str) -> None:
    assert _fmt_field(value) == expected


def test_fmt_field_rejects_bool() -> None:
    with pytest.raises(TypeError, match="bool"):
        _fmt_field(True)  # type: ignore[arg-type]


def test_int_field_uses_i_suffix() -> None:
    p = _point(fields={"count": 99})
    assert "count=99i" in p.to_line_protocol()


def test_float_field_no_suffix() -> None:
    p = _point(fields={"score": 3.14})
    assert "score=3.14" in p.to_line_protocol()
    assert "score=3.14i" not in p.to_line_protocol()


def test_string_field_quoted() -> None:
    p = _point(fields={"label": "hello world"})
    assert 'label="hello world"' in p.to_line_protocol()


def test_string_field_with_quotes_escaped() -> None:
    p = _point(fields={"msg": 'say "hi"'})
    assert 'msg="say \\"hi\\""' in p.to_line_protocol()


def test_multiple_fields_all_present() -> None:
    p = _point(fields={"count": 1, "score": 0.5, "label": "ok"})
    line = p.to_line_protocol()
    assert "count=1i" in line
    assert "score=0.5" in line
    assert 'label="ok"' in line
