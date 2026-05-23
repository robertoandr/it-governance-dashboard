"""
routes/maintenance.py — Endpoints REST de manutenção manual

Endpoints:
  GET    /api/maintenance              → lista hosts em manutenção
  GET    /api/maintenance/stats        → estatísticas
  GET    /api/maintenance/history      → audit log (últimos N)
  POST   /api/maintenance/mark         → marca 1+ hosts (requer OPS_PIN)
  POST   /api/maintenance/clear        → libera 1+ hosts (requer OPS_PIN)

Autenticação: header X-Ops-Pin OU campo "pin" no JSON body.
"""

from __future__ import annotations

import logging
import os
from functools import wraps

from flask import Blueprint, jsonify, request

from services import maintenance_service as svc

log = logging.getLogger("routes.maintenance")

maintenance_bp = Blueprint("maintenance", __name__, url_prefix="/api/maintenance")


def _get_ops_pin() -> str:
    """Lê OPS_PIN do ambiente. Vazio = endpoints de escrita bloqueados."""
    return os.environ.get("OPS_PIN", "").strip()


def _extract_pin() -> str:
    """Extrai PIN do header X-Ops-Pin ou do JSON body."""
    # 1. Header
    pin = request.headers.get("X-Ops-Pin", "").strip()
    if pin:
        return pin
    # 2. JSON body
    if request.is_json:
        data = request.get_json(silent=True) or {}
        return str(data.get("pin", "")).strip()
    return ""


def require_pin(fn):
    """Decorator: exige OPS_PIN válido."""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        expected = _get_ops_pin()
        if not expected:
            log.error("OPS_PIN não configurado no ambiente — bloqueando escrita")
            return jsonify(
                {
                    "ok": False,
                    "error": "ops_pin_not_configured",
                    "message": "Servidor sem OPS_PIN definido. Contate o admin.",
                }
            ), 503

        provided = _extract_pin()
        if provided != expected:
            log.warning("Tentativa de escrita com PIN inválido — IP: %s", request.remote_addr)
            return jsonify(
                {
                    "ok": False,
                    "error": "invalid_pin",
                    "message": "PIN inválido.",
                }
            ), 401

        return fn(*args, **kwargs)

    return wrapper


# ── Endpoints de leitura (públicos no contexto do dashboard) ────────────


@maintenance_bp.get("")
def list_maintenance():
    """Lista hosts atualmente em manutenção."""
    return jsonify(
        {
            "ok": True,
            "hosts": svc.list_active(),
        }
    )


@maintenance_bp.get("/stats")
def maintenance_stats():
    """Estatísticas agregadas."""
    return jsonify(
        {
            "ok": True,
            "stats": svc.stats(),
        }
    )


@maintenance_bp.get("/history")
def maintenance_history():
    """Últimos N eventos do audit log."""
    try:
        limit = int(request.args.get("limit", 50))
    except (TypeError, ValueError):
        limit = 50
    limit = max(1, min(limit, 500))  # clamp 1..500

    return jsonify(
        {
            "ok": True,
            "limit": limit,
            "events": svc.history(limit=limit),
        }
    )


# ── Endpoints de escrita (exigem OPS_PIN) ───────────────────────────────


@maintenance_bp.post("/mark")
@require_pin
def mark_maintenance():
    """
    Marca 1+ hosts como em manutenção.

    Body JSON:
      {
        "pin": "...",              (ou header X-Ops-Pin)
        "hosts": ["CAM-8A-35"],    (lista de host names)
        "operator": "roberto",
        "reason": "Switch 8A em troca",
        "domain": "cftv"           (opcional, default "cftv")
      }
    """
    data = request.get_json(silent=True) or {}

    hosts = data.get("hosts") or []
    if isinstance(hosts, str):
        hosts = [hosts]
    if not isinstance(hosts, list) or not hosts:
        return jsonify(
            {
                "ok": False,
                "error": "invalid_hosts",
                "message": "Campo 'hosts' deve ser lista não-vazia.",
            }
        ), 400

    operator = str(data.get("operator", "")).strip()
    reason = str(data.get("reason", "")).strip()
    domain = str(data.get("domain", "cftv")).strip() or "cftv"

    if not operator:
        return jsonify(
            {
                "ok": False,
                "error": "missing_operator",
                "message": "Campo 'operator' é obrigatório.",
            }
        ), 400

    if not reason:
        return jsonify(
            {
                "ok": False,
                "error": "missing_reason",
                "message": "Campo 'reason' é obrigatório.",
            }
        ), 400

    result = svc.mark(hosts=hosts, operator=operator, reason=reason, domain=domain)
    return jsonify({"ok": True, **result})


