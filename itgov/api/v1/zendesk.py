"""Endpoints Flask-RESTX para integração Zendesk (read-only)."""

from __future__ import annotations

import structlog
from flask import request
from flask_restx import Namespace, Resource, fields

import config
from app.auth.rbac import require_role
from itgov.models.zendesk import Ticket
from itgov.services.sla_evaluator import SLAStatus, evaluate_ticket, summarize
from itgov.services.zendesk_service import ZendeskService
from itgov.utils.cache_swr import CacheSWR

log = structlog.get_logger(__name__)

# ── Cache em memória ─────────────────────────────────────────────────────────
# Stale-while-revalidate: após a 1ª carga a página nunca espera o Zendesk
# (7–15 s); o valor anterior é servido enquanto uma thread busca o novo.

_CACHE_TTL = 300  # 5min — para MTTR e volume (fetches leves)
_CACHE_TTL_SLA = 600  # 10min — para SLA detail (fetch completo de todos os tickets)

_cache_mttr: CacheSWR[dict] = CacheSWR("zendesk.mttr", _CACHE_TTL)
_cache_vol: CacheSWR[dict] = CacheSWR("zendesk.volume", _CACHE_TTL)
_cache_sla: CacheSWR[dict] = CacheSWR("zendesk.sla_detail", _CACHE_TTL_SLA)
# Histórico muda devagar e custa ~7 páginas + 1 contagem por mês: 1 h basta.
_cache_hist: CacheSWR[dict] = CacheSWR("zendesk.historico", 3600, valido=bool)

_SLA_WINDOW_DAYS = 30


def get_cached_sla_detail() -> dict:
    """Retorna análise SLA detalhada por prioridade + fila de tickets mais antigos.

    TTL 10min — faz fetch completo de todos os tickets (1800+), pesado.
    """
    return _cache_sla.get(_carregar_sla_detail)


def _carregar_sla_detail() -> dict:
    """Busca no Zendesk e monta a análise de SLA (sem cache)."""
    from datetime import UTC, datetime, timedelta

    with _svc() as svc:
        # Duas queries rápidas em vez de uma lenta (todos os tickets)
        # 1) Tickets abertos do grupo — base para SLA/fila/buckets/volume
        open_tickets = svc.get_open_tickets()
        # 2) Tickets resolvidos na janela — base para resolved_7d/30d e SLA do período
        solved_recent = svc.get_solved_tickets(days=_SLA_WINDOW_DAYS)
        targets = svc.get_sla_targets()

    now = datetime.now(UTC)
    cutoff_7d = now - timedelta(days=7)
    sla_by_id = {t.id: evaluate_ticket(t, targets, now) for t in open_tickets}

    # ── SLA da fila aberta por prioridade ─────────────────────────────────
    by_priority: dict[str, dict] = {}
    for prio, target in targets.by_priority.items():
        bucket = [t for t in open_tickets if str(t.priority or "normal") == prio]
        backlog = summarize(bucket, targets, now)
        by_priority[prio] = {
            "count": backlog.total_tickets,
            "breached": backlog.breached,
            "unknown": backlog.unknown,
            "ok": backlog.total_tickets - backlog.breached - backlog.unknown,
            "compliance_pct": backlog.compliance_pct,
            "first_reply_h": round(target.first_reply_minutes / 60, 1),
            "resolution_h": round(target.resolution_minutes / 60, 1),
        }

    # ── Fila: tickets mais antigos (abertos) ─────────────────────────────
    oldest = sorted(open_tickets, key=lambda t: t.age_hours, reverse=True)
    oldest_list = []
    for t in oldest[:25]:
        age_h = t.age_hours
        age_str = f"{int(age_h // 24)}d {int(age_h % 24)}h" if age_h >= 24 else f"{int(age_h)}h"
        prio = str(t.priority or "normal")
        oldest_list.append(
            {
                "id": t.id,
                "subject": t.subject,
                "status": str(t.status),
                "priority": prio,
                "age_hours": round(age_h, 1),
                "age_str": age_str,
                "breached": sla_by_id[t.id].overall == SLAStatus.BREACHED,
                "created_fmt": t.created_at.strftime("%d/%m/%Y"),
            }
        )

    # ── Baldes de idade (tickets abertos) ─────────────────────────────────
    age_buckets = {"<8h": 0, "8-48h": 0, "48-168h": 0, ">168h": 0}
    for t in open_tickets:
        h = t.age_hours
        if h < 8:
            age_buckets["<8h"] += 1
        elif h < 48:
            age_buckets["8-48h"] += 1
        elif h < 168:
            age_buckets["48-168h"] += 1
        else:
            age_buckets[">168h"] += 1

    # ── Resolvidos recentes (últimos 30 dias já filtrados na query) ────────
    def _solved_at(t: Ticket) -> datetime:
        dt = t.metric_set.solved_at if t.metric_set and t.metric_set.solved_at else t.updated_at
        return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt

    resolved_7d = sum(1 for t in solved_recent if _solved_at(t) >= cutoff_7d)
    resolved_30d = len(solved_recent)

    # ── Volume por status (a partir dos abertos + resolvidos recentes) ─────
    from collections import Counter

    vol_counter = Counter(str(t.status) for t in open_tickets)
    vol_counter.update(str(t.status) for t in solved_recent)

    dados = {
        "total_open": len(open_tickets),
        # Abertos + resolvidos em 30 dias — NÃO é o histórico (ver get_cached_historico).
        "total_all": len(open_tickets) + len(solved_recent),
        "by_priority": by_priority,
        "oldest": oldest_list,
        "age_buckets": age_buckets,
        "resolved_7d": resolved_7d,
        "resolved_30d": resolved_30d,
        "volume": dict(vol_counter),
        "backlog_breached": sum(1 for r in sla_by_id.values() if r.overall == SLAStatus.BREACHED),
        "period": summarize(solved_recent, targets, now, window_days=_SLA_WINDOW_DAYS).model_dump(),
        "sla_source": targets.source,
        "sla_policy": targets.policy_name,
    }

    return dados


