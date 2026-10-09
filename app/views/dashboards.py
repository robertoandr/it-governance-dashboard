"""HTML view routes for the governance dashboard."""

from __future__ import annotations

import asyncio
import os
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID
from zoneinfo import ZoneInfo

import structlog
from flask import Blueprint, Response, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from werkzeug.exceptions import ServiceUnavailable

from app.auth.rbac import require_role
from app.integrations import graph_configured, zendesk_configured
from app.permissoes import primeira_pagina
from app.services.aprovacoes import requer_aprovacao
from app.services.metrics_aggregator import MetricsAggregator

if TYPE_CHECKING:
    from app.models.unidade import DvrUnidade

log = structlog.get_logger(__name__)


def _resumo_gravador_unidade() -> str:
    from app.models.unidade import Unidade

    raw = request.form.get("unidade_id", "").strip()
    unidade = db_get(Unidade, int(raw)) if raw.isdigit() else None
    destino = unidade.caminho if unidade else "sem unidade"
    return f"CFTV: gravador {request.form.get('gravador', '')} → {destino}"


def _resumo_unidade(unidade_id: int | None = None) -> str:
    from app.models.unidade import Unidade

    atual = db_get(Unidade, unidade_id) if unidade_id else None
    if atual and request.form.get("action") == "delete":
        return f"Unidades: excluir {atual.caminho}"
    nome = request.form.get("nome", "").strip()
    return f"Unidades: editar {atual.caminho} (nome: {nome})" if atual else f"Unidades: criar {nome}"


def _resumo_link(link_id: int | None = None) -> str:
    acao = request.form.get("action", "save")
    alvo = request.form.get("name", "").strip() or request.form.get("ip", "").strip() or f"#{link_id}"
    if acao == "delete":
        return f"Links WAN: remover link #{link_id}"
    return f"Links WAN: {'editar' if link_id else 'criar'} {alvo}"


def db_get(model: type, ident: int) -> object | None:
    """``db.session.get`` sem importar ``db`` no topo do módulo."""
    from app.extensions import db

    return db.session.get(model, ident)


_TZ_LOCAL = ZoneInfo("America/Sao_Paulo")

bp = Blueprint("dashboards", __name__, template_folder="../templates")


@bp.context_processor
def _inject_globals() -> dict:
    """Provide app_version and environment when running inside legacy app.py."""
    from flask import current_app

    return {
        "app_version": current_app.config.get("APP_VERSION", "0.0.0"),
        "app_name": current_app.config.get("APP_NAME", "Governança de TI 360"),
        "environment": current_app.config.get("APP_ENVIRONMENT", "production"),
    }


_VALID_PILLAR_IDS = {
    "strategic_alignment",
    "value_delivery",
    "risk_management",
    "resource_management",
    "performance_measure",
}


def _last_data_update(pillars: list[dict]) -> str | None:
    """Return the newest real collection time among pillars, in local time.

    ``computed_at`` is always "now" (the score is recomputed per request), so it
    says nothing about data freshness; ``last_collected`` is the ``_time`` of
    the newest InfluxDB point feeding each pillar.

    Args:
        pillars: Serialized ``PillarScore`` dicts.

    Returns:
        ``"dd/mm/aaaa hh:mm"`` in America/Sao_Paulo, or ``None`` when no pillar
        has live data.
    """
    stamps: list[datetime] = []
    for p in pillars:
        raw = p.get("last_collected")
        if not raw:
            continue
        try:
            stamps.append(datetime.fromisoformat(str(raw).replace("Z", "+00:00")))
        except ValueError:
            log.warning("last_collected_invalido", pillar=p.get("id"), value=raw)
    if not stamps:
        return None
    return max(stamps).astimezone(_TZ_LOCAL).strftime("%d/%m/%Y %H:%M")


def _get_governance() -> dict:
    aggregator = MetricsAggregator()
    governance = asyncio.run(aggregator.calculate_full_score())
    data = governance.model_dump(mode="json")
    data["provider_name"] = aggregator.provider_name
    data["last_data_update"] = _last_data_update(data["pillars"])
    return data


@bp.route("/dashboard")
@login_required
@require_role("admin", "gestor", "visualizador", pagina="visao_geral")
def dashboard_redirect():
    from flask import redirect, url_for

    return redirect(url_for("dashboards.overview"))


@bp.route("/")
@login_required
def overview() -> str | Response:
    """Render governance overview dashboard.

    ``?atualizar=1`` (botão "Atualizar") confere as fontes na hora e volta para
    a página limpa — recarregar depois não força a checagem de novo. Quem não
    vê a Visão Geral (é a página de entrada após o login) vai para a primeira
    página do menu que pode ver.
    """
    from app.services import fontes_status

    if not current_user.pode("visao_geral", "ver", ("admin", "gestor", "visualizador")):
        return redirect(url_for(primeira_pagina(current_user)))

    if request.args.get("atualizar"):
        fontes_status.atualizar_agora()
        return redirect(url_for("dashboards.overview"))

    data = _get_governance()
    return render_template(
        "dashboards/overview.html",
        governance=data,
        fontes=fontes_status.status_fontes(),
        pagina_atualizada=datetime.now(_TZ_LOCAL).strftime("%d/%m/%Y %H:%M:%S"),
    )


@bp.route("/pillars")
@login_required
@require_role("admin", "gestor", "visualizador", pagina="pilares")
def pillars() -> str:
    """Render all pillars detail page."""
    from app.services.plano_melhoria import montar_plano

    data = _get_governance()
    # Ações sem os itens concretos (rápido); os itens ficam na página do pilar.
    planos = {p["id"]: montar_plano(p, com_itens=False) for p in data["pillars"]}
    proxima = {pid: next(iter(acoes), None) for pid, acoes in planos.items()}
    return render_template("dashboards/pillars.html", governance=data, proxima=proxima, planos=planos)


