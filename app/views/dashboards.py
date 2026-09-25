"""HTML view routes for the governance dashboard."""

from __future__ import annotations

import asyncio
import os

import structlog
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.auth.rbac import require_role
from app.integrations import graph_configured, zendesk_configured
from app.services.metrics_aggregator import MetricsAggregator

log = structlog.get_logger(__name__)

bp = Blueprint("dashboards", __name__, template_folder="../templates")


@bp.context_processor
def _inject_globals() -> dict:
    """Provide app_version and environment when running inside legacy app.py."""
    from flask import current_app

    return {
        "app_version": current_app.config.get("APP_VERSION", "1.1.0"),
        "app_name": current_app.config.get("APP_NAME", "Governança de TI Dashboard"),
        "environment": current_app.config.get("APP_ENVIRONMENT", "production"),
    }


_VALID_PILLAR_IDS = {
    "strategic_alignment",
    "value_delivery",
    "risk_management",
    "resource_management",
    "performance_measure",
}


def _get_governance() -> dict:
    aggregator = MetricsAggregator()
    governance = asyncio.run(aggregator.calculate_full_score())
    data = governance.model_dump(mode="json")
    data["provider_name"] = aggregator.provider_name
    return data


@bp.route("/dashboard")
@login_required
@require_role("admin", "gestor", "visualizador")
def dashboard_redirect():
    from flask import redirect, url_for

    return redirect(url_for("dashboards.overview"))


@bp.route("/")
@login_required
@require_role("admin", "gestor", "visualizador")
def overview() -> str:
    """Render governance overview dashboard."""
    data = _get_governance()
    return render_template("dashboards/overview.html", governance=data)


@bp.route("/pillars")
@login_required
@require_role("admin", "gestor", "visualizador")
def pillars() -> str:
    """Render all pillars detail page."""
    data = _get_governance()
    return render_template("dashboards/pillars.html", governance=data)


@bp.route("/pilares")
@login_required
def pilares_redirect():
    """Alias em português de /pillars (G-03) — mesma página, mesma rota nomeada."""
    return redirect(url_for("dashboards.pillars"))


@bp.route("/sla")
@login_required
@require_role("admin", "gestor")
def sla_chamados() -> str:
    """Render painel SLA / Chamados (Zendesk)."""
    from itgov.api.v1.zendesk import get_cached_sla_detail

    if not zendesk_configured():
        abort(404)

    data = get_cached_sla_detail()
    return render_template("dashboards/sla_chamados.html", data=data)


@bp.route("/zendesk")
@login_required
@require_role("admin", "gestor")
def zendesk_mttr() -> str:
    """Render Zendesk MTTR / suporte dashboard."""
    from itgov.api.v1.zendesk import get_cached_mttr_summary, get_cached_volume_by_status

    if not zendesk_configured():
        abort(404)

    mttr = get_cached_mttr_summary()
    volume = get_cached_volume_by_status()

    return render_template(
        "dashboards/zendesk_mttr.html",
        mttr=mttr,
        volume=volume,
    )


@bp.route("/governance/devices")
@login_required
@require_role("admin", "gestor")
def governance_devices() -> str:
    """Render pilar Dispositivos (Governança M365)."""
    from app.config import get_settings
    from itgov.api.v1.governance_devices import get_cached_device_summary

    if not graph_configured():
        abort(404)

    try:
        summary = get_cached_device_summary()
    except RuntimeError:
        abort(503)

    stale_days = get_settings().graph.device_stale_days
    return render_template("dashboards/governance_devices.html", summary=summary, stale_days=stale_days)


@bp.route("/governance/apps")
@login_required
@require_role("admin", "gestor")
def governance_apps() -> str:
    """Render pilar Aplicativos (Governança M365)."""
    from itgov.api.v1.governance_apps import get_cached_app_summary

    if not graph_configured():
        abort(404)

    try:
        summary = get_cached_app_summary()
    except RuntimeError:
        abort(503)

    return render_template("dashboards/governance_apps.html", summary=summary)


