"""Modelos Pydantic v2 para o modulo de Inventario de Ativos de TI.

Edge contracts
--------------

1. UUID imutavel
   ``id`` e gerado automaticamente na criacao e nunca deve ser alterado.
   O service layer deve rejeitar qualquer tentativa de PATCH no campo id.

2. Unicidade por tipo (invariante de negocio)
   O par (nome, tipo) deve ser unico no banco. Essa restricao e aplicada
   no nivel de banco via unique constraint (issue #84) e nao no model,
   pois o model nao tem acesso ao estado global.

3. Soft delete
   Ativos nao sao removidos fisicamente. O campo ``deleted_at`` marca a
   remocao logica. O model ``Ativo`` representa o estado vivo; o service
   layer filtra ``deleted_at IS NULL`` em listagens (ADR-008, ADR-0003).

4. metadata tipado como dict[str, Any]
   Permite atributos especificos por tipo (ex: IP para servidores,
   fabricante para switches) sem schema rigido. Consumers devem tratar
   valores como opcionais e validar internamente.

5. contrato_id opcional
   Vinculo com o modulo Hero V1.0. None significa ativo sem contrato
   associado, que dispara o trigger SMTP #4 (Sprint 13).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")


class TipoAtivo(str):
    """Tipo de ativo de TI — usado como Literal nos models."""


TIPOS_VALIDOS: frozenset[str] = frozenset({"servidor", "switch", "app", "licenca", "endpoint"})
AMBIENTES_VALIDOS: frozenset[str] = frozenset({"prod", "hml", "dev"})
CRITICIDADES_VALIDAS: frozenset[str] = frozenset({"alta", "media", "baixa"})


class AtivoBase(BaseModel):
    """Campos compartilhados entre criacao, atualizacao e leitura de Ativo."""

    tipo: str = Field(description="Tipo do ativo: servidor | switch | app | licenca | endpoint")
    nome: str = Field(min_length=3, max_length=100, description="Nome do ativo (unico por tipo)")
    ambiente: str = Field(description="Ambiente: prod | hml | dev")
    criticidade: str = Field(description="Criticidade: alta | media | baixa")
    owner: str = Field(description="Email do responsavel tecnico")
    tags: list[str] = Field(default_factory=list, description="Tags livres para categorizacao")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Atributos especificos por tipo")
    contrato_id: UUID | None = Field(None, description="FK opcional para contrato do modulo Hero")

    @field_validator("tipo")
    @classmethod
    def validate_tipo(cls, v: str) -> str:
        """Normaliza para minusculo e valida contra o enum de tipos."""
        normalizado = v.strip().lower()
        if normalizado not in TIPOS_VALIDOS:
            raise ValueError(f"tipo '{v}' invalido. Valores aceitos: {sorted(TIPOS_VALIDOS)}")
        return normalizado

    @field_validator("ambiente")
    @classmethod
    def validate_ambiente(cls, v: str) -> str:
        normalizado = v.strip().lower()
        if normalizado not in AMBIENTES_VALIDOS:
            raise ValueError(f"ambiente '{v}' invalido. Valores aceitos: {sorted(AMBIENTES_VALIDOS)}")
        return normalizado

    @field_validator("criticidade")
    @classmethod
    def validate_criticidade(cls, v: str) -> str:
        normalizado = v.strip().lower()
        if normalizado not in CRITICIDADES_VALIDAS:
            raise ValueError(f"criticidade '{v}' invalida. Valores aceitos: {sorted(CRITICIDADES_VALIDAS)}")
        return normalizado

    @field_validator("tags", mode="before")
    @classmethod
    def normalize_tags(cls, v: list[Any]) -> list[str]:
        """Normaliza tags para minusculo e remove duplicatas preservando ordem."""
        seen: set[str] = set()
        result: list[str] = []
        for tag in v:
            normalized = str(tag).strip().lower()
            if normalized and normalized not in seen:
                seen.add(normalized)
                result.append(normalized)
        return result

    @field_validator("owner")
    @classmethod
    def validate_owner_email(cls, v: str) -> str:
        stripped = v.strip().lower()
        if not _EMAIL_RE.match(stripped):
            raise ValueError(f"owner '{v}' nao e um email valido")
        return stripped

    @field_validator("nome")
    @classmethod
    def validate_nome(cls, v: str) -> str:
        stripped = v.strip()
        if len(stripped) < 3:
            raise ValueError("nome deve ter pelo menos 3 caracteres apos remocao de espacos")
        return stripped


class AtivoCreate(AtivoBase):
    """Schema de entrada para criacao de um novo Ativo.

    ``id``, ``created_at`` e ``updated_at`` sao gerados pelo service layer.
    """


class AtivoUpdate(BaseModel):
    """Schema de entrada para atualizacao parcial (PATCH) de um Ativo.

    Todos os campos sao opcionais. Campos omitidos nao sao alterados.
    ``id``, ``created_at`` e ``deleted_at`` nao podem ser atualizados via PATCH.
    """

    nome: str | None = Field(None, min_length=3, max_length=100)
    ambiente: str | None = None
    criticidade: str | None = None
    owner: str | None = None
    tags: list[str] | None = None
    metadata: dict[str, Any] | None = None
    contrato_id: UUID | None = None

    @field_validator("ambiente")
    @classmethod
    def validate_ambiente(cls, v: str | None) -> str | None:
        if v is None:
            return v
        normalizado = v.strip().lower()
        if normalizado not in AMBIENTES_VALIDOS:
            raise ValueError(f"ambiente '{v}' invalido. Valores aceitos: {sorted(AMBIENTES_VALIDOS)}")
        return normalizado

    @field_validator("criticidade")
    @classmethod
    def validate_criticidade(cls, v: str | None) -> str | None:
        if v is None:
            return v
        normalizado = v.strip().lower()
        if normalizado not in CRITICIDADES_VALIDAS:
            raise ValueError(f"criticidade '{v}' invalida. Valores aceitos: {sorted(CRITICIDADES_VALIDAS)}")
        return normalizado

    @field_validator("tags", mode="before")
    @classmethod
    def normalize_tags(cls, v: list[Any] | None) -> list[str] | None:
        if v is None:
            return v
        seen: set[str] = set()
        result: list[str] = []
        for tag in v:
            normalized = str(tag).strip().lower()
            if normalized and normalized not in seen:
                seen.add(normalized)
                result.append(normalized)
        return result

    @model_validator(mode="after")
    def at_least_one_field(self) -> "AtivoUpdate":
        """Rejeita PATCH vazio — pelo menos um campo deve ser fornecido."""
        provided = {k for k, v in self.model_dump(exclude_unset=False).items() if v is not None}
        if not provided:
            raise ValueError("AtivoUpdate requer ao menos um campo para atualizar")
        return self


class Ativo(AtivoBase):
    """Representacao completa de um Ativo de TI (leitura do banco).

    Inclui campos gerados pelo sistema (id, timestamps, soft delete).
    """

    id: UUID = Field(default_factory=uuid4, description="Identificador unico imutavel")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp de criacao (UTC)",
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp da ultima atualizacao (UTC)",
    )
    deleted_at: datetime | None = Field(None, description="Soft delete: None = ativo, datetime = removido")

    model_config = {"from_attributes": True}

    @property
    def is_deleted(self) -> bool:
        """True se o ativo foi removido logicamente."""
        return self.deleted_at is not None

    @property
    def has_contract(self) -> bool:
        """True se o ativo esta vinculado a um contrato."""
        return self.contrato_id is not None


class AtivoFilters(BaseModel):
    """Filtros opcionais para listagem de ativos.

    Todos os campos sao opcionais. Campos omitidos nao filtram.
    """

    tipo: str | None = None
    ambiente: str | None = None
    criticidade: str | None = None
    owner: str | None = None
    tag: str | None = Field(None, description="Filtra ativos que contenham esta tag")
    include_deleted: bool = Field(False, description="Se True, inclui ativos com soft delete")

    @field_validator("tipo")
    @classmethod
    def validate_tipo(cls, v: str | None) -> str | None:
        if v is None:
            return v
        normalizado = v.strip().lower()
        if normalizado not in TIPOS_VALIDOS:
            raise ValueError(f"tipo '{v}' invalido. Valores aceitos: {sorted(TIPOS_VALIDOS)}")
        return normalizado

    @field_validator("ambiente")
    @classmethod
    def validate_ambiente(cls, v: str | None) -> str | None:
        if v is None:
            return v
        normalizado = v.strip().lower()
        if normalizado not in AMBIENTES_VALIDOS:
            raise ValueError(f"ambiente '{v}' invalido. Valores aceitos: {sorted(AMBIENTES_VALIDOS)}")
        return normalizado

    @field_validator("criticidade")
    @classmethod
    def validate_criticidade(cls, v: str | None) -> str | None:
        if v is None:
            return v
        normalizado = v.strip().lower()
        if normalizado not in CRITICIDADES_VALIDAS:
            raise ValueError(f"criticidade '{v}' invalida. Valores aceitos: {sorted(CRITICIDADES_VALIDAS)}")
        return normalizado


class AtivoStats(BaseModel):
    """Metricas agregadas de ativos para o widget do dashboard.

    Gerado pelo AtivoService.get_stats() a partir do estado atual no SQLite.
    """

    total: int = Field(ge=0, description="Total de ativos ativos (nao deletados)")
    por_tipo: dict[str, int] = Field(default_factory=dict, description="Contagem por tipo")
    por_criticidade: dict[str, int] = Field(default_factory=dict, description="Contagem por criticidade")
    por_ambiente: dict[str, int] = Field(default_factory=dict, description="Contagem por ambiente")
    sem_owner: int = Field(ge=0, description="Ativos sem owner definido (alerta)")
    sem_contrato: int = Field(ge=0, description="Ativos sem contrato vinculado (trigger SMTP #4)")
