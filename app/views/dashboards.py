"""HTML view routes for the governance dashboard."""

from __future__ import annotations

import asyncio

import structlog
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import login_required

from app.auth.rbac import require_role
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
    import os

    from itgov.api.v1.zendesk import get_cached_sla_detail

    if not os.getenv("ZENDESK_SUBDOMAIN"):
        abort(404)

    data = get_cached_sla_detail()
    return render_template("dashboards/sla_chamados.html", data=data)


@bp.route("/zendesk")
def zendesk_mttr() -> str:
    """Render Zendesk MTTR / suporte dashboard."""
    import os

    from itgov.api.v1.zendesk import get_cached_mttr_summary, get_cached_volume_by_status

    if not os.getenv("ZENDESK_SUBDOMAIN"):
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
    import os

    from itgov.api.v1.governance_devices import get_cached_device_summary

    if not (os.getenv("AZURE_CLIENT_ID") or os.getenv("MSAL_CLIENT_ID")):
        abort(404)

    try:
        summary = get_cached_device_summary()
    except RuntimeError:
        abort(503)

    return render_template("dashboards/governance_devices.html", summary=summary)


@bp.route("/governance/apps")
@login_required
@require_role("admin", "gestor")
def governance_apps() -> str:
    """Render pilar Aplicativos (Governança M365)."""
    import os

    from itgov.api.v1.governance_apps import get_cached_app_summary

    if not (os.getenv("AZURE_CLIENT_ID") or os.getenv("MSAL_CLIENT_ID")):
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
    import os

    from itgov.api.v1.governance_compliance import get_cached_compliance_summary
    from itgov.services.dns_check_service import _get_domain, get_email_security_summary

    if not (os.getenv("AZURE_CLIENT_ID") or os.getenv("MSAL_CLIENT_ID")):
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
    import os

    from itgov.api.v1.governance_data import get_cached_data_summary

    if not (os.getenv("AZURE_CLIENT_ID") or os.getenv("MSAL_CLIENT_ID")):
        abort(404)

    summary = get_cached_data_summary()
    return render_template("dashboards/governance_data.html", summary=summary)


@bp.route("/governance/security-alerts")
@login_required
@require_role("admin", "gestor")
def governance_security_alerts() -> str:
    """Render pilar Endpoint — Alertas de Segurança (Defender, KPI-END-01)."""
    import os

    from itgov.api.v1.governance_security_alerts import get_cached_security_alerts_summary

    if not (os.getenv("AZURE_CLIENT_ID") or os.getenv("MSAL_CLIENT_ID")):
        abort(404)

    summary = get_cached_security_alerts_summary()
    return render_template("dashboards/governance_security_alerts.html", summary=summary)


@bp.route("/governance/service-health")
@login_required
@require_role("admin", "gestor")
def governance_service_health() -> str:
    """Render Service Health M365 — status dos serviços do tenant."""
    import os

    from itgov.api.v1.governance_service_health import get_cached_service_health_summary

    if not (os.getenv("AZURE_CLIENT_ID") or os.getenv("MSAL_CLIENT_ID")):
        abort(404)

    summary = get_cached_service_health_summary()
    return render_template("dashboards/governance_service_health.html", summary=summary)


@bp.route("/backup")
@login_required
@require_role("admin", "gestor")
def acronis_backup() -> str:
    """Render painel de Backup/Proteção Acronis."""
    import os

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
    import os

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
    import os

    from itgov.api.v1.infra_monitoring import get_cached_infra_summary

    if not os.getenv("ZABBIX_URL"):
        abort(404)

    data = get_cached_infra_summary()
    return render_template("dashboards/infra_monitoring.html", data=data)


@bp.route("/cftv")
@login_required
@require_role("admin", "gestor", "operador")
def cftv_monitoring() -> str:
    """Render painel de monitoramento CFTV (câmeras e NVRs)."""
    import os

    from itgov.api.v1.cftv_monitoring import get_cached_cftv_summary

    if not os.getenv("ZABBIX_URL"):
        abort(404)

    data = get_cached_cftv_summary()
    return render_template("dashboards/cftv_monitoring.html", data=data)


@bp.route("/network")
@login_required
def network_redirect():
    return redirect(url_for("dashboards.rede_monitoring"))


@bp.route("/rede")
@login_required
@require_role("admin", "gestor", "operador")
def rede_monitoring() -> str:
    """Render painel de rede — discovery nmap + Zabbix drules."""
    import os

    from itgov.api.v1.rede_monitoring import get_cached_rede_summary

    if not os.getenv("ZABBIX_URL"):
        abort(404)

    data = get_cached_rede_summary()
    return render_template("dashboards/rede_monitoring.html", data=data)


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
    import os

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
    import os

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