@bp.route("/governance/compliance")
@login_required
@require_role("admin", "gestor")
def governance_compliance() -> str:
    """Render pilar Compliance (Secure Score) — Governança M365."""
    from itgov.api.v1.governance_compliance import get_cached_compliance_summary
    from itgov.services.dns_check_service import _get_domain, get_email_security_summary

    if not graph_configured():
        abort(404)

    summary = get_cached_compliance_summary()

    domain = _get_domain()
    email_security = get_email_security_summary(domain) if domain else None

    return render_template(
        "dashboards/governance_compliance.html",
        summary=summary,
        email_security=email_security,
    )


@bp.route("/governance/data")
@login_required
@require_role("admin", "gestor")
def governance_data() -> str:
    """Render pilar Dados (Sensitivity Labels) — Governança M365."""
    from itgov.api.v1.governance_data import get_cached_data_summary

    if not graph_configured():
        abort(404)

    summary = get_cached_data_summary()
    return render_template("dashboards/governance_data.html", summary=summary)


@bp.route("/governance/security-alerts")
@login_required
@require_role("admin", "gestor")
def governance_security_alerts() -> str:
    """Render pilar Endpoint — Alertas de Segurança (Defender, KPI-END-01)."""
    from itgov.api.v1.governance_security_alerts import get_cached_security_alerts_summary

    if not graph_configured():
        abort(404)

    summary = get_cached_security_alerts_summary()
    return render_template("dashboards/governance_security_alerts.html", summary=summary)


@bp.route("/governance/service-health")
@login_required
@require_role("admin", "gestor")
def governance_service_health() -> str:
    """Render Service Health M365 — status dos serviços do tenant."""
    from itgov.api.v1.governance_service_health import get_cached_service_health_summary

    if not graph_configured():
        abort(404)

    summary = get_cached_service_health_summary()
    return render_template("dashboards/governance_service_health.html", summary=summary)


@bp.route("/backup")
@login_required
@require_role("admin", "gestor")
def acronis_backup() -> str:
    """Render painel de Backup/Proteção Acronis."""
    from itgov.api.v1.acronis_backup import get_cached_acronis_summary

    if not os.getenv("ACRONIS_BASE_URL"):
        abort(404)

    data = get_cached_acronis_summary()
    return render_template("dashboards/acronis_backup.html", data=data)


@bp.route("/zabbix")
@login_required
@require_role("admin", "gestor", "operador")
def zabbix_monitoring() -> str:
    """Render painel de monitoramento Zabbix."""
    from itgov.api.v1.zabbix_monitoring import get_cached_problems, get_cached_zabbix_summary

    if not os.getenv("ZABBIX_URL"):
        abort(404)

    summary = get_cached_zabbix_summary()
    problems = get_cached_problems()
    return render_template("dashboards/zabbix_monitoring.html", summary=summary, problems=problems)


@bp.route("/infra")
@login_required
@require_role("admin", "gestor", "operador")
def infra_monitoring() -> str:
    """Render painel de Infraestrutura (servidores, VMs, firewall, etc.)."""
    from itgov.api.v1.infra_monitoring import get_cached_infra_summary

    if not os.getenv("ZABBIX_URL"):
        abort(404)

    data = get_cached_infra_summary()
    return render_template("dashboards/infra_monitoring.html", data=data)


@bp.route("/cftv")
@login_required
@require_role("admin", "gestor", "operador")
def cftv_monitoring() -> str:
    """Render painel CFTV: cards por gravador (DVR/NVR), filtro por unidade."""
    from app.models.unidade import DvrUnidade, Unidade
    from itgov.api.v1.cftv_monitoring import SEM_UNIDADE, get_cached_cftv_summary, montar_visao

    if not os.getenv("ZABBIX_URL"):
        abort(404)

    todas = Unidade.query.all()
    unidades = [u for u in todas if u.ativo]
    nomes = {u.id: u.caminho for u in todas}
    filtro_raw = request.args.get("unidade", "")
    filtro = _filtro_unidade(filtro_raw, unidades, SEM_UNIDADE)

    data = get_cached_cftv_summary()
    visao = montar_visao(
        data,
        unidade_por_gravador={v.dvr: v.unidade_id for v in DvrUnidade.query.all()},
        unidades=nomes,
        unidade_por_loja={u.nome.lower(): u.id for u in unidades},
        filtro=filtro,
    )
    return render_template(
        "dashboards/cftv_monitoring.html",
        data=data,
        visao=visao,
        unidades=sorted(unidades, key=lambda u: u.caminho),
        filtro=filtro_raw if filtro is not None else "",
    )


