"""
Dashboard de Governança de TI - Grupo Gadens
Flask + thread de refresh + Score de Saúde Geral.
"""

import logging
import threading
import time
from datetime import UTC, datetime

from flask import Flask, jsonify, redirect, render_template, request
from flask_restx import Api

import config
from app_utils import safe
from collectors.grafana import GrafanaCollector
from collectors.graph import GraphCollector
from collectors.influx import InfluxCollector
from collectors.ldap_collector import LDAPCollector
from collectors.zabbix import ZabbixCollector
from itgov.api.v1.alerts import ns as alerts_ns
from itgov.api.v1.ativos import ns as ativos_ns
from itgov.api.v1.zabbix import ns as zabbix_ns
from itgov.api.v1.zendesk import ns as zendesk_ns

# Fase 5: Blueprints de Governança
from routes.governance import governance_bp
from routes.maintenance import maintenance_bp

# Sprint 6c: integração de manutenção manual com pipeline Zabbix
from services import maintenance_service as _maint

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("dashboard")

app = Flask(__name__)
app.config["SECRET_KEY"] = config._env("SECRET_KEY", required=True)

# Sprint 10D: Security middleware (Talisman + Limiter)
from middleware.security import init_security  # noqa: E402

limiter = init_security(app)

# Fase 5: Registro de Blueprints
app.register_blueprint(governance_bp)
app.register_blueprint(maintenance_bp)

# Sprint 11: Governance Score Dashboard (COBIT 5 pilares) em /gov
try:
    from app.views.dashboards import bp as _gov_dash_bp

    app.register_blueprint(_gov_dash_bp, url_prefix="/gov")
except Exception as _gov_err:
    log.warning("governance_dash_blueprint_unavailable: %s", _gov_err)

# itgov REST API v1 (Flask-RESTX namespaces)
itgov_api = Api(app, prefix="/api/v1", title="IT Gov API", version="1.0", doc=False)
itgov_api.add_namespace(zabbix_ns)
itgov_api.add_namespace(zendesk_ns)
itgov_api.add_namespace(ativos_ns)
itgov_api.add_namespace(alerts_ns)

# Módulo de alertas CFTV — inicia poller em background
try:
    from itgov.services.alert_poller import AlertPoller

    _alert_poller = AlertPoller.from_config()
    _alert_poller.start()
except Exception as _poller_err:
    log.warning("alert_poller_nao_iniciado: %s", _poller_err)

_cache: dict = {
    "hosts": {},
    "problems": {"by_severity": {}, "items": []},
    "triggers": [],
    "m365_items": {},
    "mfa": {},
    "service_health": [],
    "incidents": {},
    "users_m365": {},
    "secure_score": {},
    "intune": {},
    "licenses": [],
    "grafana_dashboards": [],
    "users_ad": {},
    "cftv": {"enabled": False, "total": 0, "by_subcat": {}, "down_list": []},
    "health_score": {"score": 0, "label": "—", "components": []},
    "last_refresh": None,
    "last_error": None,
    "thresholds": config.THRESHOLDS,
}
_cache_lock = threading.Lock()

# Fase 5: Expor cache para blueprints
app.config["DASHBOARD_CACHE"] = _cache
_zbx_lock = threading.Lock()
_zbx_shared = ZabbixCollector()


def _compute_health_score(d: dict) -> dict:
    """Score 0-100 ponderado: SS 30, Hosts 25, Triggers 20, MFA 15, ServiceHealth 10."""
    components = []
    weighted, total_w = 0.0, 0.0

    ss = d.get("secure_score") or {}
    if ss.get("enabled") and ss.get("pct") is not None:
        v = float(ss["pct"])
        components.append({"name": "Secure Score", "value": round(v, 1), "weight": 30, "unit": "%"})
        weighted += v * 30
        total_w += 30

    hosts = d.get("hosts") or {}
    if hosts.get("total"):
        v = float(hosts.get("up_pct") or 0)
        components.append({"name": "Hosts UP", "value": round(v, 1), "weight": 25, "unit": "%"})
        weighted += v * 25
        total_w += 25

    trs = d.get("triggers") or []
    if trs:
        crit = sum(1 for t in trs if t.get("severity_num", 0) >= 4)
        v = 100.0 * (1.0 - crit / len(trs))
        components.append({"name": "Triggers não-críticas", "value": round(v, 1), "weight": 20, "unit": "%"})
        weighted += v * 20
        total_w += 20
    else:
        components.append({"name": "Triggers", "value": 100, "weight": 20, "unit": "%"})
        weighted += 100 * 20
        total_w += 20

    mfa = d.get("mfa") or {}
    if mfa.get("total", 0) > 0:
        v = float(mfa.get("pct") or 0)
        components.append({"name": "MFA habilitada", "value": round(v, 1), "weight": 15, "unit": "%"})
        weighted += v * 15
        total_w += 15

    sh = d.get("service_health") or []
    if sh:
        ok = sum(1 for s in sh if "operational" in str(s.get("status_text", "")).lower())
        v = round(100.0 * ok / len(sh), 1) if sh else 0
        components.append({"name": "Service Health", "value": v, "weight": 10, "unit": "%"})
        weighted += v * 10
        total_w += 10

    score = round(weighted / total_w, 1) if total_w else 0.0
    if score >= 85:
        label = "Excelente"
    elif score >= 70:
        label = "Bom"
    elif score >= 50:
        label = "Atenção"
    else:
        label = "Crítico"
    return {"score": score, "label": label, "components": components}


