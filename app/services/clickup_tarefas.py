"""Tarefas do ClickUp por responsável (aba ClickUp de ``/gov/tarefas``).

Busca as tarefas do workspace inteiro (todas as listas que o dono do token
enxerga), não só a lista de projetos da PMO. Cada usuário do dashboard vê as
tarefas atribuídas ao próprio e-mail; o admin vê todas, separadas por pessoa.

A busca completa leva ~13 s em série (≈1.000 tarefas, 100 por página); as
páginas são pedidas em lotes paralelos e o resultado fica em cache com
atualização em segundo plano, para a página nunca esperar pelo ClickUp.
"""

from __future__ import annotations

import asyncio
import os
from collections import defaultdict
from datetime import UTC, date, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

import httpx
import structlog
from pydantic import BaseModel

from itgov.utils.cache_swr import CacheSWR

log = structlog.get_logger(__name__)

API = "https://api.clickup.com/api/v2"
PRAZO_S = 20.0
PAGINAS_POR_LOTE = 5
LIMITE_PAGINAS = 50  # trava de segurança: 5.000 tarefas
_TZ = ZoneInfo("America/Sao_Paulo")

Situacao = Literal["a_fazer", "andamento", "concluida"]

_PRIORIDADES = {"1": "Urgente", "2": "Alta", "3": "Normal", "4": "Baixa"}


class Responsavel(BaseModel):
    """Pessoa atribuída a uma tarefa no ClickUp."""

    nome: str
    email: str


class TarefaClickUp(BaseModel):
    """Tarefa do ClickUp no formato da página.

    Attributes:
        situacao: ``a_fazer`` (tipo open), ``concluida`` (closed/done) ou
            ``andamento`` (status personalizados do meio do fluxo).
        status: Nome do status como aparece no ClickUp.
        local: Espaço/pasta/lista, para achar a tarefa no ClickUp.
        vencimento: Data de entrega no fuso de Brasília.
        atrasada: Vencida e ainda não concluída.
    """

    id: str
    nome: str
    situacao: Situacao
    status: str
    local: str
    prioridade: str | None
    vencimento: date | None
    concluida_em: date | None
    atrasada: bool
    url: str
    responsaveis: list[Responsavel]


class GrupoResponsavel(BaseModel):
    """Tarefas de uma pessoa, com os totais do cabeçalho."""

    nome: str
    email: str
    abertas: list[TarefaClickUp]
    concluidas: list[TarefaClickUp]
    total_concluidas: int

    @property
    def atrasadas(self) -> int:
        """Quantidade de tarefas abertas com vencimento passado."""
        return sum(1 for t in self.abertas if t.atrasada)


class ResultadoClickUp(BaseModel):
    """Resultado de uma busca no ClickUp.

    Attributes:
        ok: A busca terminou sem erro (lista vazia com ``ok`` é workspace vazio).
        motivo: ``sem_token`` ou ``erro`` quando ``ok`` é falso.
        sem_responsavel: Tarefas sem ninguém atribuído (não entram nos grupos).
    """

    ok: bool
    motivo: str | None = None
    tarefas: list[TarefaClickUp] = []
    sem_responsavel: int = 0
    atualizado_em: datetime


def _token() -> str:
    return os.getenv("CLICKUP_TOKEN", "")


def _workspace() -> str:
    return os.getenv("CLICKUP_WORKSPACE_ID", "9013344143")


def _data_local(ms: Any) -> date | None:
    if not ms:
        return None
    return datetime.fromtimestamp(int(ms) / 1000, tz=UTC).astimezone(_TZ).date()


def _situacao(tipo: str, nome: str) -> Situacao:
    # "cancelado" costuma ser status personalizado (tipo custom), mas encerra a tarefa.
    if tipo in ("closed", "done") or nome.lower().startswith("cancel"):
        return "concluida"
    if tipo == "open":
        return "a_fazer"
    return "andamento"


def converter(bruta: dict[str, Any], hoje: date) -> TarefaClickUp:
    """Converte uma tarefa da API do ClickUp para o formato da página.

    Args:
        bruta: Tarefa como devolvida por ``GET /team/{id}/task``.
        hoje: Data de referência para marcar atraso (fuso de Brasília).

    Returns:
        A tarefa convertida.
    """
    status = bruta.get("status") or {}
    situacao = _situacao(str(status.get("type", "")), str(status.get("status", "")))
    vencimento = _data_local(bruta.get("due_date"))
    local = " / ".join(
        n
        for n in (
            (bruta.get("folder") or {}).get("name"),
            (bruta.get("list") or {}).get("name"),
        )
        if n and n != "hidden"
    )
    prioridade = (bruta.get("priority") or {}).get("orderindex")
    return TarefaClickUp(
        id=str(bruta.get("id", "")),
        nome=str(bruta.get("name", "")).strip() or "(sem título)",
        situacao=situacao,
        status=str(status.get("status", "")),
        local=local,
        prioridade=_PRIORIDADES.get(str(prioridade)) if prioridade is not None else None,
        vencimento=vencimento,
        concluida_em=_data_local(bruta.get("date_closed") or bruta.get("date_done")),
        atrasada=situacao != "concluida" and vencimento is not None and vencimento < hoje,
        url=str(bruta.get("url", "")),
        responsaveis=[
            Responsavel(nome=a.get("username") or a.get("email") or "?", email=(a.get("email") or "").lower())
            for a in bruta.get("assignees") or []
        ],
    )