def get_cached_mttr_summary() -> dict:
    """Retorna resumo MTTR/SLA + CSAT do Zendesk com cache de 5min."""
    return _cache_mttr.get(_carregar_mttr_summary)


def _carregar_mttr_summary() -> dict:
    """Busca no Zendesk o resumo MTTR/SLA + CSAT (sem cache)."""
    with _svc() as svc:
        open_tickets = svc.get_open_tickets()
        targets = svc.get_sla_targets()
        period = svc.get_sla_metrics(days=_SLA_WINDOW_DAYS)
        csat = svc.get_csat_summary()

    total_open = len(open_tickets)
    backlog = summarize(open_tickets, targets)
    avg_age = round(sum(t.age_hours for t in open_tickets) / total_open, 1) if total_open else 0.0

    dados = {
        "total_open": total_open,
        # Fila aberta: tickets cujo SLA já estourou (1ª resposta ou resolução)
        "breached": backlog.breached,
        # Período: SLA dos tickets resolvidos na janela — o KPI de mercado
        "compliance_pct": period.compliance_pct,
        "first_reply_compliance_pct": period.first_reply_compliance_pct,
        "resolution_compliance_pct": period.resolution_compliance_pct,
        "avg_first_reply_minutes": period.avg_first_reply_minutes,
        "period_total": period.total_tickets,
        "period_breached": period.breached,
        "period_unknown": period.unknown,
        "window_days": _SLA_WINDOW_DAYS,
        "avg_age_hours": avg_age,
        "sla_source": targets.source,
        "sla_policy": targets.policy_name,
        "csat_pct": csat.csat_pct,
        "csat_sample": csat.sample_size,
        "csat_good": csat.good,
        "csat_bad": csat.bad,
    }
    return dados


def get_cached_volume_by_status() -> dict:
    """Retorna volume de tickets por status com cache de 5min."""
    return _cache_vol.get(_carregar_volume)


def _carregar_volume() -> dict:
    """Busca no Zendesk o volume de tickets por status (sem cache)."""
    with _svc() as svc:
        return svc.get_ticket_volume_by_status()


