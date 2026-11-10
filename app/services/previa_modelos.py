"""Dados ao vivo para a prévia dos modelos de redesign (``/gov/previa/<modelo>``).

Junta o que a Visão Geral e as páginas de operação já leem (score COBIT,
Zabbix, CFTV, Zendesk, fontes) num só objeto, para os três modelos novos
serem testados com dados reais sem mexer nas telas atuais.

Cada fonte é lida à parte: se uma falhar, a prévia mostra o resto e marca
aquela parte como indisponível.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, TypeVar
from zoneinfo import ZoneInfo

import requests
import structlog
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError

from itgov.utils.cache_swr import CacheSWR

log = structlog.get_logger(__name__)

_TZ = ZoneInfo("America/Sao_Paulo")
T = TypeVar("T")

MODELOS: dict[str, str] = {
    "a": "Console de observabilidade",
    "b": "Caixa de triagem",
    "c": "Página de status",
}
LIMITE_CRITICO = 60.0
FAIXAS_IDADE: tuple[tuple[str, int], ...] = (
    ("< 1 h", 3600),
    ("1–6 h", 6 * 3600),
    ("6–24 h", 24 * 3600),
    ("1–3 d", 3 * 86400),
    ("3–7 d", 7 * 86400),
    ("> 7 d", 10**12),
)
PRIORIDADES: tuple[tuple[str, str], ...] = (
    ("urgent", "Urgente"),
    ("high", "Alta"),
    ("normal", "Normal"),
    ("low", "Baixa"),
)


class Pilar(BaseModel):
    """Pilar COBIT com quanto ele tira do score ponderado."""

    nome: str
    score: float
    peso: float
    critico: bool
    perda: float


class Problema(BaseModel):
    """Problema ativo do Zabbix."""

    host: str
    nome: str
    gravidade: str  # "alta", "media" ou "atencao"
    idade_s: int
    idade: str
    reconhecido: bool


class FaixaIdade(BaseModel):
    """Quantos problemas estão abertos há esse tempo, por gravidade."""

    rotulo: str
    alta: int = 0
    media: int = 0
    atencao: int = 0

    @property
    def total(self) -> int:
        """Soma das três gravidades."""
        return self.alta + self.media + self.atencao


class HostMapa(BaseModel):
    """Um host no mapa: nome e pior gravidade aberta ("" = sem problema)."""

    nome: str
    estado: str


class GrupoMapa(BaseModel):
    """Um tipo de equipamento no mapa de hosts."""

    tipo: str
    hosts: list[HostMapa]

    @property
    def com_problema(self) -> int:
        """Hosts do grupo com algum problema aberto."""
        return sum(1 for h in self.hosts if h.estado)


class Chamado(BaseModel):
    """Chamado aberto do Zendesk (dos mais antigos)."""

    id: int
    assunto: str
    prioridade: str
    idade: str
    estourado: bool


class Prioridade(BaseModel):
    """Chamados abertos de uma prioridade e quantos estouraram o SLA."""

    nome: str
    abertos: int
    fora: int


class Fonte(BaseModel):
    """Situação de uma fonte de dados."""

    nome: str
    estado: str
    latencia_ms: int | None = None
    detalhe: str = ""


class Servico(BaseModel):
    """Linha da página de status: uma área da operação e como ela está."""

    nome: str
    estado: str  # "ok", "degradado" ou "fora"
    resumo: str


class Incidente(BaseModel):
    """Mesmo problema em vários hosts, como uma linha só (página de status)."""

    nome: str
    gravidade: str
    hosts: list[str]
    mais_antigo: str
    mais_novo: str


class Previa(BaseModel):
    """Tudo o que os modelos mostram."""

    atualizado: str
    score: float | None = None
    status: str = ""
    pilares: list[Pilar] = []
    hosts_total: int = 0
    hosts_fora: int = 0
    disponibilidade: float | None = None
    problemas: list[Problema] = []
    gravidades: dict[str, int] = {"alta": 0, "media": 0, "atencao": 0}
    idades: list[FaixaIdade] = []
    mapa: list[GrupoMapa] = []
    cameras_on: int = 0
    cameras_total: int = 0
    gravadores_on: int = 0
    gravadores_total: int = 0
    chamados_abertos: int = 0
    chamados_fora: int = 0
    prioridades: list[Prioridade] = []
    antigos: list[Chamado] = []
    fontes: list[Fonte] = []
    servicos: list[Servico] = []
    indisponivel: list[str] = []

    @property
    def nao_reconhecidos(self) -> int:
        """Problemas ainda sem reconhecimento."""
        return sum(1 for p in self.problemas if not p.reconhecido)

    @property
    def mais_de_7_dias(self) -> int:
        """Problemas abertos há mais de 7 dias."""
        return self.idades[-1].total if self.idades else 0

    @property
    def incidentes(self) -> list[Incidente]:
        """Problemas agrupados por nome e gravidade, os maiores primeiro."""
        return agrupar_incidentes(self.problemas)

    @property
    def estado_geral(self) -> str:
        """Pior estado entre as áreas (``ok``, ``degradado`` ou ``fora``)."""
        estados = {s.estado for s in self.servicos}
        return "fora" if "fora" in estados else "degradado" if "degradado" in estados else "ok"


def gravidade(severidade: int) -> str:
    """Severidade do Zabbix (0–5) → ``alta`` (4–5), ``media`` (3) ou ``atencao`` (0–2)."""
    return "alta" if severidade >= 4 else "media" if severidade == 3 else "atencao"


def idade_texto(segundos: int) -> str:
    """``95`` → ``"1 min"``; ``7200`` → ``"2 h"``; ``129600`` → ``"1 d 12 h"``."""
    if segundos < 3600:
        return f"{max(segundos // 60, 1)} min"
    if segundos < 86400:
        return f"{segundos // 3600} h"
    dias, resto = divmod(segundos, 86400)
    horas = resto // 3600
    return f"{dias} d {horas} h" if horas and dias < 7 else f"{dias} d"


def montar_problemas(brutos: list[dict[str, Any]], agora: datetime) -> tuple[list[Problema], list[FaixaIdade]]:
    """Problemas do Zabbix (``get_cached_problems``) → lista ordenada e faixas de idade.

    Ordem: gravidade (alta primeiro) e, dentro dela, os mais novos primeiro.

    Args:
        brutos: Itens de ``get_cached_problems`` (com ``clock``).
        agora: Horário de referência (UTC).

    Returns:
        ``(problemas, faixas de idade)``.
    """
    ordem = {"alta": 0, "media": 1, "atencao": 2}
    faixas = [FaixaIdade(rotulo=r) for r, _ in FAIXAS_IDADE]
    problemas: list[Problema] = []
    for p in brutos:
        clock = int(p.get("clock") or 0)
        idade = max(int(agora.timestamp()) - clock, 0) if clock else 0
        grav = gravidade(int(p.get("severity") or 0))
        problemas.append(
            Problema(
                host=", ".join(p.get("hosts") or []) or "—",
                nome=str(p.get("name") or ""),
                gravidade=grav,
                idade_s=idade,
                idade=idade_texto(idade) if clock else "—",
                reconhecido=bool(p.get("acknowledged")),
            )
        )
        if clock:  # sem horário não dá para saber a idade
            faixa = next(f for f, (_, limite) in zip(faixas, FAIXAS_IDADE, strict=True) if idade < limite)
            setattr(faixa, grav, getattr(faixa, grav) + 1)
    problemas.sort(key=lambda x: (ordem[x.gravidade], x.idade_s))
    return problemas, faixas


def agrupar_incidentes(problemas: list[Problema]) -> list[Incidente]:
    """Junta os problemas de mesmo nome e gravidade (ex.: ICMP fora em 51 câmeras).

    Args:
        problemas: Problemas já montados.

    Returns:
        Incidentes por gravidade (alta primeiro) e, dentro dela, pelo número de hosts.
    """
    ordem = {"alta": 0, "media": 1, "atencao": 2}
    grupos: dict[tuple[str, str], list[Problema]] = {}
    for p in problemas:
        grupos.setdefault((p.nome, p.gravidade), []).append(p)
    saida = [
        Incidente(
            nome=nome,
            gravidade=grav,
            hosts=[p.host for p in ps],
            mais_antigo=max(ps, key=lambda p: p.idade_s).idade,
            mais_novo=min(ps, key=lambda p: p.idade_s).idade,
        )
        for (nome, grav), ps in grupos.items()
    ]
    return sorted(saida, key=lambda i: (ordem[i.gravidade], -len(i.hosts)))


def montar_pilares(pilares: list[dict[str, Any]]) -> list[Pilar]:
    """Pilares do score, com a perda de cada um: ``peso × (100 − score)``, do que mais tira ao que menos tira."""
    saida = [
        Pilar(
            nome=str(p.get("label") or p.get("id") or ""),
            score=float(p.get("score") or 0),
            peso=float(p.get("weight") or 0),
            critico=float(p.get("score") or 0) < LIMITE_CRITICO,
            perda=round(float(p.get("weight") or 0) * (100 - float(p.get("score") or 0)), 1),
        )
        for p in pilares
    ]
    return sorted(saida, key=lambda p: -p.perda)


def montar_mapa(hosts: list[dict[str, Any]], problemas: list[Problema]) -> list[GrupoMapa]:
    """Hosts habilitados por tipo, cada um com a pior gravidade aberta nele.

    Args:
        hosts: ``host.get`` com ``name`` e ``selectHostGroups``.
        problemas: Problemas já montados (o host vem pelo nome visível).

    Returns:
        Grupos do maior para o menor, hosts com problema primeiro.
    """
    from itgov.api.v1.zabbix_monitoring import hosts_por_tipo

    ordem = {"alta": 0, "media": 1, "atencao": 2, "": 3}
    pior: dict[str, str] = {}
    for p in problemas:
        for nome in p.host.split(", "):
            if ordem[p.gravidade] < ordem[pior.get(nome, "")]:
                pior[nome] = p.gravidade
    grupos: dict[str, list[HostMapa]] = {}
    for h in hosts:
        tipo = next(iter(hosts_por_tipo([h])))
        nome = str(h.get("name") or h.get("host") or "")
        grupos.setdefault(tipo, []).append(HostMapa(nome=nome, estado=pior.get(nome, "")))
    return [
        GrupoMapa(tipo=t, hosts=sorted(hs, key=lambda x: (ordem[x.estado], x.nome)))
        for t, hs in sorted(grupos.items(), key=lambda kv: -len(kv[1]))
    ]


def montar_servicos(previa: Previa) -> list[Servico]:
    """Áreas da operação com estado e uma frase, para a página de status."""

    def estado(fora: int, total: int, limite: float = 0.1) -> str:
        if not total:
            return "ok"
        return "fora" if fora / total >= limite * 3 else "degradado" if fora else "ok"

    servicos = []
    if previa.hosts_total:
        servicos.append(
            Servico(
                nome="Infraestrutura monitorada",
                estado=estado(previa.hosts_fora, previa.hosts_total),
                resumo=f"{previa.hosts_fora} de {previa.hosts_total} hosts sem resposta",
            )
        )
    if previa.cameras_total:
        fora = previa.cameras_total - previa.cameras_on
        servicos.append(
            Servico(
                nome="CFTV · câmeras",
                estado=estado(fora, previa.cameras_total),
                resumo=f"{previa.cameras_on} de {previa.cameras_total} online",
            )
        )
    if previa.gravadores_total:
        fora = previa.gravadores_total - previa.gravadores_on
        servicos.append(
            Servico(
                nome="CFTV · gravadores",
                estado=estado(fora, previa.gravadores_total),
                resumo=f"{previa.gravadores_on} de {previa.gravadores_total} online",
            )
        )
    if previa.chamados_abertos:
        servicos.append(
            Servico(
                nome="Service desk (SLA)",
                estado=estado(previa.chamados_fora, previa.chamados_abertos),
                resumo=f"{previa.chamados_fora} de {previa.chamados_abertos} chamados abertos fora do SLA",
            )
        )
    for f in previa.fontes:
        if f.estado == "nao_configurada":
            continue
        servicos.append(
            Servico(
                nome=f"Integração · {f.nome}",
                estado="ok" if f.estado == "online" else "fora",
                resumo=("respondendo" + (f" em {f.latencia_ms} ms" if f.latencia_ms else ""))
                if f.estado == "online"
                else (f.detalhe or "sem resposta"),
            )
        )
    return servicos


def _ler(nome: str, previa: Previa, ler: Callable[[], T], padrao: T) -> T:  # noqa: UP047 — CI também roda em 3.11
    try:
        return ler()
    except (requests.RequestException, RuntimeError, KeyError, ValueError, TypeError, SQLAlchemyError) as exc:
        log.warning("previa_modelos.fonte_falhou", fonte=nome, erro=f"{type(exc).__name__}: {exc}"[:200])
        previa.indisponivel.append(nome)
        return padrao


def _buscar_hosts() -> list[dict[str, Any]] | None:
    from itgov.api.v1.cftv_monitoring import _zbx

    try:
        return _zbx("host.get", {"output": ["host", "name"], "selectHostGroups": ["name"], "filter": {"status": 0}})
    except (requests.RequestException, RuntimeError, KeyError, ValueError) as exc:
        log.warning("previa_modelos.hosts_falhou", erro=f"{type(exc).__name__}: {exc}"[:200])
        return None


# Os outros dados já têm cache; a lista de hosts do mapa é a única consulta nova
_cache_hosts: CacheSWR[list[dict[str, Any]] | None] = CacheSWR(
    "previa.hosts", ttl=300, valido=lambda v: v is not None, ttl_falha=60
)


def _hosts_zabbix() -> list[dict[str, Any]]:
    hosts = _cache_hosts.get(_buscar_hosts)
    if hosts is None:
        raise RuntimeError("Zabbix não respondeu à lista de hosts")
    return hosts


def _montar(agora: datetime | None = None) -> Previa:
    """Lê todas as fontes (requer app context)."""
    from app.services import fontes_status
    from app.views.dashboards import _get_governance
    from itgov.api.v1.cftv_monitoring import get_cached_cftv_summary
    from itgov.api.v1.zabbix_monitoring import get_cached_problems, get_cached_zabbix_summary

    agora = agora or datetime.now(UTC)
    previa = Previa(atualizado=agora.astimezone(_TZ).strftime("%d/%m %H:%M"))

    gov = _ler("score", previa, _get_governance, {})
    if gov:
        previa.score = round(float(gov.get("global_score") or 0), 1)
        previa.status = str(gov.get("status") or "").capitalize()
        previa.pilares = montar_pilares(gov.get("pillars") or [])

    zbx = _ler("zabbix", previa, get_cached_zabbix_summary, {})
    previa.hosts_total = int(zbx.get("total_monitorado") or 0)
    previa.hosts_fora = int(zbx.get("hosts_down") or 0)
    previa.disponibilidade = zbx.get("uptime_pct")

    previa.problemas, previa.idades = montar_problemas(_ler("problemas", previa, get_cached_problems, []), agora)
    for p in previa.problemas:
        previa.gravidades[p.gravidade] += 1
    previa.mapa = montar_mapa(_ler("mapa de hosts", previa, _hosts_zabbix, []), previa.problemas)

    cftv = _ler("cftv", previa, get_cached_cftv_summary, {})
    for d in cftv.get("devices", []):
        if d.get("is_gravador"):
            previa.gravadores_total += 1
            previa.gravadores_on += d.get("status") == "up"
        else:
            previa.cameras_total += 1
            previa.cameras_on += d.get("status") == "up"

    sla = _ler("zendesk", previa, _sla, {})
    por = sla.get("by_priority") or {}
    previa.prioridades = [
        Prioridade(nome=rotulo, abertos=int(por[k].get("count") or 0), fora=int(por[k].get("breached") or 0))
        for k, rotulo in PRIORIDADES
        if k in por
    ]
    previa.chamados_abertos = int(sla.get("total_open") or 0)
    previa.chamados_fora = sum(p.fora for p in previa.prioridades)
    nomes_pri = dict(PRIORIDADES)
    previa.antigos = [
        Chamado(
            id=int(c["id"]),
            assunto=str(c.get("subject") or "").strip(),
            prioridade=nomes_pri.get(str(c.get("priority")), str(c.get("priority") or "—")),
            idade=str(c.get("age_str") or ""),
            estourado=bool(c.get("breached")),
        )
        for c in (sla.get("oldest") or [])[:8]
    ]

    previa.fontes = [
        Fonte(nome=f.nome, estado=f.estado, latencia_ms=f.latencia_ms, detalhe=f.detalhe)
        for f in _ler("fontes", previa, fontes_status.status_fontes, [])
    ]
    previa.servicos = montar_servicos(previa)
    return previa


def _sla() -> dict[str, Any]:
    from app.integrations import zendesk_configured
    from itgov.api.v1.zendesk import get_cached_sla_detail

    return get_cached_sla_detail() if zendesk_configured() else {}


def obter() -> Previa:
    """Dados da prévia (requer app context).

    Sem cache próprio: cada fonte já tem o seu (score, Zabbix, CFTV, Zendesk
    e fontes, de 2 a 5 min); só o mapa de hosts consulta o Zabbix na hora.
    """
    return _montar()