@bp.route("/pilares")
@login_required
def pilares_redirect():
    """Alias em português de /pillars (G-03) — mesma página, mesma rota nomeada."""
    return redirect(url_for("dashboards.pillars"))


@bp.route("/sla")
@login_required
@require_role("admin", "gestor", pagina="sla")
def sla_chamados() -> str:
    """Render painel SLA / Chamados (Zendesk)."""
    from itgov.api.v1.zendesk import get_cached_historico, get_cached_sla_detail

    if not zendesk_configured():
        abort(404)

    data = get_cached_sla_detail()
    return render_template("dashboards/sla_chamados.html", data=data, historico=get_cached_historico())


@bp.route("/zendesk")
@login_required
@require_role("admin", "gestor", pagina="zendesk")
def zendesk_mttr() -> str:
    """Render Zendesk MTTR / suporte dashboard."""
    from itgov.api.v1.zendesk import get_cached_historico, get_cached_mttr_summary, get_cached_volume_by_status

    if not zendesk_configured():
        abort(404)

    mttr = get_cached_mttr_summary()
    volume = get_cached_volume_by_status()

    return render_template(
        "dashboards/zendesk_mttr.html",
        mttr=mttr,
        volume=volume,
        historico=get_cached_historico(),
    )


@bp.route("/governance/devices")
@login_required
@require_role("admin", "gestor", pagina="dispositivos")
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
@require_role("admin", "gestor", pagina="aplicativos")
def governance_apps() -> str:
    """Render pilar Aplicativos (Governança M365)."""
    from itgov.api.v1.governance_apps import get_cached_app_summary
    from itgov.services.m365_uso import obter_uso

    if not graph_configured():
        abort(404)

    try:
        summary = get_cached_app_summary()
    except RuntimeError as exc:
        raise ServiceUnavailable() from exc

    return render_template("dashboards/governance_apps.html", summary=summary, uso_apps=obter_uso())


@bp.route("/governance/compliance")
@login_required
@require_role("admin", "gestor", pagina="compliance")
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
@require_role("admin", "gestor", pagina="dados")
def governance_data() -> str:
    """Render pilar Dados (Sensitivity Labels) — Governança M365."""
    from itgov.api.v1.governance_data import get_cached_data_summary

    if not graph_configured():
        abort(404)

    summary = get_cached_data_summary()
    return render_template("dashboards/governance_data.html", summary=summary)


@bp.route("/governance/security-alerts")
@login_required
@require_role("admin", "gestor", pagina="alertas_defender")
def governance_security_alerts() -> str:
    """Render pilar Endpoint — Alertas de Segurança (Defender, KPI-END-01)."""
    from itgov.api.v1.governance_security_alerts import get_cached_security_alerts_summary

    if not graph_configured():
        abort(404)

    summary = get_cached_security_alerts_summary()
    return render_template("dashboards/governance_security_alerts.html", summary=summary)


@bp.route("/governance/service-health")
@login_required
@require_role("admin", "gestor", pagina="m365")
def governance_service_health() -> str:
    """Render Service Health M365 — status dos serviços do tenant."""
    from itgov.api.v1.governance_service_health import get_cached_service_health_summary

    if not graph_configured():
        abort(404)

    summary = get_cached_service_health_summary()
    return render_template("dashboards/governance_service_health.html", summary=summary)


@bp.route("/backup")
def backup_redirect() -> Response:
    """Endereço antigo: a página passou a se chamar Cibersegurança."""
    return redirect(url_for("dashboards.acronis_backup"), code=301)


@bp.route("/ciberseguranca")
@login_required
@require_role("admin", "gestor", pagina="ciberseguranca")
def acronis_backup() -> str:
    """Render painel de Cibersegurança (proteção/backup Acronis)."""
    from itgov.api.v1.acronis_backup import get_cached_acronis_summary

    if not os.getenv("ACRONIS_BASE_URL"):
        abort(404)

    data = get_cached_acronis_summary()
    return render_template("dashboards/acronis_backup.html", data=data)


@bp.route("/zabbix")
@login_required
@require_role("admin", "gestor", "operador", pagina="zabbix")
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
@require_role("admin", "gestor", "operador", pagina="infra")
def infra_monitoring() -> str:
    """Render painel de Infraestrutura (servidores, VMs, firewall, etc.)."""
    from itgov.api.v1.infra_monitoring import get_cached_infra_summary

    if not os.getenv("ZABBIX_URL"):
        abort(404)

    data = get_cached_infra_summary()
    return render_template("dashboards/infra_monitoring.html", data=data)


@bp.route("/infra/temperatura-diaria.json")
@login_required
@require_role("admin", "gestor", "operador", pagina="infra")
def infra_temperatura_diaria():
    """Resumo diário da temperatura do datacenter (JSON, carregado pela página /infra)."""
    from flask import jsonify

    from itgov.api.v1.datacenter_temp_diario import get_cached_temp_diaria

    dias = min(max(request.args.get("dias", 30, type=int), 1), 90)
    return jsonify(get_cached_temp_diaria(dias))