def _refresh_once():
    with _zbx_lock:
        zbx = _zbx_shared
    influx = InfluxCollector()
    graph = GraphCollector()
    grafana = GrafanaCollector()
    ldap = LDAPCollector()

    new_data = {}
    errors = []

    # Sprint 6c: wrappear coletas do Zabbix com filtro de manutenção manual
    # (m365_items não é wrappeado: não tem hosts on-prem)
    for name, fn in [
        ("hosts", lambda: _maint.apply_filter("hosts", zbx.get_host_summary())),
        ("problems", lambda: _maint.apply_filter("problems", zbx.get_problems(limit=30))),
        ("triggers", lambda: _maint.apply_filter("triggers", zbx.get_active_triggers(limit=200))),
        ("m365_items", zbx.get_m365_items),
        ("cftv", lambda: _maint.apply_filter("cftv", zbx.get_cftv_summary())),
    ]:
        try:
            new_data[name] = fn()
        except Exception as e:
            log.warning("%s falhou: %s", name, type(e).__name__)
            errors.append(f"{name}: {e}")

    for name, fn in [
        ("mfa", influx.get_mfa),
        ("service_health", influx.get_service_health),
        ("incidents", influx.get_incidents),
        ("users_m365", influx.get_users),
    ]:
        try:
            new_data[name] = fn()
        except Exception as e:
            log.warning("%s falhou: %s", name, type(e).__name__)
    influx.close()

    if config.GRAPH_ENABLED:
        for name, fn in [
            ("secure_score", graph.get_secure_score),
            ("intune", graph.get_intune_compliance),
            ("licenses", graph.get_licenses),
        ]:
            try:
                new_data[name] = fn()
            except Exception as e:
                log.warning("%s falhou: %s", name, type(e).__name__)
                errors.append(f"{name}: {e}")

    if config.GRAFANA_ENABLED:
        try:
            new_data["grafana_dashboards"] = grafana.list_dashboards()
        except Exception as e:
            log.warning("grafana: %s", e)
    if config.LDAP_ENABLED:
        try:
            new_data["users_ad"] = ldap.get_user_summary()
        except Exception as e:
            log.warning("users_ad: %s", e)
            errors.append(f"users_ad: {e}")

    merged = {**_cache, **new_data}
    new_data["health_score"] = _compute_health_score(merged)
    new_data["last_refresh"] = datetime.now(UTC).isoformat(timespec="seconds")
    new_data["last_error"] = "; ".join(errors) if errors else None

    with _cache_lock:
        _cache.update(new_data)


def _refresh_loop():
    while True:
        try:
            _refresh_once()
        except Exception as e:
            log.exception("loop: %s", safe(e))
            with _cache_lock:
                _cache["last_error"] = str(e)
        time.sleep(config.CACHE_TTL)


@app.route("/")
def index():
    with _cache_lock:
        data = dict(_cache)
    return render_template(
        "dashboard.html",
        data=data,
        grafana_url=config.GRAFANA_URL if config.GRAFANA_ENABLED else None,
        cache_ttl=config.CACHE_TTL,
        zabbix_front_url=config.ZABBIX_FRONT_URL,
    )


@app.route("/api/data")
def api_data():
    with _cache_lock:
        return jsonify(_cache)


@app.route("/api/health")
def api_health():
    with _cache_lock:
        hs = _cache.get("health_score") or {}
        return jsonify(
            {
                "ok": _cache.get("last_refresh") is not None,
                "last_refresh": _cache.get("last_refresh"),
                "last_error": _cache.get("last_error"),
                "graph_enabled": config.GRAPH_ENABLED,
                "grafana_enabled": config.GRAFANA_ENABLED,
                "ldap_enabled": config.LDAP_ENABLED,
                "health_score": hs.get("score"),
                "health_label": hs.get("label"),
            }
        )


