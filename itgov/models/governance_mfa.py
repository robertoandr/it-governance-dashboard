"""Modelos Pydantic para adoção de MFA — Governança de TI.

Segue o padrão multi-model do projeto: modelos separados por responsabilidade.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class Classificacao(StrEnum):
    """Classificação de conta M365 para fins de adoção de MFA."""

    humano = "human"
    nao_humano = "non_human"
    sala = "room"
    pendente_revisao = "pending_review"


class Conta(BaseModel):
    """Representação de uma conta M365 com status de MFA e classificação."""

    upn: str = Field(..., description="User Principal Name")
    nome_exibicao: str = Field(..., description="Nome de exibição")
    mfa_habilitado: bool = Field(..., description="isMfaRegistered (fonte real de MFA)")
    habilitada: bool = Field(..., description="Conta ativa no Entra ID")
    classificacao: Classificacao


class ReclassificarRequest(BaseModel):
    """Corpo da requisição para reclassificação manual de uma conta."""

    upn: str = Field(..., min_length=1, description="UPN da conta a reclassificar")
    classificacao: Classificacao

    @field_validator("upn")
    @classmethod
    def normalizar_upn(cls, v: str) -> str:
        return v.strip().lower()


class DetalheGovernancaMFA(BaseModel):
    """Resultado completo do cálculo de adoção de MFA.

    Denominador: somente contas classificadas como 'human' + habilitadas.
    Contas room, non_human e pending_review ficam fora do denominador.
    """

    adocao_pct: float = Field(..., description="Adoção de MFA em % (meta: 87%)", ge=0.0, le=100.0)
    denominador: int = Field(..., description="Total de humanos ativos (base do cálculo)")
    com_mfa: int = Field(..., description="Humanos ativos com MFA registrado")
    sem_mfa: int = Field(..., description="Humanos ativos sem MFA")
    total_humanos: int = Field(..., description="Total bucket human")
    total_nao_humanos: int = Field(..., description="Total bucket non_human")
    total_salas: int = Field(..., description="Total bucket room")
    total_pendente_revisao: int = Field(..., description="Total bucket pending_review")
    pendentes_revisao: list[Conta] = Field(default_factory=list, description="Contas aguardando revisão manual")