@bp.route("/cftv")
@login_required
@require_role("admin", "gestor", "operador", pagina="cftv")
def cftv_monitoring() -> str:
    """Render painel CFTV: cards por gravador (DVR/NVR), filtro por unidade."""
    from app.models.unidade import DvrUnidade, Unidade
    from itgov.api.v1.cftv_monitoring import (
        DIAS_HISTORICO,
        SEM_UNIDADE,
        formatar_duracao,
        get_cached_cftv_summary,
        get_cached_historico_quedas,
        montar_visao,
    )
    from itgov.services.cftv_snmp import AUTH_PROTOCOLOS, NIVEIS, PRIV_PROTOCOLOS

    if not os.getenv("ZABBIX_URL"):
        abort(404)

    todas = Unidade.query.all()
    unidades = [u for u in todas if u.ativo]
    nomes = {u.id: u.caminho for u in todas}
    filtro_raw = request.args.get("unidade", "")
    filtro = _filtro_unidade(filtro_raw, unidades, SEM_UNIDADE)

    data = get_cached_cftv_summary()
    vinculos = DvrUnidade.query.all()
    # Sede = unidade raiz; o 1º nível do drill-down soma câmeras por sede
    sede_por_unidade = {u.id: (u.parent_id or u.id) for u in todas}
    visao = montar_visao(
        data,
        unidade_por_gravador={v.dvr: v.unidade_id for v in vinculos},
        unidades=nomes,
        unidade_por_loja={u.nome.lower(): u.id for u in unidades},
        filtro=filtro,
        apelidos={v.dvr: v.apelido for v in vinculos if v.apelido},
        duplicado_de={v.dvr: v.duplicado_de for v in vinculos if v.duplicado_de},
        sede_por_unidade=sede_por_unidade,
        quedas=get_cached_historico_quedas(),
    )
    selecionada = next((u for u in todas if filtro_raw == str(u.id)), None)
    sede = (selecionada.parent or selecionada) if selecionada else None
    # Sem filtro: só os cards de sede, a não ser que peça todos os gravadores
    ver_gravadores = filtro is not None or request.args.get("ver") == "gravadores"
    return render_template(
        "dashboards/cftv_monitoring.html",
        data=data,
        visao=visao,
        unidades=sorted(unidades, key=lambda u: u.caminho),
        sedes=sorted((u for u in unidades if u.parent_id is None), key=lambda u: u.nome),
        sede=sede,
        filhas=sorted((f for f in sede.filhas if f.ativo), key=lambda u: u.nome) if sede else [],
        selecionada=selecionada,
        ver_gravadores=ver_gravadores,
        niveis_snmp=NIVEIS,
        auth_snmp=AUTH_PROTOCOLOS,
        priv_snmp=PRIV_PROTOCOLOS,
        gravadores=sorted({c["gravador"] for c in visao["cards"] if c["gravador"]} | {v.dvr for v in vinculos}),
        filtro=filtro_raw if filtro is not None else "",
        dias_historico=DIAS_HISTORICO,
        agora=time.time(),
        duracao=formatar_duracao,
    )


def _voltar_cftv() -> object:
    """Redireciona de volta para /cftv mantendo o filtro do formulário."""
    return redirect(url_for("dashboards.cftv_monitoring", unidade=request.form.get("filtro") or None))


def _vinculo_dvr(gravador: str) -> DvrUnidade:
    """Vínculo do gravador, criado (sem unidade) se ainda não existir."""
    from app.extensions import db
    from app.models.unidade import DvrUnidade

    vinculo = DvrUnidade.query.filter_by(dvr=gravador).first()
    if vinculo is None:
        vinculo = DvrUnidade(dvr=gravador)
        db.session.add(vinculo)
    return vinculo


@bp.route("/cftv/gravador/nome", methods=["POST"])
@login_required
@require_role("admin", pagina="cftv")
@requer_aprovacao(
    lambda: (
        f"CFTV: renomear gravador {request.form.get('gravador', '')} para “{request.form.get('apelido', '').strip() or 'nome do Zabbix'}”"
    )
)
def cftv_gravador_nome() -> object:
    """Renomeia um gravador na página /cftv (só admin; vazio volta ao nome do Zabbix)."""
    from app.extensions import db

    gravador = request.form.get("gravador", "").strip()
    apelido = " ".join(request.form.get("apelido", "").split())
    if not gravador or len(apelido) > 120:
        abort(400)
    vinculo = _vinculo_dvr(gravador)
    vinculo.apelido = apelido
    db.session.commit()
    log.info("cftv.gravador_nome", gravador=gravador, apelido=apelido, user=current_user.email)
    flash(
        f"{gravador} agora aparece como “{apelido}”." if apelido else f"{gravador} voltou ao nome do Zabbix.", "success"
    )
    return _voltar_cftv()


def _resumo_snmp() -> str:
    f = request.form
    versao = "v2c" if f.get("snmp_versao") == "2" else "v3"
    if f.get("novo"):
        return f"CFTV: cadastrar gravador novo {f.get('gravador', '')} — {f.get('snmp_ip', '')}, {versao}"
    return f"CFTV: SNMP do gravador {f.get('gravador', '')} — {f.get('snmp_ip', '')}, {versao}"