@bp.route("/cftv/gravador", methods=["POST"])
@login_required
@require_role("admin", "gestor")
def cftv_gravador_unidade() -> object:
    """Define a unidade de um gravador (DVR/NVR) a partir do card na página /cftv."""
    from app.extensions import db
    from app.models.unidade import DvrUnidade, Unidade

    gravador = request.form.get("gravador", "").strip()
    unidade_raw = request.form.get("unidade_id", "").strip()
    if not gravador:
        abort(400)
    unidade_id = int(unidade_raw) if unidade_raw.isdigit() else None
    if unidade_id is not None and db.session.get(Unidade, unidade_id) is None:
        abort(400)

    vinculo = DvrUnidade.query.filter_by(dvr=gravador).first()
    if vinculo is None:
        vinculo = DvrUnidade(dvr=gravador)
        db.session.add(vinculo)
    vinculo.unidade_id = unidade_id
    db.session.commit()
    log.info("cftv.gravador_unidade", gravador=gravador, unidade_id=unidade_id, user=current_user.email)
    flash(f"Unidade de {gravador} atualizada.", "success")
    return redirect(url_for("dashboards.cftv_monitoring", unidade=request.form.get("filtro") or None))


@bp.route("/unidades")
@login_required
@require_role("admin", "gestor", "operador")
def unidades_list() -> str:
    """Lista as unidades (sites) em árvore, com faixas de IP e gravadores vinculados."""
    from app.models.unidade import DvrUnidade, Unidade

    raizes = Unidade.query.filter_by(parent_id=None).order_by(Unidade.nome).all()
    gravadores: dict[int, list[str]] = {}
    for v in DvrUnidade.query.order_by(DvrUnidade.dvr).all():
        if v.unidade_id is not None:
            gravadores.setdefault(v.unidade_id, []).append(v.dvr)
    return render_template("dashboards/unidades.html", raizes=raizes, gravadores=gravadores)


@bp.route("/unidades/nova", methods=["GET", "POST"])
@bp.route("/unidades/<int:unidade_id>/editar", methods=["GET", "POST"])
@login_required
@require_role("admin", "gestor")
def unidade_form(unidade_id: int | None = None) -> object:
    """Formulário de criação/edição de unidade (nome, unidade pai, faixas de IP)."""
    from app.extensions import db
    from app.models.unidade import DvrUnidade, Unidade, parse_faixas

    unidade = db.session.get(Unidade, unidade_id) if unidade_id else None
    if unidade_id and unidade is None:
        abort(404)
    # Hierarquia de dois níveis: só raízes podem ser pai, e nunca a própria unidade
    pais = [
        u for u in Unidade.query.filter_by(parent_id=None).order_by(Unidade.nome) if not unidade or u.id != unidade.id
    ]

    def _render() -> str:
        return render_template("dashboards/unidade_form.html", unidade=unidade, pais=pais)

    if request.method == "GET":
        return _render()

    if request.form.get("action") == "delete" and unidade:
        if unidade.filhas:
            flash("Remova ou mova as unidades filhas antes de excluir.", "error")
            return _render()
        DvrUnidade.query.filter_by(unidade_id=unidade.id).update({"unidade_id": None})
        db.session.delete(unidade)
        db.session.commit()
        log.info("unidade.removida", unidade=unidade.nome, user=current_user.email)
        flash("Unidade removida.", "success")
        return redirect(url_for("dashboards.unidades_list"))

    nome = request.form.get("nome", "").strip()
    parent_raw = request.form.get("parent_id", "").strip()
    parent_id = int(parent_raw) if parent_raw.isdigit() else None
    if not nome:
        flash("Nome é obrigatório.", "error")
        return _render()
    if parent_id is not None and parent_id not in {p.id for p in pais}:
        flash("Unidade pai inválida.", "error")
        return _render()
    if unidade and parent_id is not None and unidade.filhas:
        flash("Uma unidade com filhas não pode virar filha de outra.", "error")
        return _render()
    try:
        faixas = parse_faixas(request.form.get("faixas_ip", ""))
    except ValueError as exc:
        flash(str(exc), "error")
        return _render()
    duplicada = Unidade.query.filter_by(nome=nome, parent_id=parent_id).first()
    if duplicada and (not unidade or duplicada.id != unidade.id):
        flash(f"Já existe a unidade {nome} neste nível.", "error")
        return _render()

    if unidade is None:
        unidade = Unidade(nome=nome)
        db.session.add(unidade)
    unidade.nome = nome
    unidade.parent_id = parent_id
    unidade.faixas_ip = "\n".join(faixas)
    unidade.ativo = "ativo" in request.form or unidade_id is None
    db.session.commit()
    log.info("unidade.salva", unidade=unidade.caminho, faixas=len(faixas), user=current_user.email)
    flash("Unidade salva.", "success")
    return redirect(url_for("dashboards.unidades_list"))