@maintenance_bp.post("/clear")
@require_pin
def clear_maintenance():
    """
    Remove 1+ hosts da manutenção.

    Body JSON:
      {
        "pin": "...",
        "hosts": ["CAM-8A-35"],
        "operator": "roberto",
        "note": "Switch normalizado"   (opcional)
      }
    """
    data = request.get_json(silent=True) or {}

    hosts = data.get("hosts") or []
    if isinstance(hosts, str):
        hosts = [hosts]
    if not isinstance(hosts, list) or not hosts:
        return jsonify(
            {
                "ok": False,
                "error": "invalid_hosts",
                "message": "Campo 'hosts' deve ser lista não-vazia.",
            }
        ), 400

    operator = str(data.get("operator", "")).strip()
    note = str(data.get("note", "")).strip()

    if not operator:
        return jsonify(
            {
                "ok": False,
                "error": "missing_operator",
                "message": "Campo 'operator' é obrigatório.",
            }
        ), 400

    result = svc.clear(hosts=hosts, operator=operator, note=note)
    return jsonify({"ok": True, **result})


# ═══════════════════════════════════════════════════════════════════════
# Sprint 6e — Endpoints adicionais
# ═══════════════════════════════════════════════════════════════════════


@maintenance_bp.get("/_debug")
def maintenance_debug():
    """
    Health/debug endpoint — útil para troubleshooting.
    NÃO requer PIN (apenas leitura de metadados).
    """
    from datetime import datetime, timezone

    sf = svc._STATE_FILE
    hf = svc._HISTORY_FILE
    state = svc._load_state()
    hosts = state.get("hosts", {})

    state_info = {"path": str(sf), "exists": sf.exists()}
    if sf.exists():
        st = sf.stat()
        state_info["size_bytes"] = st.st_size
        state_info["mtime"] = datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(
            timespec="seconds"
        )

    history_info = {"path": str(hf), "exists": hf.exists()}
    if hf.exists():
        history_info["size_bytes"] = hf.stat().st_size

    return jsonify(
        {
            "ok": True,
            "module": "maintenance_service",
            "state_file": state_info,
            "history_file": history_info,
            "active_count": len(hosts),
            "active_hosts": list(hosts.keys()),
            "version": state.get("version"),
            "updated_at": state.get("updated_at"),
        }
    )


@maintenance_bp.post("/release_all")
@require_pin
def release_all_maintenance():
    """
    🚨 ENDPOINT PERIGOSO — Libera TODOS os hosts em manutenção.

    Camadas de segurança:
      1. Header X-Ops-Pin válido (@require_pin)
      2. Query string ?confirm=YES_RELEASE_ALL
      3. Body JSON com 'operator' obrigatório
    """
    confirm = request.args.get("confirm", "").strip()
    if confirm != "YES_RELEASE_ALL":
        return jsonify(
            {
                "ok": False,
                "error": "confirm_required",
                "message": "Adicione ?confirm=YES_RELEASE_ALL à URL.",
            }
        ), 400

    data = request.get_json(silent=True) or {}
    operator = str(data.get("operator", "")).strip()
    note = str(data.get("note", "Sprint 6e: release_all")).strip()

    if not operator:
        return jsonify(
            {
                "ok": False,
                "error": "missing_operator",
                "message": "Campo 'operator' é obrigatório.",
            }
        ), 400

    active = svc.list_active_dict()  # 🔧 Sprint 6f: dict pra .keys()
    if not active:
        return jsonify(
            {
                "ok": True,
                "released": 0,
                "hosts": [],
                "message": "Nenhum host em manutenção.",
            }
        )

    hosts_to_release = list(active.keys())
    log.warning(
        "RELEASE_ALL acionado por %s (IP=%s): %d hosts → %s",
        operator,
        request.remote_addr,
        len(hosts_to_release),
        hosts_to_release,
    )

    result = svc.clear(
        hosts=hosts_to_release,
        operator=operator,
        note=f"[release_all] {note}",
    )

    return jsonify(
        {
            "ok": True,
            "released": len(hosts_to_release),
            "hosts": hosts_to_release,
            **result,
        }
    )
