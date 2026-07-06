"""Modelos Pydantic para o pilar Compliance — Governança de TI."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RecomendacaoControle(BaseModel):
    """Controle do Secure Score com implementação baixa — ação recomendada."""

    control_name: str
    categoria: str
    descricao: str
    score_pct: float = Field(ge=0.0, le=100.0)


class ControlePendente(BaseModel):
    """Controle do Secure Score (secureScoreControlProfiles) — linha da tabela completa.

    ``status`` é um de "implementado", "pendente" ou "ignorado".
    ``acao`` traz o texto de ``remediation`` retornado pela API do Graph — a
    coluna existe para a UI de ação (M-05b: botão "Criar trigger Zabbix"), que
    ainda não foi implementado.
    """

    control_name: str
    title: str
    categoria: str
    max_score: float = Field(ge=0.0)
    score: float = Field(ge=0.0)
    status: str
    acao: str
    action_url: str | None = None


class HistoricoPonto(BaseModel):
    """Ponto da série histórica do Secure Score (InfluxDB gov_m365_secure_score)."""

    time: str
    pct: float = Field(ge=0.0, le=100.0)


class ComplianceSummary(BaseModel):
    """Resumo de Compliance a partir do Microsoft Secure Score.

    ``pct`` é ``None`` quando não há dado coletado (Graph indisponível ou
    sem histórico) — não deve ser confundido com 0% de compliance.

    ``security_controls`` contém status dos controles de segurança específicos
    (Safe Links, Safe Attachments, Audit Log) quando disponíveis.

    ``comparative_pct``/``comparative_basis`` vêm de ``averageComparativeScores``
    (Graph) — média de tenants comparáveis, ``None`` quando a API não retorna
    o campo (ex.: tenant pequeno demais para ter grupo de comparação).

    ``historico_90d`` e ``variacao_30d`` vêm do InfluxDB (measurement
    ``gov_m365_secure_score``, gravado a cada 6h) — ``None``/vazio quando
    ainda não há histórico suficiente.
    """

    current_score: float | None = Field(default=None, ge=0.0)
    max_score: float | None = Field(default=None, ge=0.0)
    pct: float | None = Field(default=None, ge=0.0, le=100.0)
    category_breakdown: dict[str, float] = Field(default_factory=dict)
    recomendacoes: list[RecomendacaoControle] = Field(default_factory=list)
    security_controls: dict = Field(default_factory=dict)
    comparative_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    comparative_basis: str | None = None
    controles: list[ControlePendente] = Field(default_factory=list)
    historico_90d: list[HistoricoPonto] = Field(default_factory=list)
    variacao_30d: float | None = None