# Mapa de Câmeras (serviço nativo mapa-cameras.service, porta 8080) é servido
# pelo nginx em /mapa-cameras/, protegido por auth_request contra a rota abaixo.
MAPA_CAMERAS_ROLES: tuple[str, ...] = ("admin", "gestor")
MAPA_CAMERAS_PROXY_PATH = "/mapa-cameras/"


@bp.route("/cameras")
@login_required
@require_role(*MAPA_CAMERAS_ROLES)
def mapa_cameras() -> str:
    """Render o Mapa de Câmeras embutido (iframe) na dashboard."""
    return render_template("dashboards/mapa_cameras.html", mapa_url=MAPA_CAMERAS_PROXY_PATH)


@bp.route("/cameras/auth")
def mapa_cameras_auth() -> tuple[str, int]:
    """Subrequest do nginx (auth_request) que libera o proxy do Mapa de Câmeras.

    Não usa ``login_required``/``require_role``: o handler global de 401
    responde 302 para /login, e auth_request só aceita 2xx/401/403 (o resto
    vira 500 no nginx). Por isso o status é devolvido direto.

    Returns:
        204 quando o usuário logado tem perfil permitido, 401 sem sessão,
        403 com perfil negado.
    """
    if not current_user.is_authenticated:
        return "", 401
    if current_user.role not in MAPA_CAMERAS_ROLES:
        log.warning("mapa_cameras_access_denied", user_id=current_user.get_id(), role=current_user.role)
        return "", 403
    return "", 204


@bp.route("/network")
@login_required
def network_redirect():
    return redirect(url_for("dashboards.rede_monitoring"))


def _filtro_unidade(raw: str, unidades: list, sem_unidade: str) -> set[int] | str | None:
    """Converte ``?unidade=`` em ids aceitos (unidade + filhas), ``sem_unidade`` ou None."""
    if raw == sem_unidade:
        return sem_unidade
    if raw.isdigit():
        selecionada = next((u for u in unidades if u.id == int(raw)), None)
        return selecionada.ids_subarvore() if selecionada else None
    return None


def _listar_ativos() -> list[dict]:
    """Ativos vivos do inventário como dicts (sessão fechada ao retornar).

    Falha no banco de ativos não derruba as páginas de rede: devolve lista vazia.
    """
    from sqlalchemy.exc import SQLAlchemyError

    from itgov.db.session import get_session
    from itgov.services.ativo_service import AtivoService

    try:
        with get_session() as session:
            return [
                {
                    "id": str(a.id),
                    "nome": a.nome,
                    "tipo": a.tipo,
                    "criticidade": a.criticidade,
                    "ambiente": a.ambiente,
                    "owner": a.owner,
                    "metadata": a.metadata_ or {},
                    "created_at": a.created_at,
                }
                for a in AtivoService(session).list(limit=10000)
            ]
    except SQLAlchemyError as exc:
        log.warning("ativos.listagem_falhou", erro=str(exc))
        return []


