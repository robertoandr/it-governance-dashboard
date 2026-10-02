"""Histórico do grupo do Zendesk: chamados por mês e volume por usuário.

Conferência V2.0 (itens z3, z4 e sl1): as páginas Zendesk e SLA mostravam só
a janela de 30 dias, e o "Total histórico" era abertos + resolvidos em 30 dias.
O escopo continua sendo só o grupo configurado (TI / Infra, decisão do usuário
em 02/10/2026), então o histórico começa na criação do grupo.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta

from pydantic import BaseModel

from itgov.models.zendesk import Ticket, TicketStatus

_EM_ABERTO = {TicketStatus.NEW, TicketStatus.OPEN, TicketStatus.PENDING, TicketStatus.HOLD}
_RESOLVIDO = {TicketStatus.SOLVED, TicketStatus.CLOSED}
SEM_RESPONSAVEL = "Sem responsável"
MAX_SOLICITANTES = 15


class Mes(BaseModel):
    """Chamados de um mês: abertos (criados) e resolvidos."""

    mes: str  # "2026-09"
    rotulo: str  # "09/2026"
    abertos: int
    resolvidos: int


class VolumeUsuario(BaseModel):
    """Volume de chamados de uma pessoa (responsável ou solicitante)."""

    nome: str
    total: int
    em_aberto: int
    resolvidos: int
    ultimos_30d: int


class HistoricoZendesk(BaseModel):
    """Tudo o que as páginas Zendesk e SLA mostram sobre o histórico."""

    grupo: str
    desde: date | None
    total: int
    media_mensal: float | None
    meses: list[Mes]
    por_responsavel: list[VolumeUsuario]
    por_solicitante: list[VolumeUsuario]


def meses_desde(inicio: date, hoje: date) -> list[tuple[date, date]]:
    """Intervalos [1º dia, 1º dia do mês seguinte) de ``inicio`` até ``hoje``."""
    out: list[tuple[date, date]] = []
    atual = inicio.replace(day=1)
    while atual <= hoje:
        prox = (atual.replace(day=28) + timedelta(days=4)).replace(day=1)
        out.append((atual, prox))
        atual = prox
    return out


def _volumes(tickets: list[Ticket], chave: dict[int, str], hoje: date) -> list[VolumeUsuario]:
    corte = hoje - timedelta(days=30)
    acc: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0])
    for t, nome in ((t, chave[t.id]) for t in tickets):
        v = acc[nome]
        v[0] += 1
        v[1] += t.status in _EM_ABERTO
        v[2] += t.status in _RESOLVIDO
        v[3] += t.created_at.date() >= corte
    lista = [
        VolumeUsuario(nome=n, total=a, em_aberto=b, resolvidos=c, ultimos_30d=d) for n, (a, b, c, d) in acc.items()
    ]
    lista.sort(key=lambda v: (-v.total, v.nome))
    return lista


def resumir(
    tickets: list[Ticket],
    nomes: dict[int, str],
    resolvidos_por_mes: dict[str, int],
    grupo: str,
    desde: date | None,
    hoje: date,
) -> HistoricoZendesk:
    """Monta o histórico a partir dos tickets do grupo.

    Args:
        tickets: Todos os tickets do grupo (qualquer status).
        nomes: Id do usuário -> nome.
        resolvidos_por_mes: ``"AAAA-MM"`` -> resolvidos naquele mês (Search API).
        grupo: Nome do grupo.
        desde: Criação do grupo (ou o ticket mais antigo, se desconhecida).
        hoje: Data de referência.

    Returns:
        Histórico com meses, médias e volumes por usuário.
    """
    if desde is None and tickets:
        desde = min(t.created_at.date() for t in tickets)
    criados: dict[str, int] = defaultdict(int)
    for t in tickets:
        criados[t.created_at.strftime("%Y-%m")] += 1
    meses = [
        Mes(
            mes=ini.strftime("%Y-%m"),
            rotulo=ini.strftime("%m/%Y"),
            abertos=criados.get(ini.strftime("%Y-%m"), 0),
            resolvidos=resolvidos_por_mes.get(ini.strftime("%Y-%m"), 0),
        )
        for ini, _ in (meses_desde(desde, hoje) if desde else [])
    ]
    # Média só com meses completos: o mês corrente e o da criação são parciais.
    completos = meses[1:-1]
    media = round(sum(m.abertos for m in completos) / len(completos), 1) if completos else None

    responsavel = {
        t.id: nomes.get(t.assignee_id, SEM_RESPONSAVEL) if t.assignee_id else SEM_RESPONSAVEL for t in tickets
    }
    solicitante = {t.id: nomes.get(t.requester_id, f"Usuário {t.requester_id}") for t in tickets}
    return HistoricoZendesk(
        grupo=grupo,
        desde=desde,
        total=len(tickets),
        media_mensal=media,
        meses=meses,
        por_responsavel=_volumes(tickets, responsavel, hoje),
        por_solicitante=_volumes(tickets, solicitante, hoje)[:MAX_SOLICITANTES],
    )


def data_criacao(grupo: dict | None) -> date | None:
    """Data de criação do grupo a partir do JSON da API."""
    bruto = (grupo or {}).get("created_at")
    if not bruto:
        return None
    try:
        return datetime.fromisoformat(str(bruto).replace("Z", "+00:00")).date()
    except ValueError:
        return None