@app.route("/api/ack", methods=["POST"])
def api_ack():
    body = request.get_json(silent=True) or {}
    eventid = str(body.get("eventid", "")).strip()
    message = str(body.get("message", "")).strip() or "Resolvido via dashboard"
    if not eventid or not eventid.isdigit():
        return jsonify({"ok": False, "error": "eventid obrigatório (numérico)"}), 400
    try:
        with _zbx_lock:
            result = _zbx_shared.acknowledge_event(eventid, message)
        threading.Thread(target=_refresh_once, daemon=True).start()
        return jsonify(result)
    except Exception as e:
        log.warning("ack %s: %s", safe(eventid), safe(e))
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/unack", methods=["POST"])
def api_unack():
    body = request.get_json(silent=True) or {}
    eventid = str(body.get("eventid", "")).strip()
    if not eventid or not eventid.isdigit():
        return jsonify({"ok": False, "error": "eventid obrigatório (numérico)"}), 400
    try:
        with _zbx_lock:
            result = _zbx_shared.unacknowledge_event(eventid)
        threading.Thread(target=_refresh_once, daemon=True).start()
        return jsonify(result)
    except Exception as e:
        log.warning("unack %s: %s", safe(eventid), safe(e))
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/license-edit", methods=["POST"])
def api_license_edit():
    """Edita cost_brl ou category de uma SKU. Valida PIN, faz append no history e loga."""
    import json
    from pathlib import Path

    body = request.get_json(silent=True) or {}
    pin = str(body.get("pin", "")).strip()
    sku = str(body.get("sku", "")).strip()
    field = str(body.get("field", "")).strip()
    value = body.get("value")

    expected_pin = str(getattr(config, "FINOPS_PIN", "") or "")
    if not expected_pin or expected_pin == "000000":
        return jsonify({"ok": False, "error": "PIN não configurado no servidor"}), 503
    if pin != expected_pin:
        return jsonify({"ok": False, "error": "PIN incorreto"}), 401
    if not sku:
        return jsonify({"ok": False, "error": "sku obrigatório"}), 400
    if field not in ("cost_brl", "category"):
        return jsonify({"ok": False, "error": "field deve ser cost_brl ou category"}), 400

    # Validações de valor
    if field == "cost_brl":
        try:
            value = round(float(value), 2)
            if value < 0 or value > 100000:
                raise ValueError()
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "cost_brl inválido (0-100000)"}), 400
    elif field == "category" and value not in ("paid", "free", "trial"):
        return jsonify({"ok": False, "error": "category deve ser paid/free/trial"}), 400

    costs_path = Path("/opt/it-gov-dashboard/data/license_costs.json")
    try:
        with open(costs_path) as f:
            costs = json.load(f)
    except Exception as e:
        return jsonify({"ok": False, "error": f"erro lendo JSON: {e}"}), 500

    if sku not in costs:
        return jsonify({"ok": False, "error": f"SKU '{sku}' não encontrada"}), 404

    old_value = costs[sku].get(field)

    # Se mudou cost_brl, faz append no history (data atual)
    if field == "cost_brl" and old_value != value:
        history = costs[sku].setdefault("history", [])
        history.append(
            {
                "date": datetime.now(UTC).strftime("%Y-%m-%d"),
                "cost_brl": float(old_value) if old_value else 0,
            }
        )
        # Mantém apenas últimos 20 entries
        costs[sku]["history"] = history[-20:]

    costs[sku][field] = value

    try:
        with open(costs_path, "w") as f:
            json.dump(costs, f, indent=2, ensure_ascii=False)
    except Exception as e:
        return jsonify({"ok": False, "error": f"erro salvando JSON: {e}"}), 500

    # Log de mudança
    log_path = Path("/opt/it-gov-dashboard/logs/finops-changes.log")
    log_path.parent.mkdir(exist_ok=True)
    with open(log_path, "a") as f:
        f.write(
            f"{datetime.now(UTC).isoformat(timespec='seconds')} | "
            f"IP={request.remote_addr} | SKU={sku} | field={field} | "
            f"old={old_value} | new={value}\n"
        )

    threading.Thread(target=_refresh_once, daemon=True).start()
    return jsonify({"ok": True, "sku": sku, "field": field, "old": old_value, "new": value})


def _start():
    t = threading.Thread(target=_refresh_loop, daemon=True, name="refresh")
    t.start()
    log.info("Thread de refresh iniciada (TTL=%ss)", config.CACHE_TTL)


_start()


# ═════════════════════════════════════════════════════════════
# Fase 5: Rotas v2 (nova estrutura - em paralelo ao dashboard.html)
# ═════════════════════════════════════════════════════════════
@app.route("/v2")
@app.route("/v2/")
def v2_home():
    """Home v2 - visão geral consolidada de governança."""
    return render_template("v2/home.html")


# ─── Deprecation redirect ────────────────────────────────────────────────────
# Este arquivo (app.py) só é carregado via wsgi_prod.py ou python app.py.
# O entrypoint de produção (Docker) usa wsgi.py → create_app() do factory.
# Manter este redirect até v1.2.0 para não quebrar bookmarks de quem ainda
# acessa via app.py direto.
@app.route("/dashboard")
def legacy_dashboard_redirect():
    """Redirect permanente do dashboard legado para o novo (factory app).

    Remove em v1.2.0 após confirmar que nenhum bookmark/integração usa este path.
    """
    log.warning(
        "legacy_dashboard_accessed",
        extra={"referrer": request.referrer, "remote_addr": request.remote_addr},
    )
    return redirect("/", code=301)


if __name__ == "__main__":
    log.info("Subindo Flask em 0.0.0.0:%s", config.FLASK_PORT)
    app.run(host="0.0.0.0", port=config.FLASK_PORT, debug=config.FLASK_DEBUG, use_reloader=False)