@bp.route("/cftv/gravador/snmp", methods=["POST"])
@login_required
@require_role("admin", pagina="cftv")
@requer_aprovacao(_resumo_snmp)
def cftv_gravador_snmp() -> object:
    """Grava IP e SNMP (v2c/v3) de um gravador no Zabbix; cria o host se não existir (só admin).

    As senhas vão direto para macros secretas do host no Zabbix; em branco,
    mantém as que já estão lá. Com ``novo`` (formulário "Cadastrar novo
    gravador"), recusa nome que já existe e grava a unidade escolhida.
    """
    import requests
    from pydantic import ValidationError

    from app.extensions import db
    from app.models.unidade import Unidade
    from itgov.api.v1.cftv_monitoring import invalidar_cache_cftv
    from itgov.services.cftv_snmp import ConfigSnmp, GravadorJaExisteError, aplicar

    f = request.form
    gravador = f.get("gravador", "").strip()
    novo = bool(f.get("novo"))
    if novo and not gravador:
        flash("Informe o nome do gravador novo.", "error")
        return _voltar_cftv()
    if not gravador or len(gravador) > 120:
        abort(400)
    unidade_raw = f.get("unidade_id", "").strip()
    unidade_id = int(unidade_raw) if novo and unidade_raw.isdigit() else None
    if unidade_id is not None:
        alvo = db.session.get(Unidade, unidade_id)
        if alvo is None or not alvo.ativo:
            abort(400)
    try:
        cfg = ConfigSnmp(
            ip=f.get("snmp_ip", "").strip(),
            porta=f.get("snmp_porta") or 161,
            versao=f.get("snmp_versao", ""),
            community=f.get("snmp_community", ""),
            usuario=f.get("snmp_usuario", ""),
            nivel=f.get("snmp_nivel") or 2,
            auth_protocolo=f.get("snmp_auth_protocolo") or 1,
            auth_senha=f.get("snmp_auth_senha", ""),
            priv_protocolo=f.get("snmp_priv_protocolo") or 1,
            priv_senha=f.get("snmp_priv_senha", ""),
        )
    except ValidationError as exc:
        motivos = "; ".join(str(e["msg"]).removeprefix("Value error, ") for e in exc.errors())
        flash(f"SNMP de {gravador} não foi salvo: {motivos}.", "error")
        return _voltar_cftv()
    try:
        res = aplicar(gravador, cfg, so_criar=novo)
    except GravadorJaExisteError:
        flash(f"Já existe um gravador {gravador} no Zabbix: ajuste o SNMP no card dele.", "error")
        return _voltar_cftv()
    except (RuntimeError, requests.RequestException) as exc:
        log.warning("cftv.gravador_snmp_falhou", gravador=gravador, erro=str(exc)[:200])
        flash(f"O Zabbix recusou o SNMP de {gravador}: {exc}", "error")
        return _voltar_cftv()
    if unidade_id is not None:
        _vinculo_dvr(gravador).unidade_id = unidade_id
        db.session.commit()
    invalidar_cache_cftv()
    log.info("cftv.gravador_snmp", gravador=gravador, criado=res.criado, versao=cfg.versao, user=current_user.email)
    msg = f"{gravador} {'cadastrado no Zabbix' if res.criado else 'atualizado no Zabbix'} ({cfg.ip}, SNMP v{'2c' if cfg.versao == '2' else '3'})."
    if res.itens_verificados:
        msg += f" Pedi a checagem de {res.itens_verificados} itens SNMP agora; o status aparece em até 1 minuto."
    flash(msg, "success")
    if res.senhas_faltando:
        flash(f"Falta informar: {', '.join(res.senhas_faltando)} — sem isso o Zabbix usa a credencial global.", "info")
    return _voltar_cftv()


@bp.route("/cftv/gravador/duplicado", methods=["POST"])
@login_required
@require_role("admin", pagina="cftv")
@requer_aprovacao(
    lambda: (
        f"CFTV: unir gravador {request.form.get('gravador', '')} a {request.form.get('duplicado_de')}"
        if request.form.get("duplicado_de")
        else f"CFTV: separar gravador {request.form.get('gravador', '')}"
    )
)
def cftv_gravador_duplicado() -> object:
    """Marca um gravador como duplicado de outro (só admin; vazio desfaz).

    O duplicado deixa de ter card próprio: seus dispositivos entram no card do
    principal. Nada é apagado no Zabbix.
    """
    from app.extensions import db
    from app.models.unidade import DvrUnidade
    from itgov.api.v1.cftv_monitoring import resolver_principal

    gravador = request.form.get("gravador", "").strip()
    principal = request.form.get("duplicado_de", "").strip()
    if not gravador or len(principal) > 120:
        abort(400)
    if principal:
        mapa = {v.dvr: v.duplicado_de for v in DvrUnidade.query.all() if v.duplicado_de}
        mapa[gravador] = principal
        # Recusa A→A e ciclos (A→B→A): o principal não pode levar de volta a este
        if resolver_principal(principal, mapa) == gravador or principal == gravador:
            flash("Não dá para unir: o principal escolhido já é duplicado deste gravador.", "error")
            return _voltar_cftv()
    vinculo = _vinculo_dvr(gravador)
    vinculo.duplicado_de = principal
    db.session.commit()
    log.info("cftv.gravador_duplicado", gravador=gravador, duplicado_de=principal, user=current_user.email)
    if principal:
        flash(f"{gravador} foi unido a {principal}.", "success")
    else:
        flash(f"{gravador} voltou a ter card próprio.", "success")
    return _voltar_cftv()


@bp.route("/cftv/gravador", methods=["POST"])
@login_required
@require_role("admin", "gestor", pagina="cftv")
@requer_aprovacao(_resumo_gravador_unidade)
def cftv_gravador_unidade() -> object:
    """Define a unidade de um gravador (DVR/NVR) a partir do card na página /cftv."""
    from app.extensions import db
    from app.models.unidade import DvrUnidade, Unidade

    gravador = request.form.get("gravador", "").strip()
    unidade_raw = request.form.get("unidade_id", "").strip()
    if not gravador:
        abort(400)
    unidade_id = int(unidade_raw) if unidade_raw.isdigit() else None
    if unidade_id is not None:
        alvo = db.session.get(Unidade, unidade_id)
        # Inativa não aparece no seletor do card: o próximo "Salvar" apagaria o vínculo
        if alvo is None or not alvo.ativo:
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
@require_role("admin", "gestor", "operador", pagina="unidades")
def unidades_list() -> str:
    """Lista as unidades (sites) em árvore, com CNPJ, endereço e faixas de IP."""
    from app.models.unidade import Unidade

    raizes = Unidade.query.filter_by(parent_id=None).order_by(Unidade.nome).all()
    return render_template("dashboards/unidades.html", raizes=raizes)