def get_cached_historico() -> dict:
    """Histórico do grupo (chamados por mês e volume por usuário), cache de 1 h.

    Zendesk fora do ar na primeira carga devolve ``{}`` (a página mostra
    "indisponível agora") em vez de derrubar a página.
    """
    import httpx
    from tenacity import RetryError

    try:
        return _cache_hist.get(_carregar_historico)
    except (httpx.HTTPError, RetryError, ValueError, KeyError) as exc:
        log.warning("zendesk.historico_indisponivel", erro=type(exc).__name__)
        return {}


def _carregar_historico() -> dict:
    """Busca no Zendesk todos os tickets do grupo e monta o histórico (sem cache)."""
    from datetime import date

    from itgov.services.zendesk_historico import data_criacao, meses_desde, resumir

    hoje = date.today()
    with _svc() as svc:
        grupo = svc.get_group()
        tickets = svc.get_tickets()
        desde = data_criacao(grupo) or (min(t.created_at.date() for t in tickets) if tickets else None)
        resolvidos = {
            ini.strftime("%Y-%m"): svc.count_tickets(f"solved>={ini.isoformat()} solved<{fim.isoformat()}")
            for ini, fim in (meses_desde(desde, hoje) if desde else [])
        }
        ids = [t.assignee_id for t in tickets if t.assignee_id] + [t.requester_id for t in tickets]
        nomes = svc.get_user_names(ids)
    hist = resumir(tickets, nomes, resolvidos, (grupo or {}).get("name") or "Todos os grupos", desde, hoje)
    return hist.model_dump(mode="json")


def aquecer_caches() -> None:
    """Carrega os caches do Zendesk em segundo plano (subida do worker)."""
    _cache_mttr.aquecer(_carregar_mttr_summary)
    _cache_vol.aquecer(_carregar_volume)
    _cache_sla.aquecer(_carregar_sla_detail)
    _cache_hist.aquecer(_carregar_historico)


ns = Namespace("zendesk", description="Zendesk support integration")

# ── Swagger models ────────────────────────────────────────────────────────────

ticket_model = ns.model(
    "ZendeskTicket",
    {
        "id": fields.Integer,
        "subject": fields.String,
        "status": fields.String(description="new|open|pending|hold|solved|closed"),
        "priority": fields.String(description="low|normal|high|urgent"),
        "created_at": fields.String(description="ISO 8601"),
        "updated_at": fields.String(description="ISO 8601"),
        "is_open": fields.Boolean,
        "age_hours": fields.Float(description="Horas desde abertura"),
        "tags": fields.List(fields.String),
    },
)

sla_model = ns.model(
    "ZendeskSLAMetric",
    {
        "total_tickets": fields.Integer(description="Tickets resolvidos na janela"),
        "breached": fields.Integer(description="Tickets da janela com SLA violado"),
        "unknown": fields.Integer(description="Tickets sem dados de SLA (fora do denominador)"),
        "compliance_pct": fields.Float(
            allow_null=True, description="Percentual de tickets dentro do SLA, ou null se sem dados"
        ),
        "first_reply_compliance_pct": fields.Float(allow_null=True),
        "resolution_compliance_pct": fields.Float(allow_null=True),
        "avg_first_reply_minutes": fields.Float(allow_null=True, description="Minutos úteis"),
        "window_days": fields.Integer,
        "open_breached": fields.Integer(description="Tickets abertos com SLA já violado"),
    },
)

csat_model = ns.model(
    "ZendeskCSATSummary",
    {
        "total_ratings": fields.Integer,
        "good": fields.Integer,
        "bad": fields.Integer,
        "csat_pct": fields.Float(
            allow_null=True, description="Percentual de avaliações positivas, ou null se sem dados"
        ),
        "sample_size": fields.Integer(description="Surveys respondidas (good + bad) na janela"),
    },
)

volume_model = ns.model(
    "ZendeskVolumeByStatus",
    dict.fromkeys(("new", "open", "pending", "hold", "solved", "closed"), fields.Integer),
)


# ── Helper ────────────────────────────────────────────────────────────────────