@bp.route("/rede")
@login_required
@require_role("admin", "gestor", "operador")
def rede_monitoring() -> str:
    """Render painel de rede: fila de revisão dos hosts descobertos pelo nmap."""
    from app.models.unidade import Unidade
    from itgov.api.v1.rede_monitoring import (
        SEM_UNIDADE,
        STATUS_CADASTRADO,
        STATUS_NOVO,
        get_cached_rede_summary,
        montar_descobertos,
    )
    from itgov.models.ativo import TIPO_LABELS

    if not os.getenv("ZABBIX_URL"):
        abort(404)

    todas = Unidade.query.all()
    unidades = [u for u in todas if u.ativo]
    filtro_raw = request.args.get("unidade", "")
    filtro = _filtro_unidade(filtro_raw, unidades, SEM_UNIDADE)
    status = request.args.get("status", "")
    if status not in (STATUS_NOVO, STATUS_CADASTRADO):
        status = ""

    data = get_cached_rede_summary()
    ativos_por_ip = {a["metadata"]["ip"]: a for a in _listar_ativos() if a["metadata"].get("ip")}
    revisao = montar_descobertos(
        data["influx"].get("hosts", []),
        faixas=[(u.id, f) for u in unidades for f in u.faixas],
        ativos_por_ip=ativos_por_ip,
        unidades={u.id: u.caminho for u in todas},
        filtro_unidade=filtro,
        filtro_status=status,
    )
    return render_template(
        "dashboards/rede_monitoring.html",
        data=data,
        revisao=revisao,
        unidades=sorted(unidades, key=lambda u: u.caminho),
        filtro=filtro_raw if filtro is not None else "",
        status=status,
        tipo_labels=TIPO_LABELS,
        sem_faixas=not any(u.faixas for u in unidades),
    )


@bp.route("/rede/cadastrar", methods=["GET", "POST"])
@login_required
@require_role("admin", "gestor")
def rede_cadastrar_ativo() -> object:
    """Cadastra um host descoberto como ativo de rede, já com tipo e unidade sugeridos."""
    from sqlalchemy import select

    from app.models.unidade import Unidade
    from itgov.api.v1.rede_monitoring import host_descoberto, unidade_do_ip
    from itgov.db.session import get_session
    from itgov.models.ativo import AMBIENTES_VALIDOS, CRITICIDADES_VALIDAS, TIPO_LABELS
    from itgov.models.db.ativo import AtivoDB
    from itgov.services.ativo_service import AtivoDuplicateError, AtivoService

    ip = (request.values.get("ip") or "").strip()
    host = host_descoberto(ip) if ip else None
    if host is None:
        abort(404)
    if any(a["metadata"].get("ip") == ip for a in _listar_ativos()):
        flash(f"{ip} já está cadastrado como ativo.", "error")
        return redirect(url_for("dashboards.ativos_rede"))

    unidades = sorted((u for u in Unidade.query.all() if u.ativo), key=lambda u: u.caminho)
    sugestao = {
        "nome": host["hostname"] if host["hostname"] and host["hostname"] != ip else f"{host['tipo_sugerido']}-{ip}",
        "tipo": host["tipo_sugerido"] if host["tipo_sugerido"] in TIPO_LABELS else "outro",
        "unidade_id": unidade_do_ip(ip, [(u.id, f) for u in unidades for f in u.faixas]),
        "criticidade": "media",
        "ambiente": "prod",
        "descricao": "",
    }

    def _render(valores: dict) -> str:
        return render_template(
            "dashboards/ativo_rede_form.html",
            host=host,
            valores=valores,
            unidades=unidades,
            tipo_labels=TIPO_LABELS,
            criticidades=sorted(CRITICIDADES_VALIDAS),
            ambientes=sorted(AMBIENTES_VALIDOS),
        )

    if request.method == "GET":
        return _render(sugestao)

    valores = {k: (request.form.get(k) or "").strip() for k in sugestao}
    unidade = next((u for u in unidades if str(u.id) == valores["unidade_id"]), None)
    metadata: dict[str, object] = {
        "ip": ip,
        "mac": host["mac"],
        "fabricante": host["vendor"],
        "portas": host["portas"],
        "origem": "descoberta_rede",
        "tipo_sugerido": host["tipo_sugerido"],
        "unidade_id": unidade.id if unidade else None,
        "unidade": unidade.caminho if unidade else "",
    }
    if valores["descricao"]:
        metadata["descricao"] = valores["descricao"]
    payload = {
        "nome": valores["nome"],
        "tipo": valores["tipo"],
        "ambiente": valores["ambiente"],
        "criticidade": valores["criticidade"],
        "owner": current_user.email,
        "tags": ["descoberta-rede", valores["tipo"]],
        "metadata": metadata,
    }
    try:
        with get_session() as session:
            svc = AtivoService(session)
            try:
                svc.create(payload)
            except AtivoDuplicateError:
                # (nome, tipo) é único mesmo após soft delete: recadastro reativa o removido
                existente = session.execute(
                    select(AtivoDB).where(AtivoDB.nome == valores["nome"], AtivoDB.tipo == valores["tipo"])
                ).scalar_one()
                if existente.deleted_at is None:
                    raise
                svc.upsert(payload)
    except AtivoDuplicateError:
        flash(f"Já existe um ativo {valores['nome']} do tipo {valores['tipo']}.", "error")
        return _render(valores)
    except ValueError as exc:  # ValidationError do Pydantic herda de ValueError
        flash(f"Dados inválidos: {exc}", "error")
        return _render(valores)

    log.info("rede.ativo_cadastrado", ip=ip, tipo=valores["tipo"], unidade=metadata["unidade"], user=current_user.email)
    flash(f"{valores['nome']} cadastrado como ativo.", "success")
    return redirect(url_for("dashboards.rede_monitoring", status="novos"))