@bp.route("/unidades/nova", methods=["GET", "POST"])
@bp.route("/unidades/<int:unidade_id>/editar", methods=["GET", "POST"])
@login_required
@require_role("admin", "gestor", pagina="unidades", nivel="alterar")
@requer_aprovacao(_resumo_unidade)
def unidade_form(unidade_id: int | None = None) -> object:
    """Formulário de criação/edição de unidade (nome, pai, faixas de IP, endereço e logo)."""
    from app.extensions import db
    from app.models.unidade import (
        LOGO_MAX_BYTES,
        UFS,
        DvrUnidade,
        Unidade,
        normalizar_cep,
        normalizar_cnpj,
        normalizar_uf,
        parse_faixas,
        tipo_logo,
    )

    unidade = db.session.get(Unidade, unidade_id) if unidade_id else None
    if unidade_id and unidade is None:
        abort(404)
    # Hierarquia de dois níveis: só raízes podem ser pai, e nunca a própria unidade
    pais = [
        u for u in Unidade.query.filter_by(parent_id=None).order_by(Unidade.nome) if not unidade or u.id != unidade.id
    ]

    def _render() -> str:
        return render_template("dashboards/unidade_form.html", unidade=unidade, pais=pais, ufs=sorted(UFS))

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
    arquivo = request.files.get("logo")
    try:
        faixas = parse_faixas(request.form.get("faixas_ip", ""))
        cnpj = normalizar_cnpj(request.form.get("cnpj", ""))
        cep = normalizar_cep(request.form.get("cep", ""))
        uf = normalizar_uf(request.form.get("uf", ""))
        # Um byte além do limite basta para tipo_logo() recusar o arquivo.
        logo = arquivo.read(LOGO_MAX_BYTES + 1) if arquivo and arquivo.filename else None
        logo_mime = tipo_logo(logo) if logo is not None else None
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
    unidade.cnpj = cnpj
    unidade.endereco = request.form.get("endereco", "").strip()[:200]
    unidade.cep = cep
    unidade.cidade = request.form.get("cidade", "").strip()[:120]
    unidade.uf = uf
    if logo is not None:
        unidade.logo, unidade.logo_mime = logo, logo_mime
    elif "remover_logo" in request.form:
        unidade.logo, unidade.logo_mime = None, None
    unidade.ativo = "ativo" in request.form or unidade_id is None
    db.session.commit()
    log.info("unidade.salva", unidade=unidade.caminho, faixas=len(faixas), user=current_user.email)
    flash("Unidade salva.", "success")
    return redirect(url_for("dashboards.unidades_list"))


@bp.route("/unidades/<int:unidade_id>/logo")
@login_required
@require_role("admin", "gestor", "operador", pagina="unidades")
def unidade_logo(unidade_id: int) -> Response:
    """Serve o logo da unidade guardado no banco (404 quando não há)."""
    from app.extensions import db
    from app.models.unidade import Unidade

    unidade = db.session.get(Unidade, unidade_id)
    if unidade is None or not unidade.logo or not unidade.logo_mime:
        abort(404)
    resp = Response(unidade.logo, mimetype=unidade.logo_mime)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Cache-Control"] = "private, max-age=86400"
    return resp


# Mapa de Câmeras (serviço nativo mapa-cameras.service, porta 8080) é servido
# pelo nginx em /mapa-cameras/, protegido por auth_request contra a rota abaixo.
MAPA_CAMERAS_ROLES: tuple[str, ...] = ("admin", "gestor")
MAPA_CAMERAS_PROXY_PATH = "/mapa-cameras/"


