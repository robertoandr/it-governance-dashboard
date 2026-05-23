"""
Rotas de Governança - metadados de domínios e owners.

Endpoints:
  GET /api/governance/owners       → lista de domínios + owners + SLA
  GET /api/governance/organization → nome, título do dashboard
  GET /api/governance/domain/<key> → detalhes de um domínio
"""
import logging

from flask import Blueprint, current_app, jsonify

from services import governance_service as gs

log = logging.getLogger("routes.governance")

governance_bp = Blueprint("governance", __name__, url_prefix="/api/governance")


def _get_cache() -> dict:
    """Acessa o _cache global definido em app.py."""
    return current_app.config.get("DASHBOARD_CACHE", {})


@governance_bp.route("/owners")
def owners():
    """Lista de domínios com owners e SLA (runtime)."""
    cache = _get_cache()
    domains = gs.get_domains_with_runtime(cache)
    return jsonify({
        "organization": gs.get_organization(),
        "domains": domains,
        "count": {
            "total": len(domains),
            "active": sum(1 for d in domains if d.get("status") == "active"),
            "planned": sum(1 for d in domains if d.get("status") == "planned"),
        }
    })


@governance_bp.route("/organization")
def organization():
    """Dados da organização."""
    return jsonify(gs.get_organization())


@governance_bp.route("/domain/<key>")
def domain_detail(key: str):
    """Detalhes de um domínio específico."""
    d = gs.get_domain(key)
    if not d:
        return jsonify({"error": "domain_not_found", "key": key}), 404

    if d.get("status") == "active":
        current = gs.compute_sla_current(key, _get_cache())
        if current is not None:
            d.setdefault("sla", {})["current"] = current

    return jsonify(d)
