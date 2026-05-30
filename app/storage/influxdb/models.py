"""Modelos Pydantic para o storage layer de governança."""

from __future__ import annotations

from pydantic import BaseModel, field_validator

MEASUREMENT_PREFIX = "gov_"


class GovernancePoint(BaseModel):
    """Ponto de dado para escrita em governance_metrics.

    Args:
        measurement: Nome do measurement (deve começar com 'gov_').
        tags: Tags de cardinalidade controlada (str → str).
        fields: Campos de valor (str → int | float | str).
        timestamp_ns: Timestamp em nanosegundos. None usa server time.
    """

    measurement: str
    tags: dict[str, str]
    fields: dict[str, int | float | str]
    timestamp_ns: int | None = None

    @field_validator("measurement")
    @classmethod
    def measurement_must_have_prefix(cls, v: str) -> str:
        """Rejeita measurements sem prefixo gov_ (namespace compartilhado)."""
        if not v.startswith(MEASUREMENT_PREFIX):
            raise ValueError(
                f"Measurement {v!r} rejeitado. "
                f"Todos devem começar com {MEASUREMENT_PREFIX!r}. "
                "Ex: gov_zabbix_problem, gov_github_pr, gov_zendesk_ticket"
            )
        return v

    @field_validator("fields")
    @classmethod
    def fields_must_not_be_empty(cls, v: dict[str, int | float | str]) -> dict[str, int | float | str]:
        """Garante que todo ponto tem ao menos um field."""
        if not v:
            raise ValueError("GovernancePoint deve ter ao menos um field")
        return v

    def to_line_protocol(self) -> str:
        """Serializa para InfluxDB Line Protocol.

        Returns:
            String no formato: measurement,tag=val field=val [timestamp]
        """
        tag_str = ",".join(f"{_esc(k)}={_esc(v)}" for k, v in sorted(self.tags.items()))
        field_str = ",".join(f"{_esc(k)}={_fmt_field(v)}" for k, v in self.fields.items())
        measurement = _esc_measurement(self.measurement)
        base = f"{measurement},{tag_str} {field_str}" if tag_str else f"{measurement} {field_str}"
        return f"{base} {self.timestamp_ns}" if self.timestamp_ns is not None else base


def _esc_measurement(value: str) -> str:
    return value.replace(" ", r"\ ").replace(",", r"\,")


def _esc(value: str) -> str:
    return value.replace(" ", r"\ ").replace(",", r"\,").replace("=", r"\=")


def _fmt_field(value: int | float | str) -> str:
    if isinstance(value, bool):
        raise TypeError(f"bool não é suportado como field: {value!r}")
    if isinstance(value, int):
        return f"{value}i"
    if isinstance(value, float):
        return repr(value)
    # string field
    escaped = value.replace('"', '\\"')
    return f'"{escaped}"'