@bp.route("/cameras")
@login_required
@require_role(*MAPA_CAMERAS_ROLES, pagina="mapa_cameras")
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
        204 quando o usuário logado pode ver o Mapa de Câmeras (perfil ou
        ajuste de permissão), 401 sem sessão, 403 sem permissão.
    """
    if not current_user.is_authenticated:
        return "", 401
    if not current_user.pode("mapa_cameras", "ver", MAPA_CAMERAS_ROLES):
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
@require_role("admin", "gestor", "operador", pagina="rede")
def rede_monitoring() -> str:
    """Render painel de rede: fila de revisão dos hosts descobertos pelo nmap."""
    from app.models.unidade import Unidade
    from itgov.api.v1.rede_descoberta import SIGLA_TIPO, faixas_fora_da_varredura, resumo_por_unidade, sigla_unidade
    from itgov.api.v1.rede_monitoring import (
        SEM_UNIDADE,
        STATUS_CADASTRADO,
        STATUS_NOVO,
        STATUS_RECENTE,
        get_cached_rede_summary,
        get_latencia,
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
    if status not in (STATUS_NOVO, STATUS_CADASTRADO, STATUS_RECENTE):
        status = ""

    data = get_cached_rede_summary()
    ativos_por_ip = {a["metadata"]["ip"]: a for a in _listar_ativos() if a["metadata"].get("ip")}
    faixas = [(u.id, f) for u in unidades for f in u.faixas]
    nomes = {u.id: u.caminho for u in todas}
    revisao = montar_descobertos(
        data.get("hosts", []),
        faixas=faixas,
        ativos_por_ip=ativos_por_ip,
        unidades=nomes,
        filtro_unidade=filtro,
        filtro_status=status,
    )
    return render_template(
        "dashboards/rede_monitoring.html",
        data=data,
        revisao=revisao,
        cards=resumo_por_unidade(data.get("hosts", []), faixas, nomes, set(ativos_por_ip)),
        latencia=get_latencia(faixas, nomes),
        faixas_fora=faixas_fora_da_varredura(
            [(u.caminho, f) for u in unidades for f in u.faixas],
            [r for dr in data.get("active_drules", []) for r in dr["ranges"]],
        ),
        siglas=[(u.nome, sigla_unidade(u.nome)) for u in unidades if u.parent_id is None],
        sigla_tipo=SIGLA_TIPO,
        unidades=sorted(unidades, key=lambda u: u.caminho),
        filtro=filtro_raw if filtro is not None else "",
        status=status,
        tipo_labels=TIPO_LABELS,
        sem_faixas=not any(u.faixas for u in unidades),
    )


@bp.route("/rede/cadastrar", methods=["GET", "POST"])
@login_required
@require_role("admin", "gestor", pagina="rede", nivel="alterar")
@requer_aprovacao(lambda: f"Rede: cadastrar ativo {request.form.get('ip') or request.args.get('ip', '')}")
def rede_cadastrar_ativo() -> object:
    """Cadastra um host descoberto como ativo de rede, já com tipo e unidade sugeridos."""
    from sqlalchemy import select

    from app.models.unidade import Unidade
    from itgov.api.v1.rede_descoberta import sigla_unidade, sugerir_nome
    from itgov.api.v1.rede_monitoring import host_descoberto, unidade_do_ip
    from itgov.db.session import get_session
    from itgov.models.ativo import AMBIENTES_VALIDOS, CRITICIDADES_VALIDAS, TIPO_LABELS
    from itgov.models.db.ativo import AtivoDB
    from itgov.services.ativo_service import AtivoDuplicateError, AtivoService

    ip = (request.values.get("ip") or "").strip()
    host = host_descoberto(ip) if ip else None
    if host is None:
        abort(404)
    ativos = _listar_ativos()
    if any(a["metadata"].get("ip") == ip for a in ativos):
        flash(f"{ip} já está cadastrado como ativo.", "error")
        return redirect(url_for("dashboards.ativos_rede"))

    unidades = sorted((u for u in Unidade.query.all() if u.ativo), key=lambda u: u.caminho)
    tipo = host["tipo_sugerido"] if host["tipo_sugerido"] in TIPO_LABELS else "outro"
    unidade_id = unidade_do_ip(ip, [(u.id, f) for u in unidades for f in u.faixas])
    unidade_ip = next((u for u in unidades if u.id == unidade_id), None)
    # Nome no padrão SIGLA-TIPO-NNN (sigla da unidade raiz); o nome visto na rede vai para a descrição
    raiz = unidade_ip.caminho.split(" / ")[0] if unidade_ip else ""
    visto = host.get("ia_nome") or (host["hostname"] if host["hostname"] != ip else "")
    sugestao = {
        "nome": sugerir_nome(tipo, sigla_unidade(raiz), (a["nome"] for a in ativos)),
        "tipo": tipo,
        "unidade_id": unidade_id,
        "criticidade": "media",
        "ambiente": "prod",
        "descricao": visto or "",
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
@require_role("admin", "gestor", "operador", pagina="ativos_rede")
def ativos_rede() -> str:
    """Topologia da rede e ativos do inventário separados por unidade, com filtro por unidade e tipo."""
    from app.models.unidade import Unidade
    from itgov.api.v1.rede_monitoring import SEM_UNIDADE, get_cached_rede_summary
    from itgov.api.v1.rede_topologia import layout_geral, montar_topologia
    from itgov.models.ativo import TIPO_LABELS
    from itgov.services.fortigate_api import configurados, get_cached_fortigates

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
    topologia = montar_topologia(
        get_cached_fortigates() if configurados()[0] else [],
        configurados()[1],
        get_cached_rede_summary().get("hosts", []) if os.getenv("ZABBIX_URL") else [],
    )
    return render_template(
        "dashboards/ativos_rede.html",
        topologia=topologia,
        mapa=layout_geral(topologia),
        secoes=secoes,
        total=sum(len(i) for _, i in secoes),
        unidades=sorted(unidades, key=lambda u: u.caminho),
        filtro=filtro_raw if filtro is not None else "",
        tipo=tipo,
        tipo_labels=TIPO_LABELS,
    )


@bp.route("/ativos-rede/<uuid:ativo_id>/remover", methods=["POST"])
@login_required
@require_role("admin", "gestor", pagina="ativos_rede")
@requer_aprovacao(lambda ativo_id: f"Rede: remover ativo {ativo_id}")
def ativo_rede_remover(ativo_id: UUID) -> object:
    """Remove (soft delete) um ativo do inventário."""
    from itgov.db.session import get_session
    from itgov.services.ativo_service import AtivoNotFoundError, AtivoService

    try:
        with get_session() as session:
            AtivoService(session).delete(ativo_id)
    except AtivoNotFoundError:
        abort(404)
    log.info("rede.ativo_removido", ativo_id=str(ativo_id), user=current_user.email)
    flash("Ativo removido.", "success")
    return redirect(url_for("dashboards.ativos_rede"))


@bp.route("/links")
@login_required
@require_role("admin", "gestor", "operador", pagina="links")
def links_manager() -> str:
    """Render gerenciador de links WAN/Internet."""
    from itgov.api.v1.links_manager import get_cached_links
    from itgov.services.fortigate_api import configurados, get_cached_fortigates
    from itgov.services.fortinet_service import get_cached_fortinet

    data = get_cached_links()
    # Zabbix (template FortiGate by HTTP) + leitura direta pela API; a API vale quando há as duas
    pela_api = get_cached_fortigates()
    nomes_api = {fw["name"] for fw in pela_api}
    fortinet = [fw for fw in get_cached_fortinet() if fw["name"] not in nomes_api] + pela_api
    return render_template(
        "dashboards/links_manager.html",
        data=data,
        fortinet=fortinet,
        fortigate_pendentes=configurados()[1],
    )


@bp.route("/links/novo", methods=["GET", "POST"])
@bp.route("/links/<int:link_id>/editar", methods=["GET", "POST"])
@login_required
@require_role("admin", "gestor", pagina="links", nivel="alterar")
@requer_aprovacao(_resumo_link)
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
@require_role("admin", "gestor", "visualizador", pagina="pilares")
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

    from app.services.plano_melhoria import montar_plano

    return render_template("dashboards/pillar_detail.html", pillar=pillar, governance=data, plano=montar_plano(pillar))


@bp.route("/pmo")
@login_required
@require_role("admin", "gestor", "visualizador", pagina="pmo")
def pmo_dashboard() -> str:
    """Render PMO — Projetos de TI (ClickUp + score manual)."""
    from itgov.api.v1.pmo_clickup import get_cached_pmo

    data = get_cached_pmo()
    return render_template("dashboards/pmo_dashboard.html", data=data)


@bp.route("/relatorios")
@login_required
@require_role("admin", "gestor", pagina="relatorios")
def relatorios() -> str:
    """Render o catálogo de relatórios (executivo, temáticos e personalizado)."""
    from app.services.relatorios import CATALOGO, SECOES

    return render_template("dashboards/relatorios_catalogo.html", catalogo=CATALOGO, secoes=SECOES)


@bp.route("/relatorios/executivo")
@login_required
@require_role("admin", "gestor", pagina="relatorios")
def relatorio_executivo() -> str:
    """Render o relatório executivo — score dos pilares e PMO."""
    from itgov.api.v1.pmo_clickup import get_cached_pmo

    pmo = get_cached_pmo()
    gov = _get_governance()
    return render_template("dashboards/relatorios.html", pmo=pmo, governance=gov)


@bp.route("/relatorios/<chave>")
@login_required
@require_role("admin", "gestor", pagina="relatorios")
def relatorio(chave: str) -> str | Response:
    """Render um relatório do catálogo ou o personalizado (``?secao=``); ``?formato=csv`` baixa o CSV."""
    from app.services.relatorios import CATALOGO, SECOES, gerar_csv, montar_relatorio

    secoes = request.args.getlist("secao")
    if chave not in CATALOGO and chave != "personalizado":
        abort(404)
    if chave == "personalizado" and not any(s in SECOES for s in secoes):
        flash("Escolha pelo menos uma seção para o relatório personalizado.", "error")
        return redirect(url_for("dashboards.relatorios"))
    rel = montar_relatorio(chave, secoes)
    if request.args.get("formato") == "csv":
        nome = f"relatorio-{chave}-{rel.gerado_em:%Y%m%d-%H%M}.csv"
        return Response(
            "\ufeff" + gerar_csv(rel),  # BOM: o Excel abre os acentos certos
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename={nome}"},
        )
    return render_template("dashboards/relatorio.html", rel=rel, secoes_escolhidas=secoes)


@bp.route("/licenses")
@login_required
@require_role("admin", "gestor", pagina="licencas")
def m365_licenses() -> str:
    """Render painel de Licenças M365 — uso vs disponível + custos manuais."""
    from itgov.api.v1.m365_licenses import get_licenses_summary

    data = get_licenses_summary()
    return render_template("dashboards/m365_licenses.html", data=data)


@bp.route("/licenses/update", methods=["POST"])
@login_required
@require_role("admin", "gestor", pagina="licencas")
@requer_aprovacao(
    lambda: f"Licenças: alterar custo/dados de {(request.get_json(silent=True) or {}).get('sku_name', '?')}"
)
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
        # vazio = sem valor de contrato → a tela usa o preço de lista (estimado)
        costs[sku]["cost_per_unit_brl"] = float(payload.get("cost_per_unit_brl") or 0)
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
@require_role("admin", "gestor", "operador", pagina="triggers")
def zabbix_triggers() -> str:
    """Render painel de Triggers Zabbix — abas Em aberto e Resolvidos."""
    from itgov.api.v1.zabbix_triggers import get_cached_resolved, get_cached_triggers

    if not os.getenv("ZABBIX_URL"):
        abort(404)

    data = get_cached_triggers()
    resolved = get_cached_resolved() if data.get("enabled") else {"items": [], "truncated": False, "days": 7}
    return render_template(
        "dashboards/zabbix_triggers.html", data=data, resolved=resolved, **_triggers_abas(data, resolved["items"])
    )


def _fmt_local(dt: datetime) -> str:
    """Formata um datetime do banco (UTC, às vezes sem tzinfo no SQLite) em Brasília."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(_TZ_LOCAL).strftime("%d/%m %H:%M")