async def _pagina(cliente: httpx.AsyncClient, pagina: int) -> tuple[list[dict[str, Any]], bool]:
    resp = await cliente.get(
        f"{API}/team/{_workspace()}/task",
        params={"page": pagina, "include_closed": "true", "subtasks": "true"},
    )
    resp.raise_for_status()
    dados = resp.json()
    tarefas = dados.get("tasks") or []
    return tarefas, bool(dados.get("last_page")) or not tarefas


async def buscar_tarefas() -> list[dict[str, Any]]:
    """Busca todas as tarefas do workspace, em lotes de páginas paralelas.

    Returns:
        Tarefas cruas da API, na ordem das páginas.

    Raises:
        httpx.HTTPError: Falha de rede ou resposta de erro do ClickUp.
    """
    brutas: list[dict[str, Any]] = []
    async with httpx.AsyncClient(headers={"Authorization": _token()}, timeout=PRAZO_S) as cliente:
        inicio = 0
        while inicio < LIMITE_PAGINAS:
            lote = await asyncio.gather(*(_pagina(cliente, p) for p in range(inicio, inicio + PAGINAS_POR_LOTE)))
            fim = False
            for tarefas, ultima in lote:
                brutas.extend(tarefas)
                if ultima:
                    fim = True
                    break
            if fim:
                return brutas
            inicio += PAGINAS_POR_LOTE
    log.warning("clickup_tarefas.limite_paginas", paginas=LIMITE_PAGINAS)
    return brutas


def _carregar() -> ResultadoClickUp:
    agora = datetime.now(_TZ)
    if not _token():
        return ResultadoClickUp(ok=False, motivo="sem_token", atualizado_em=agora)
    try:
        brutas = asyncio.run(buscar_tarefas())
    except httpx.HTTPError as exc:
        log.warning("clickup_tarefas.busca_falhou", error=str(exc))
        return ResultadoClickUp(ok=False, motivo="erro", atualizado_em=agora)
    vistas: set[str] = set()
    tarefas: list[TarefaClickUp] = []
    for bruta in brutas:
        t = converter(bruta, agora.date())
        if t.id not in vistas:  # paginação pode repetir tarefa alterada no meio da busca
            vistas.add(t.id)
            tarefas.append(t)
    log.info("clickup_tarefas.carregado", total=len(tarefas))
    return ResultadoClickUp(
        ok=True,
        tarefas=tarefas,
        sem_responsavel=sum(1 for t in tarefas if not t.responsaveis),
        atualizado_em=agora,
    )


_cache: CacheSWR[ResultadoClickUp] = CacheSWR("clickup.tarefas", ttl=300, valido=lambda r: r.ok)


def obter() -> ResultadoClickUp:
    """Tarefas do workspace com cache de 5 min atualizado em segundo plano."""
    return _cache.get(_carregar)


def aquecer() -> None:
    """Faz a primeira busca em segundo plano (subida do worker)."""
    _cache.aquecer(_carregar)


def _ordem_aberta(t: TarefaClickUp) -> tuple[bool, date, str]:
    # Atrasadas primeiro, depois por vencimento (sem data por último).
    return (not t.atrasada, t.vencimento or date.max, t.nome.lower())


def _grupo(nome: str, chave: str, lista: list[TarefaClickUp], concluidas_max: int) -> GrupoResponsavel:
    fechadas = sorted(
        (t for t in lista if t.situacao == "concluida"),
        key=lambda t: t.concluida_em or date.min,
        reverse=True,
    )
    return GrupoResponsavel(
        nome=nome,
        email=chave if "@" in chave else "",
        abertas=sorted((t for t in lista if t.situacao != "concluida"), key=_ordem_aberta),
        concluidas=fechadas[:concluidas_max],
        total_concluidas=len(fechadas),
    )


def agrupar_por_responsavel(tarefas: list[TarefaClickUp], concluidas_max: int = 10) -> list[GrupoResponsavel]:
    """Separa as tarefas por pessoa; tarefa com 2 responsáveis entra nos 2 grupos.

    Args:
        tarefas: Tarefas convertidas.
        concluidas_max: Quantas concluídas (as mais recentes) manter por pessoa.

    Returns:
        Grupos ordenados por atrasadas, abertas e nome.
    """
    por_email: dict[str, list[TarefaClickUp]] = defaultdict(list)
    nomes: dict[str, str] = {}
    for t in tarefas:
        for r in t.responsaveis:
            chave = r.email or r.nome.lower()
            por_email[chave].append(t)
            nomes.setdefault(chave, r.nome)
    grupos = [_grupo(nomes[chave], chave, lista, concluidas_max) for chave, lista in por_email.items()]
    grupos.sort(key=lambda g: (-g.atrasadas, -len(g.abertas), g.nome.lower()))
    return grupos