@bp.route("/ativos-rede")
@login_required
@require_role("admin", "gestor", "operador")
def ativos_rede() -> str:
    """Ativos do inventário separados por unidade, com filtro por unidade e tipo."""
    from app.models.unidade import Unidade
    from itgov.api.v1.rede_monitoring import SEM_UNIDADE
    from itgov.models.ativo import TIPO_LABELS

    todas = Unidade.query.all()
    unidades = [u for u in todas if u.ativo]
    nomes = {u.id: u.caminho for u in todas}
    filtro_raw = request.args.get("unidade", "")
    filtro = _filtro_unidade(filtro_raw, unidades, SEM_UNIDADE)
    tipo = request.args.get("tipo", "")

    grupos: dict[str, list[dict]] = {}
    for a in _listar_ativos():
        uid = a["metadata"].get("unidade_id")
        if filtro == SEM_UNIDADE and uid is not None:
            continue
        if isinstance(filtro, set) and uid not in filtro:
            continue
        if tipo and a["tipo"] != tipo:
            continue
        grupos.setdefault(nomes.get(uid, "") if uid else "", []).append(a)

    secoes = [(nome or "Sem unidade", sorted(itens, key=lambda a: a["nome"].lower())) for nome, itens in grupos.items()]
    secoes.sort(key=lambda s: (s[0] == "Sem unidade", s[0]))
    return render_template(
        "dashboards/ativos_rede.html",
        secoes=secoes,
        total=sum(len(i) for _, i in secoes),
        unidades=sorted(unidades, key=lambda u: u.caminho),
        filtro=filtro_raw if filtro is not None else "",
        tipo=tipo,
        tipo_labels=TIPO_LABELS,
    )


@bp.route("/ativos-rede/<string:ativo_id>/remover", methods=["POST"])
@login_required
@require_role("admin", "gestor")
def ativo_rede_remover(ativo_id: str) -> object:
    """Remove (soft delete) um ativo do inventário."""
    from uuid import UUID

    from itgov.db.session import get_session
    from itgov.services.ativo_service import AtivoNotFoundError, AtivoService

    try:
        uuid = UUID(ativo_id)
    except ValueError:
        abort(404)
    try:
        with get_session() as session:
            AtivoService(session).delete(uuid)
    except AtivoNotFoundError:
        abort(404)
    log.info("rede.ativo_removido", ativo_id=ativo_id, user=current_user.email)
    flash("Ativo removido.", "success")
    return redirect(url_for("dashboards.ativos_rede"))


@bp.route("/links")
@login_required
@require_role("admin", "gestor", "operador")
def links_manager() -> str:
    """Render gerenciador de links WAN/Internet."""
    from itgov.api.v1.links_manager import get_cached_links
    from itgov.services.fortinet_service import get_cached_fortinet

    data = get_cached_links()
    fortinet = get_cached_fortinet()
    return render_template("dashboards/links_manager.html", data=data, fortinet=fortinet)