def _triggers_abas(data: dict, resolved: list[dict]) -> dict:
    """Separa os problemas em "Em aberto" e "Resolvidos" usando as marcas locais.

    Problema ativo marcado no dashboard vai para Resolvidos com
    ``still_active=True`` (o Zabbix ainda não normalizou); os resolvidos pelo
    Zabbix ganham o autor da marca, quando houver.
    """
    from app.models.trigger_resolucao import TriggerResolucao

    problems: list[dict] = data.get("problems", [])
    ids = [p["eventid"] for p in problems] + [r["eventid"] for r in resolved]
    marcas = (
        {m.eventid: m for m in TriggerResolucao.query.filter(TriggerResolucao.eventid.in_(ids)).all()} if ids else {}
    )

    abertos: list[dict] = []
    marcados: list[dict] = []
    for p in problems:
        marca = marcas.get(p["eventid"])
        if marca is None:
            abertos.append(p)
            continue
        marcados.append(
            {
                **p,
                "still_active": True,
                "resolved_at": _fmt_local(marca.resolvido_em),
                "resolvido_por": marca.resolvido_por,
                "nota": marca.nota,
            }
        )

    resolvidos = marcados + [
        {
            **r,
            "still_active": False,
            "resolvido_por": marcas[r["eventid"]].resolvido_por if r["eventid"] in marcas else "",
            "nota": marcas[r["eventid"]].nota if r["eventid"] in marcas else "",
        }
        for r in resolved
    ]
    return {"abertos": abertos, "resolvidos": resolvidos}


