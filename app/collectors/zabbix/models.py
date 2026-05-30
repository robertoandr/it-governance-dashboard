"""Modelos Pydantic para dados crus da API Zabbix."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ZabbixHost(BaseModel):
    """Host Zabbix associado a um problema."""

    hostid: str
    host: str
    name: str


class ZabbixProblem(BaseModel):
    """Problema ativo (ou recém-resolvido) retornado por problem.get.

    Severity mapping (Zabbix):
        0 = Not classified
        1 = Information
        2 = Warning
        3 = Average
        4 = High
        5 = Disaster
    """

    eventid: str
    objectid: str
    name: str
    severity: int = Field(ge=0, le=5)
    clock: int
    acknowledged: str  # "0" ou "1" (string na API Zabbix)
    r_eventid: str | None = None
    r_clock: int | None = None
    hosts: list[ZabbixHost] = Field(default_factory=list)

    @property
    def is_acknowledged(self) -> bool:
        return self.acknowledged == "1"

    @property
    def is_resolved(self) -> bool:
        return self.r_eventid is not None and self.r_eventid != "0"


class ZabbixTrigger(BaseModel):
    """Trigger Zabbix (metadados do item que gerou o problema)."""

    triggerid: str
    description: str
    priority: int = Field(ge=0, le=5)