def _svc() -> ZendeskService:
    """Instancia ZendeskService com credenciais do ambiente.

    Se ``ZENDESK_GROUP_ID`` estiver definido, filtra tickets server-side
    pelo grupo (ex: TI / Infra), reduzindo drasticamente o payload nas
    consultas de SLA e MTTR.

    Raises:
        HTTPException: 503 quando ``ZENDESK_SUBDOMAIN`` não está configurado —
            sem isso o cliente montaria ``https://.zendesk.com`` e estouraria 500.
    """
    if not config.ZENDESK_SUBDOMAIN:
        ns.abort(503, "Integração Zendesk não configurada")
    group_id = config.ZENDESK_GROUP_ID or None
    return ZendeskService(
        subdomain=config.ZENDESK_SUBDOMAIN or "",
        email=config.ZENDESK_EMAIL or "",
        api_token=config.ZENDESK_API_TOKEN or "",
        group_id=group_id,
    )


# ── Resources ─────────────────────────────────────────────────────────────────


@ns.route("/groups")
class GroupListResource(Resource):
    @ns.doc(description="Lista grupos Zendesk — use para descobrir o ZENDESK_GROUP_ID")
    @require_role("admin", "gestor", "visualizador")
    def get(self) -> list[dict]:
        """Retorna id + name de todos os grupos do Zendesk."""
        with _svc() as svc:
            return svc.get_groups()


@ns.route("/tickets")
class TicketListResource(Resource):
    @ns.marshal_list_with(ticket_model)
    @ns.doc(
        description="Lista tickets com filtro opcional por status",
        params={"status": "Filtro de status: new|open|pending|hold|solved|closed"},
    )
    @require_role("admin", "gestor", "visualizador")
    def get(self) -> list[dict]:
        """Retorna tickets do Zendesk."""
        status_filter = request.args.get("status")
        with _svc() as svc:
            tickets = svc.get_tickets(status=status_filter)
        return [
            {
                **t.model_dump(),
                "created_at": t.created_at.isoformat(),
                "updated_at": t.updated_at.isoformat(),
            }
            for t in tickets
        ]


@ns.route("/tickets/open")
class OpenTicketResource(Resource):
    @ns.marshal_list_with(ticket_model)
    @ns.doc(description="Lista apenas tickets abertos (new + open + pending)")
    @require_role("admin", "gestor", "visualizador")
    def get(self) -> list[dict]:
        """Tickets abertos (requerem atenção)."""
        with _svc() as svc:
            tickets = svc.get_open_tickets()
        return [
            {
                **t.model_dump(),
                "created_at": t.created_at.isoformat(),
                "updated_at": t.updated_at.isoformat(),
            }
            for t in tickets
        ]


@ns.route("/tickets/volume")
class VolumeResource(Resource):
    @ns.marshal_with(volume_model)
    @ns.doc(description="Contagem de tickets por status")
    @require_role("admin", "gestor", "visualizador")
    def get(self) -> dict:
        """Volume de tickets agrupado por status."""
        return get_cached_volume_by_status()


@ns.route("/sla")
class SLAResource(Resource):
    @ns.marshal_with(sla_model)
    @ns.doc(description="Métricas de SLA — compliance dos resolvidos na janela e fila em breach")
    @require_role("admin", "gestor", "visualizador")
    def get(self) -> dict:
        """SLA dos tickets resolvidos nos últimos 30 dias (horário comercial)."""
        summary = get_cached_mttr_summary()
        return {
            "total_tickets": summary["period_total"],
            "breached": summary["period_breached"],
            "unknown": summary["period_unknown"],
            "compliance_pct": summary["compliance_pct"],
            "first_reply_compliance_pct": summary["first_reply_compliance_pct"],
            "resolution_compliance_pct": summary["resolution_compliance_pct"],
            "avg_first_reply_minutes": summary["avg_first_reply_minutes"],
            "window_days": summary["window_days"],
            "open_breached": summary["breached"],
        }


@ns.route("/csat")
class CSATResource(Resource):
    @ns.marshal_with(csat_model)
    @ns.doc(description="Customer Satisfaction Score — resumo de avaliações")
    @require_role("admin", "gestor", "visualizador")
    def get(self) -> dict:
        """Resumo de CSAT (satisfação do cliente)."""
        summary = get_cached_mttr_summary()
        return {
            "total_ratings": summary["csat_sample"],
            "good": summary["csat_good"],
            "bad": summary["csat_bad"],
            "csat_pct": summary["csat_pct"],
            "sample_size": summary["csat_sample"],
        }
