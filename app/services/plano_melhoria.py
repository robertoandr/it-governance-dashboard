"""Plano de melhoria de cada pilar: o que fazer para subir o score.

Para cada componente com dado real abaixo da meta, calcula quantos pontos o
pilar ganha se o componente chegar à meta (mesma média ponderada do
``ScoreCalculator``), diz o que fazer e onde agir e, quando há dado concreto,
lista os itens a atacar (controles do Secure Score, problemas do Zabbix,
chamados fora do SLA, projetos atrasados). As ações saem ordenadas pelo ganho.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field

import structlog
from pydantic import BaseModel

log = structlog.get_logger(__name__)

MAX_ITENS = 5
_COMING_SOON = "coming_soon"

ENTRA_CA_URL = "https://entra.microsoft.com/#view/Microsoft_AAD_ConditionalAccess/ConditionalAccessBlade/~/Policies"


class ItemConcreto(BaseModel):
    """Um item específico a atacar (controle, problema, chamado, tarefa)."""

    titulo: str
    detalhe: str = ""
    url: str | None = None


class AcaoMelhoria(BaseModel):
    """Ação recomendada para um componente abaixo da meta.

    Attributes:
        componente_id: Id do componente no score.
        componente: Nome exibido do componente.
        valor: Valor atual (0–100) usado no score.
        meta: Valor-alvo (0–100).
        ganho: Pontos que o pilar ganha se o componente chegar à meta.
        o_que_fazer: Orientação em uma ou duas frases.
        onde_label: Texto do link "onde agir".
        onde_endpoint: Endpoint Flask interno (``dashboards.x``), se houver.
        onde_url: URL externa (portal), se não houver página interna.
        itens: Até ``MAX_ITENS`` itens concretos, quando a fonte permite.
    """

    componente_id: str
    componente: str
    valor: float
    meta: float
    ganho: float
    o_que_fazer: str
    onde_label: str
    onde_endpoint: str | None = None
    onde_url: str | None = None
    itens: list[ItemConcreto] = []


@dataclass(frozen=True)
class Regra:
    """Meta e orientação de um componente."""

    meta: float
    o_que_fazer: str
    onde_label: str
    onde_endpoint: str | None = None
    onde_url: str | None = None
    itens: Callable[[], list[ItemConcreto]] | None = field(default=None, compare=False)


# ── itens concretos (cada um protegido: falha da fonte = sem itens) ─────────


def _controles_secure_score() -> list[ItemConcreto]:
    from itgov.api.v1.governance_compliance import get_cached_compliance_summary

    pendentes = [c for c in get_cached_compliance_summary().get("controles", []) if c.get("status") == "pendente"]
    pendentes.sort(key=lambda c: float(c.get("max_score") or 0) - float(c.get("score") or 0), reverse=True)
    return [
        ItemConcreto(
            titulo=c.get("title") or c.get("control_name", ""),
            detalhe=f"+{float(c.get('max_score') or 0) - float(c.get('score') or 0):.1f} pts no Secure Score · {c.get('categoria', '')}",
            url=c.get("action_url"),
        )
        for c in pendentes[:MAX_ITENS]
    ]


def _problemas_zabbix(sev_min: int = 0, so_indisponivel: bool = False) -> Callable[[], list[ItemConcreto]]:
    def _itens() -> list[ItemConcreto]:
        from itgov.api.v1.zabbix_triggers import get_cached_triggers

        probs = [p for p in get_cached_triggers().get("problems", []) if int(p.get("severity") or 0) >= sev_min]
        if so_indisponivel:
            probs = [p for p in probs if "unavailable" in (p.get("name") or "").lower()]
        # Mais graves primeiro; entre iguais, os mais antigos (since_iso crescente).
        probs.sort(key=lambda p: (-int(p.get("severity") or 0), p.get("since_iso") or ""))
        return [
            ItemConcreto(
                titulo=p.get("host") or "",
                detalhe=f"{p.get('severity_label', '')} · {p.get('name', '')} · desde {p.get('since', '')}",
                url=p.get("zabbix_url"),
            )
            for p in probs[:MAX_ITENS]
        ]

    return _itens


def _chamados_fora_do_sla() -> list[ItemConcreto]:
    from itgov.api.v1.zendesk import get_cached_sla_detail

    sub = os.getenv("ZENDESK_SUBDOMAIN", "")
    estourados = [t for t in get_cached_sla_detail().get("oldest", []) if t.get("breached")]
    return [
        ItemConcreto(
            titulo=f"#{t['id']} {t.get('subject', '')}",
            detalhe=f"aberto há {t.get('age_str', '')} · prioridade {t.get('priority', '')}",
            url=f"https://{sub}.zendesk.com/agent/tickets/{t['id']}" if sub else None,
        )
        for t in estourados[:MAX_ITENS]
    ]


def _projetos_atrasados() -> list[ItemConcreto]:
    from itgov.api.v1.pmo_clickup import get_cached_pmo

    atrasados = [t for t in get_cached_pmo().get("ativos", []) if t.get("overdue")]
    return [
        ItemConcreto(
            titulo=t.get("name", ""),
            detalhe=f"prazo {t.get('due_date') or '—'} · {', '.join(t.get('assignees') or []) or 'sem responsável'}",
            url=t.get("url"),
        )
        for t in atrasados[:MAX_ITENS]
    ]


# ── regras por componente ───────────────────────────────────────────────────

REGRAS: dict[str, Regra] = {
    # Alinhamento estratégico
    "security_posture": Regra(
        80,
        "Implementar os controles pendentes do Microsoft Secure Score, começando pelos que valem mais pontos.",
        "Compliance M365",
        onde_endpoint="dashboards.governance_compliance",
        itens=_controles_secure_score,
    ),
    "privileged_access": Regra(
        100,
        "Reduzir as funções administrativas permanentes ao mínimo necessário e usar acesso just-in-time (PIM) para o resto.",
        "Governança M365",
        onde_endpoint="dashboards.m365_overview",
    ),
    "pmo_alignment": Regra(
        80,
        "Concluir ou replanejar os projetos atrasados no ClickUp, atualizando prazo e percentual de conclusão.",
        "PMO",
        onde_endpoint="dashboards.pmo_dashboard",
        itens=_projetos_atrasados,
    ),
    # Entrega de valor
    "sla_compliance": Regra(
        99,
        "Atacar os hosts com mais indisponibilidade no Zabbix e as causas que se repetem.",
        "Triggers",
        onde_endpoint="dashboards.zabbix_triggers",
        itens=_problemas_zabbix(sev_min=4),
    ),
    "ticket_resolution_rate": Regra(
        90,
        "Priorizar os chamados abertos que já estouraram o SLA e revisar a distribuição da fila.",
        "SLA de chamados",
        onde_endpoint="dashboards.sla_chamados",
        itens=_chamados_fora_do_sla,
    ),
    "user_satisfaction": Regra(
        90,
        "Ler as avaliações negativas do CSAT e dar retorno a quem avaliou.",
        "Zendesk",
        onde_endpoint="dashboards.zendesk_mttr",
    ),
    # Gestão de riscos
    "incidents_critical": Regra(
        100,
        "Resolver os problemas de severidade Alta/Desastre e eliminar os que se repetem (ex.: câmeras e links caindo).",
        "Triggers",
        onde_endpoint="dashboards.zabbix_triggers",
        itens=_problemas_zabbix(sev_min=4),
    ),
    "mfa_adoption": Regra(
        100,
        "Exigir MFA de todos os usuários (Acesso Condicional) e cobrar o cadastro de quem ainda não registrou.",
        "Governança M365",
        onde_endpoint="dashboards.m365_overview",
    ),
    "risk_score_zabbix": Regra(
        80,
        "Reduzir os problemas ativos no Zabbix: corrigir a causa ou ajustar triggers que disparam sem necessidade.",
        "Triggers",
        onde_endpoint="dashboards.zabbix_triggers",
        itens=_problemas_zabbix(),
    ),
    "backup_coverage": Regra(
        100,
        "Incluir no Acronis as máquinas que ainda não têm plano de proteção.",
        "Cibersegurança",
        onde_endpoint="dashboards.acronis_backup",
    ),
    # Gestão de recursos
    "identity_hygiene": Regra(
        95,
        "Desativar contas sem login há mais de 90 dias e revisar as contas de serviço.",
        "Governança M365",
        onde_endpoint="dashboards.m365_overview",
    ),
    "conditional_access": Regra(
        100,
        "Criar as políticas básicas de Acesso Condicional: MFA para todos, bloqueio de autenticação legada e proteção de administradores.",
        "Entra ID (Acesso Condicional)",
        onde_url=ENTRA_CA_URL,
    ),
    "m365_controls": Regra(
        80,
        "Implementar os controles pendentes do Secure Score com mais pontos.",
        "Compliance M365",
        onde_endpoint="dashboards.governance_compliance",
        itens=_controles_secure_score,
    ),
    "license_utilization": Regra(
        85,
        "Recolher licenças sem uso ou de usuários inativos e reduzir a quantidade contratada na renovação.",
        "Licenças",
        onde_endpoint="dashboards.m365_licenses",
    ),
    "mailbox_hygiene": Regra(
        100,
        "Concluir a exclusão das caixas de correio pendentes há mais de 7 dias.",
        "Governança M365",
        onde_endpoint="dashboards.m365_overview",
    ),
    # Medição de desempenho
    "availability": Regra(
        99,
        "Restabelecer os hosts fora do ar e tratar os que caem com frequência.",
        "Triggers",
        onde_endpoint="dashboards.zabbix_triggers",
        itens=_problemas_zabbix(so_indisponivel=True),
    ),
    "mttr": Regra(
        100,
        "Fechar primeiro os chamados mais antigos e os parados aguardando a TI — a meta é resolver em até 24 h em média.",
        "SLA de chamados",
        onde_endpoint="dashboards.sla_chamados",
        itens=_chamados_fora_do_sla,
    ),
    "monitoring_coverage": Regra(
        100,
        "Cadastrar no Zabbix os ativos que ainda não são monitorados.",
        "Infraestrutura",
        onde_endpoint="dashboards.infra_monitoring",
    ),
}


def _itens_seguros(regra: Regra, componente_id: str) -> list[ItemConcreto]:
    if regra.itens is None:
        return []
    try:
        return regra.itens()
    except Exception as exc:
        log.warning("plano_melhoria.itens_indisponiveis", componente=componente_id, error=type(exc).__name__)
        return []


def montar_plano(pilar: dict, com_itens: bool = True) -> list[AcaoMelhoria]:
    """Ações para o pilar subir o score, da que mais rende para a que menos rende.

    Args:
        pilar: ``PillarScore`` serializado (com ``components``).
        com_itens: Buscar itens concretos nas fontes (a lista de pilares não precisa).

    Returns:
        Uma ação por componente real abaixo da meta, ordenadas por ganho.
    """
    reais = [c for c in pilar.get("components", []) if c.get("source") != _COMING_SOON]
    peso_total = sum(float(c.get("weight") or 0) for c in reais)
    if not peso_total:
        return []

    acoes: list[AcaoMelhoria] = []
    for c in reais:
        regra = REGRAS.get(c["id"])
        valor = float(c.get("value") or 0)
        if regra is None or valor >= regra.meta:
            continue
        ganho = float(c.get("weight") or 0) * (regra.meta - valor) / peso_total
        acoes.append(
            AcaoMelhoria(
                componente_id=c["id"],
                componente=c.get("label", c["id"]),
                valor=round(valor, 1),
                meta=regra.meta,
                ganho=round(ganho, 1),
                o_que_fazer=regra.o_que_fazer,
                onde_label=regra.onde_label,
                onde_endpoint=regra.onde_endpoint,
                onde_url=regra.onde_url,
                itens=_itens_seguros(regra, c["id"]) if com_itens else [],
            )
        )
    acoes.sort(key=lambda a: a.ganho, reverse=True)
    return acoes