@bp.route("/m365")
@login_required
@require_role("admin", "gestor", pagina="m365")
def m365_overview() -> str:
    """Render painel de Governança M365 — KPIs, pilares, checklist e consoles."""
    from app.services.influxdb_provider import InfluxDBMetricsProvider
    from app.services.m365_governanca import obter_painel
    from itgov.api.v1.governance_security_alerts import get_cached_security_alerts_summary
    from itgov.api.v1.m365_licenses import get_licenses_summary
    from itgov.api.v1.zabbix_triggers import get_cached_triggers
    from itgov.services.dns_check_service import get_email_security_summary
    from itgov.services.m365_uso import obter_uso

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

    painel = obter_painel(lic.get("licenses", []), entra, dns_check, url_for)

    return render_template(
        "dashboards/m365_overview.html",
        painel=painel,
        secure_score=secure_score,
        entra=entra,
        licenses=lic,
        triggers=triggers,
        mailbox=mailbox,
        security_alerts=security_alerts,
        dns_check=dns_check,
        uso_apps=obter_uso() if graph_configured() else False,
    )


@bp.route("/triggers/<string:eventid>/ack", methods=["POST"])
@login_required
@require_role("admin", "gestor", "operador", pagina="triggers")
def zabbix_trigger_ack(eventid: str):
    """Acknowledge um problema no Zabbix."""
    from flask import jsonify, request

    from itgov.api.v1.zabbix_triggers import ack_problem

    payload = request.get_json(force=True) or {}
    ok = ack_problem(eventid, message=payload.get("message", ""))
    return (jsonify({"ok": True}) if ok else jsonify({"ok": False, "error": "Zabbix ack falhou"}), 200 if ok else 500)


@bp.route("/triggers/<string:eventid>/resolve", methods=["POST"])
@login_required
@require_role("admin", "gestor", "operador", pagina="triggers")
def zabbix_trigger_resolve(eventid: str) -> tuple[Response, int]:
    """Marca um problema ativo como resolvido.

    Fecha no Zabbix quando o trigger permite (``manual_close``); senão faz
    ack com mensagem e guarda a marca local, que move o problema para a aba
    Resolvidos.
    """
    from flask import jsonify
    from sqlalchemy.exc import IntegrityError

    from app.extensions import db
    from app.models.trigger_resolucao import TriggerResolucao
    from itgov.api.v1 import zabbix_triggers as zt

    if TriggerResolucao.query.filter_by(eventid=eventid).first() is not None:
        return jsonify({"ok": True}), 200

    payload = request.get_json(silent=True) or {}
    nota = str(payload.get("nota", "")).strip()[:500]

    problema = _problema_ativo(eventid)
    if problema is None:
        return jsonify({"ok": False, "error": "Problema não está mais ativo no Zabbix"}), 404

    mensagem = f"Resolvido via Governança de TI 360 por {current_user.name}"
    if nota:
        mensagem += f": {nota}"
    fechar = bool(problema.get("manual_close"))
    if not zt.resolve_problem(eventid, close=fechar, acknowledged=problema["acknowledged"], message=mensagem):
        return jsonify({"ok": False, "error": "O Zabbix recusou a operação"}), 502

    db.session.add(
        TriggerResolucao(
            eventid=eventid,
            triggerid=problema["triggerid"],
            host=problema["host"],
            name=problema["name"],
            severity=problema["severity"],
            fechado_no_zabbix=fechar,
            nota=nota,
            resolvido_por=current_user.name,
        )
    )
    try:
        db.session.commit()
    except IntegrityError:  # duplo clique: outra requisição gravou primeiro
        db.session.rollback()
    log.info("trigger.resolvido", eventid=eventid, fechado_no_zabbix=fechar, user=current_user.email)
    return jsonify({"ok": True, "fechado_no_zabbix": fechar}), 200


@bp.route("/triggers/<string:eventid>/reopen", methods=["POST"])
@login_required
@require_role("admin", "gestor", "operador", pagina="triggers")
def zabbix_trigger_reopen(eventid: str) -> tuple[Response, int]:
    """Desfaz a marca local de resolvido (volta para Em aberto se ainda ativo)."""
    from flask import jsonify

    from app.extensions import db
    from app.models.trigger_resolucao import TriggerResolucao

    marca = TriggerResolucao.query.filter_by(eventid=eventid).first()
    if marca is None:
        return jsonify({"ok": False, "error": "Problema não estava marcado como resolvido"}), 404
    if marca.fechado_no_zabbix:
        return jsonify({"ok": False, "error": "Fechado no Zabbix — não dá para reabrir por aqui"}), 409
    db.session.delete(marca)
    db.session.commit()
    log.info("trigger.reaberto", eventid=eventid, user=current_user.email)
    return jsonify({"ok": True}), 200


def _problema_ativo(eventid: str) -> dict | None:
    """Relê o Zabbix e devolve o problema ativo (ack/manual_close atuais).

    Ler do cache arriscaria um ``acknowledged`` velho: ack repetido faz o
    Zabbix recusar a operação inteira.
    """
    from itgov.api.v1 import zabbix_triggers as zt

    zt.invalidate_cache()
    return next((p for p in zt.get_cached_triggers().get("problems", []) if p["eventid"] == eventid), None)