@bp.route("/links/novo", methods=["GET", "POST"])
@bp.route("/links/<int:link_id>/editar", methods=["GET", "POST"])
@login_required
@require_role("admin", "gestor")
def link_form(link_id: int | None = None) -> str:
    """Formulario de criacao e edicao de links WAN."""
    from app.extensions import db
    from app.models.link import LINK_TYPES, Link
    from itgov.api.v1.links_manager import invalidar_cache

    link = Link.query.get(link_id) if link_id else None
    if link_id and not link:
        abort(404)

    if request.method == "POST":
        action = request.form.get("action", "save")

        if action == "delete" and link:
            db.session.delete(link)
            db.session.commit()
            invalidar_cache()
            flash("Link removido.", "success")
            return redirect(url_for("dashboards.links_manager"))

        ip = request.form.get("ip", "").strip()
        name = request.form.get("name", "").strip()
        if not ip or not name:
            flash("IP e nome sao obrigatorios.", "error")
            return render_template("dashboards/link_form.html", link=link, link_types=LINK_TYPES)

        # Verificar IP duplicado (exceto o proprio)
        existing = Link.query.filter_by(ip=ip).first()
        if existing and (not link or existing.id != link.id):
            flash(f"IP {ip} ja esta cadastrado.", "error")
            return render_template("dashboards/link_form.html", link=link, link_types=LINK_TYPES)

        bw_raw = request.form.get("bandwidth_mbps", "").strip()
        bw = float(bw_raw) if bw_raw else None

        if link:
            link.name = name
            link.ip = ip
            link.cidr = request.form.get("cidr", ip).strip() or ip
            link.provider = request.form.get("provider", "").strip()
            link.circuit_id = request.form.get("circuit_id", "").strip()
            link.link_type = request.form.get("link_type", "dedicado")
            link.bandwidth_mbps = bw
            link.notes = request.form.get("notes", "").strip()
            link.active = "active" in request.form
        else:
            link = Link(
                name=name,
                ip=ip,
                cidr=request.form.get("cidr", ip).strip() or ip,
                provider=request.form.get("provider", "").strip(),
                circuit_id=request.form.get("circuit_id", "").strip(),
                link_type=request.form.get("link_type", "dedicado"),
                bandwidth_mbps=bw,
                notes=request.form.get("notes", "").strip(),
                active=True,
            )
            db.session.add(link)

        db.session.commit()
        invalidar_cache()
        flash("Link salvo com sucesso.", "success")
        return redirect(url_for("dashboards.links_manager"))

    return render_template("dashboards/link_form.html", link=link, link_types=LINK_TYPES)


@bp.route("/pillars/<string:pillar_id>")
@login_required
@require_role("admin", "gestor", "visualizador")
def pillar_detail(pillar_id: str) -> str:
    """Render drill-down page for a single pillar.

    Args:
        pillar_id: One of the five pillar identifiers.
    """
    if pillar_id not in _VALID_PILLAR_IDS:
        abort(404)

    data = _get_governance()
    pillar = next(
        (p for p in data.get("pillars", []) if p["id"] == pillar_id),
        None,
    )
    if pillar is None:
        abort(404)

    return render_template("dashboards/pillar_detail.html", pillar=pillar, governance=data)


@bp.route("/pmo")
@login_required
@require_role("admin", "gestor", "visualizador")
def pmo_dashboard() -> str:
    """Render PMO — Projetos de TI (ClickUp + score manual)."""
    from itgov.api.v1.pmo_clickup import get_cached_pmo

    data = get_cached_pmo()
    return render_template("dashboards/pmo_dashboard.html", data=data)


@bp.route("/relatorios")
@login_required
@require_role("admin", "gestor")
def relatorios() -> str:
    """Render Relatórios — resumo consolidado de governança."""
    from itgov.api.v1.pmo_clickup import get_cached_pmo

    pmo = get_cached_pmo()
    gov = _get_governance()
    return render_template("dashboards/relatorios.html", pmo=pmo, governance=gov)


@bp.route("/licenses")
@login_required
@require_role("admin", "gestor")
def m365_licenses() -> str:
    """Render painel de Licenças M365 — uso vs disponível + custos manuais."""
    from itgov.api.v1.m365_licenses import get_licenses_summary

    data = get_licenses_summary()
    return render_template("dashboards/m365_licenses.html", data=data)


