"""Modelos Pydantic v2 para integração com Zabbix API."""

from __future__ import annotations

from datetime import datetime
from enum import IntEnum

from pydantic import BaseModel, Field, field_validator


class ZabbixSeverity(IntEnum):
    """Níveis de severidade do Zabbix (0–5)."""

    NOT_CLASSIFIED = 0
    INFORMATION = 1
    WARNING = 2
    AVERAGE = 3
    HIGH = 4
    DISASTER = 5

    @property
    def label(self) -> str:
        return self.name.lower().replace("_", " ")


class ZabbixHost(BaseModel):
    """Host monitorado pelo Zabbix."""

    hostid: str
    host: str
    name: str
    status: int = Field(ge=0, le=1, description="0=habilitado, 1=desabilitado")
    available: int = Field(ge=0, le=2, default=0, description="0=desconhecido, 1=disponível, 2=indisponível")

    @property
    def is_up(self) -> bool:
        return self.available == 1


class ZabbixProblem(BaseModel):
    """Problema ativo no Zabbix."""

    eventid: str
    objectid: str = Field(description="ID do trigger que gerou o problema")
    name: str
    severity: ZabbixSeverity
    acknowledged: bool
    clock: datetime
    r_clock: datetime | None = Field(None, description="Hora de resolução (None se ainda aberto)")
    hosts: list[str] = Field(default_factory=list, description="Nomes dos hosts afetados")

    @field_validator("clock", "r_clock", mode="before")
    @classmethod
    def parse_unix_timestamp(cls, v: int | str | None) -> datetime | None:
        if v is None or v == "0" or v == 0:
            return None
        return datetime.fromtimestamp(int(v))

    @field_validator("acknowledged", mode="before")
    @classmethod
    def parse_acknowledged(cls, v: str | bool | int) -> bool:
        if isinstance(v, bool):
            return v
        return str(v) == "1"

    @field_validator("severity", mode="before")
    @classmethod
    def parse_severity(cls, v: str | int) -> ZabbixSeverity:
        return ZabbixSeverity(int(v))

    @property
    def is_resolved(self) -> bool:
        return self.r_clock is not None

    @property
    def duration_seconds(self) -> int:
        if self.is_resolved and self.r_clock:
            return max(0, int((self.r_clock - self.clock).total_seconds()))
        return 0


class ZabbixHostSummary(BaseModel):
    """Resumo de disponibilidade dos hosts monitorados."""

    total: int
    up: int
    down: int
    unknown: int
    up_pct: float = Field(ge=0.0, le=100.0)


class AcknowledgeRequest(BaseModel):
    """Payload para reconhecimento de evento."""

    eventid: str
    message: str = Field(
        default="Resolvido via dashboard",
        min_length=1,
        max_length=255,
    )
