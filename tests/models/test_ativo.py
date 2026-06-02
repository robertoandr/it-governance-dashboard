"""Testes para itgov/models/ativo.py — Pydantic v2 model Ativo.

Cobre AtivoCreate, AtivoUpdate, Ativo, AtivoFilters e AtivoStats.
Meta de coverage: >=95% do arquivo (ADR-001 + issue #83).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from itgov.models.ativo import (
    AMBIENTES_VALIDOS,
    CRITICIDADES_VALIDAS,
    TIPOS_VALIDOS,
    Ativo,
    AtivoCreate,
    AtivoFilters,
    AtivoStats,
    AtivoUpdate,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────

VALID_CREATE: dict = {
    "tipo": "servidor",
    "nome": "SRV-CORE-01",
    "ambiente": "prod",
    "criticidade": "alta",
    "owner": "ti@empresa.com",
}


def make_create(**overrides) -> AtivoCreate:
    return AtivoCreate(**{**VALID_CREATE, **overrides})


def make_ativo(**overrides) -> Ativo:
    return Ativo(**{**VALID_CREATE, **overrides})


# ── AtivoCreate — campo obrigatorios e defaults ───────────────────────────────


class TestAtivoCreate:
    def test_happy_path_minimal(self):
        a = make_create()
        assert a.tipo == "servidor"
        assert a.nome == "SRV-CORE-01"
        assert a.ambiente == "prod"
        assert a.criticidade == "alta"
        assert a.owner == "ti@empresa.com"
        assert a.tags == []
        assert a.metadata == {}
        assert a.contrato_id is None

    def test_tags_deduplicados_e_normalizados(self):
        a = make_create(tags=["TI", "ti", "Infra", "INFRA", "core"])
        assert a.tags == ["ti", "infra", "core"]

    def test_tags_vazias_ignoradas(self):
        a = make_create(tags=["", "  ", "valid"])
        assert a.tags == ["valid"]

    def test_nome_stripped(self):
        a = make_create(nome="  SRV-01  ")
        assert a.nome == "SRV-01"

    def test_tipo_case_insensitive(self):
        a = make_create(tipo="SERVIDOR")
        assert a.tipo == "servidor"

    def test_ambiente_case_insensitive(self):
        a = make_create(ambiente="PROD")
        assert a.ambiente == "prod"

    def test_criticidade_case_insensitive(self):
        a = make_create(criticidade="ALTA")
        assert a.criticidade == "alta"

    def test_todos_tipos_validos(self):
        for tipo in TIPOS_VALIDOS:
            a = make_create(tipo=tipo)
            assert a.tipo == tipo

    def test_todos_ambientes_validos(self):
        for amb in AMBIENTES_VALIDOS:
            a = make_create(ambiente=amb)
            assert a.ambiente == amb

    def test_todas_criticidades_validas(self):
        for crit in CRITICIDADES_VALIDAS:
            a = make_create(criticidade=crit)
            assert a.criticidade == crit

    def test_contrato_id_aceita_uuid(self):
        cid = uuid4()
        a = make_create(contrato_id=cid)
        assert a.contrato_id == cid

    def test_metadata_dict_arbitrario(self):
        meta = {"ip": "10.0.0.1", "rack": "A3", "portas": 48}
        a = make_create(metadata=meta)
        assert a.metadata == meta


class TestAtivoCreateValidacao:
    def test_tipo_invalido_levanta_erro(self):
        with pytest.raises(ValidationError) as exc:
            make_create(tipo="mainframe")
        assert "tipo" in str(exc.value).lower() or "invalido" in str(exc.value).lower()

    def test_ambiente_invalido_levanta_erro(self):
        with pytest.raises(ValidationError):
            make_create(ambiente="staging")

    def test_criticidade_invalida_levanta_erro(self):
        with pytest.raises(ValidationError):
            make_create(criticidade="critica")

    def test_nome_muito_curto_levanta_erro(self):
        with pytest.raises(ValidationError):
            make_create(nome="AB")

    def test_nome_curto_apos_strip_levanta_erro(self):
        with pytest.raises(ValidationError):
            make_create(nome="  A  ")

    def test_nome_muito_longo_levanta_erro(self):
        with pytest.raises(ValidationError):
            make_create(nome="X" * 101)

    def test_owner_invalido_levanta_erro(self):
        with pytest.raises(ValidationError):
            make_create(owner="nao-e-email")

    def test_campos_obrigatorios_ausentes(self):
        with pytest.raises(ValidationError):
            AtivoCreate(tipo="servidor", nome="srv")


# ── AtivoUpdate ───────────────────────────────────────────────────────────────


class TestAtivoUpdate:
    def test_update_parcial_nome(self):
        u = AtivoUpdate(nome="SRV-NOVO")
        assert u.nome == "SRV-NOVO"
        assert u.ambiente is None

    def test_update_parcial_criticidade(self):
        u = AtivoUpdate(criticidade="baixa")
        assert u.criticidade == "baixa"

    def test_update_normaliza_criticidade(self):
        u = AtivoUpdate(criticidade="MEDIA")
        assert u.criticidade == "media"

    def test_update_normaliza_ambiente(self):
        u = AtivoUpdate(ambiente="HML")
        assert u.ambiente == "hml"

    def test_update_ambiente_invalido(self):
        with pytest.raises(ValidationError):
            AtivoUpdate(ambiente="producao")

    def test_update_criticidade_invalida(self):
        with pytest.raises(ValidationError):
            AtivoUpdate(criticidade="urgente")

    def test_update_vazio_levanta_erro(self):
        with pytest.raises(ValidationError) as exc:
            AtivoUpdate()
        assert "pelo menos um campo" in str(exc.value).lower() or "requer" in str(exc.value).lower()

    def test_update_contrato_id_uuid(self):
        cid = uuid4()
        u = AtivoUpdate(contrato_id=cid)
        assert u.contrato_id == cid

    def test_update_tags_normalizadas(self):
        u = AtivoUpdate(tags=["TI", "TI", "infra"])
        assert u.tags == ["ti", "infra"]


# ── Ativo (model completo) ────────────────────────────────────────────────────


class TestAtivo:
    def test_id_gerado_automaticamente(self):
        a = make_ativo()
        assert isinstance(a.id, UUID)

    def test_ids_distintos(self):
        a1, a2 = make_ativo(), make_ativo()
        assert a1.id != a2.id

    def test_created_at_utc(self):
        a = make_ativo()
        assert a.created_at.tzinfo is not None

    def test_updated_at_utc(self):
        a = make_ativo()
        assert a.updated_at.tzinfo is not None

    def test_deleted_at_none_por_padrao(self):
        a = make_ativo()
        assert a.deleted_at is None

    def test_is_deleted_false_por_padrao(self):
        a = make_ativo()
        assert a.is_deleted is False

    def test_is_deleted_true_quando_deleted_at_definido(self):
        a = make_ativo(deleted_at=datetime.now(UTC))
        assert a.is_deleted is True

    def test_has_contract_false_por_padrao(self):
        a = make_ativo()
        assert a.has_contract is False

    def test_has_contract_true_com_contrato_id(self):
        a = make_ativo(contrato_id=uuid4())
        assert a.has_contract is True

    def test_from_attributes_config(self):
        assert Ativo.model_config.get("from_attributes") is True

    def test_model_dump_inclui_todos_campos(self):
        a = make_ativo()
        d = a.model_dump()
        for campo in (
            "id",
            "tipo",
            "nome",
            "ambiente",
            "criticidade",
            "owner",
            "tags",
            "metadata",
            "contrato_id",
            "created_at",
            "updated_at",
            "deleted_at",
        ):
            assert campo in d


# ── AtivoFilters ──────────────────────────────────────────────────────────────


class TestAtivoFilters:
    def test_filtros_todos_none_por_padrao(self):
        f = AtivoFilters()
        assert f.tipo is None
        assert f.ambiente is None
        assert f.criticidade is None
        assert f.owner is None
        assert f.tag is None
        assert f.include_deleted is False

    def test_filtro_tipo_valido(self):
        f = AtivoFilters(tipo="app")
        assert f.tipo == "app"

    def test_filtro_tipo_invalido(self):
        with pytest.raises(ValidationError):
            AtivoFilters(tipo="mainframe")

    def test_filtro_ambiente_invalido(self):
        with pytest.raises(ValidationError):
            AtivoFilters(ambiente="qa")

    def test_filtro_criticidade_invalida(self):
        with pytest.raises(ValidationError):
            AtivoFilters(criticidade="moderada")

    def test_filtro_include_deleted(self):
        f = AtivoFilters(include_deleted=True)
        assert f.include_deleted is True

    def test_filtro_tag(self):
        f = AtivoFilters(tag="monitorado")
        assert f.tag == "monitorado"

    def test_filtro_owner_valido(self):
        f = AtivoFilters(owner="admin@empresa.com")
        assert f.owner == "admin@empresa.com"

    def test_filtro_tipo_none_permanece_none(self):
        f = AtivoFilters(tipo=None)
        assert f.tipo is None

    def test_filtro_ambiente_none_permanece_none(self):
        f = AtivoFilters(ambiente=None)
        assert f.ambiente is None

    def test_filtro_criticidade_none_permanece_none(self):
        f = AtivoFilters(criticidade=None)
        assert f.criticidade is None


# ── AtivoStats ────────────────────────────────────────────────────────────────


class TestAtivoStats:
    def test_stats_basico(self):
        s = AtivoStats(
            total=10,
            por_tipo={"servidor": 5, "switch": 3, "app": 2},
            por_criticidade={"alta": 3, "media": 5, "baixa": 2},
            por_ambiente={"prod": 7, "hml": 2, "dev": 1},
            sem_owner=1,
            sem_contrato=4,
        )
        assert s.total == 10
        assert s.sem_owner == 1
        assert s.sem_contrato == 4

    def test_stats_defaults_vazios(self):
        s = AtivoStats(total=0, sem_owner=0, sem_contrato=0)
        assert s.por_tipo == {}
        assert s.por_criticidade == {}
        assert s.por_ambiente == {}

    def test_total_nao_negativo(self):
        with pytest.raises(ValidationError):
            AtivoStats(total=-1, sem_owner=0, sem_contrato=0)

    def test_sem_owner_nao_negativo(self):
        with pytest.raises(ValidationError):
            AtivoStats(total=5, sem_owner=-1, sem_contrato=0)