@bp.route("/licenses/update", methods=["POST"])
@login_required
@require_role("admin", "gestor")
def m365_licenses_update():
    """Salvar custo/renovação de uma SKU (JSON POST)."""

    from flask import jsonify, request

    from itgov.api.v1.m365_licenses import _load_costs, save_costs

    try:
        payload = request.get_json(force=True)
        sku = payload.get("sku_name", "")
        if not sku:
            return jsonify({"ok": False, "error": "sku_name obrigatório"}), 400
        costs = _load_costs()
        if sku not in costs:
            costs[sku] = {}
        costs[sku]["cost_per_unit_brl"] = float(payload.get("cost_per_unit_brl", 0))
        costs[sku]["renewal_date"] = str(payload.get("renewal_date", ""))
        costs[sku]["billing_cycle"] = str(payload.get("billing_cycle", "monthly"))
        costs[sku]["notes"] = str(payload.get("notes", ""))
        if "friendly_name" in payload:
            costs[sku]["friendly_name"] = str(payload["friendly_name"])
        save_costs(costs)
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.route("/triggers")
@login_required
@require_role("admin", "gestor", "operador")
def zabbix_triggers() -> str:
    """Render painel de Triggers Zabbix — problemas ativos."""
    from itgov.api.v1.zabbix_triggers import get_cached_triggers

    if not os.getenv("ZABBIX_URL"):
        abort(404)

    data = get_cached_triggers()
    return render_template("dashboards/zabbix_triggers.html", data=data)


@bp.route("/m365")
@login_required
@require_role("admin", "gestor")
def m365_overview() -> str:
    """Render painel de Governança M365 — KPIs, pilares, checklist e consoles."""
    from app.services.influxdb_provider import InfluxDBMetricsProvider
    from itgov.api.v1.governance_security_alerts import get_cached_security_alerts_summary
    from itgov.api.v1.m365_licenses import get_licenses_summary
    from itgov.api.v1.zabbix_triggers import get_cached_triggers
    from itgov.services.dns_check_service import get_email_security_summary

    provider = InfluxDBMetricsProvider()

    # Secure Score
    ss_rows = provider._query(f"""
from(bucket: "{provider._bucket_raw}")
  |> range(start: -7d)
  |> filter(fn: (r) => r._measurement == "gov_m365_secure_score")
  |> last()
  |> keep(columns: ["_field", "_value"])
""")
    secure_score: dict = {r["_field"]: r["_value"] for r in ss_rows}

    # Entra / MFA
    entra_rows = provider._query(f"""
from(bucket: "{provider._bucket_raw}")
  |> range(start: -24h)
  |> filter(fn: (r) => r._measurement == "gov_entra_summary")
  |> last()
  |> keep(columns: ["_field", "_value"])
""")
    entra: dict = {r["_field"]: r["_value"] for r in entra_rows}

    # Licenses
    lic = get_licenses_summary()

    # Triggers
    triggers = get_cached_triggers()

    # Security Alerts Defender (>24h)
    security_alerts = get_cached_security_alerts_summary()

    # DNS check — DMARC / SPF / DKIM
    _tenant_domain = os.getenv("M365_TENANT_DOMAIN", "")
    dns_check = get_email_security_summary(_tenant_domain) if _tenant_domain else {}

    # Soft-deleted mailboxes (COBIT BAI09)
    mb_rows = provider._query(f"""
from(bucket: "{provider._bucket_raw}")
  |> range(start: -25h)
  |> filter(fn: (r) => r._measurement == "gov_exchange_mailbox")
  |> last()
  |> keep(columns: ["_field", "_value"])
""")
    mailbox: dict = {r["_field"]: r["_value"] for r in mb_rows}

    return render_template(
        "dashboards/m365_overview.html",
        secure_score=secure_score,
        entra=entra,
        licenses=lic,
        triggers=triggers,
        mailbox=mailbox,
        security_alerts=security_alerts,
        dns_check=dns_check,
    )


@bp.route("/triggers/<string:eventid>/ack", methods=["POST"])
@login_required
@require_role("admin", "gestor", "operador")
def zabbix_trigger_ack(eventid: str):
    """Acknowledge um problema no Zabbix."""
    from flask import jsonify, request

    from itgov.api.v1.zabbix_triggers import ack_problem

    payload = request.get_json(force=True) or {}
    ok = ack_problem(eventid, message=payload.get("message", ""))
    return (jsonify({"ok": True}) if ok else jsonify({"ok": False, "error": "Zabbix ack falhou"}), 200 if ok else 500)
