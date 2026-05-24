"""
services/maintenance_service.py — CRUD de manutenção manual

Características:
- Estado em JSON (manual_maintenance.json)
- Audit log em JSONL append-only (maintenance_history.jsonl)
- Thread-safe via threading.Lock
- Sem expiração automática (decisão de produto)
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from app_utils import safe

log = logging.getLogger("maintenance")

# Caminhos
_BASE = Path(__file__).resolve().parent.parent
_STATE_FILE = _BASE / "data" / "manual_maintenance.json"
_HISTORY_FILE = _BASE / "data" / "maintenance_history.jsonl"

# Lock global pra writes (Flask é multi-thread)
_LOCK = threading.Lock()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _validate_state(data) -> dict:
    """
    Garante que o estado carregado tem schema válido.
    Sprint 6e: blindagem contra JSON corrompido/manipulado.
    """
    if not isinstance(data, dict):
        log.warning("State não é dict (type=%s) — reset", type(data).__name__)
        return {"version": 1, "hosts": {}, "updated_at": None}

    hosts = data.get("hosts")
    if not isinstance(hosts, dict):
        log.warning("State.hosts não é dict — reset")
        data["hosts"] = {}

    data.setdefault("version", 1)
    data.setdefault("updated_at", None)

    clean_hosts = {}
    for host_name, info in data["hosts"].items():
        if not isinstance(host_name, str) or not host_name.strip():
            log.warning("Host com nome inválido descartado: %r", host_name)
            continue
        if not isinstance(info, dict):
            log.warning("Info inválido para host %s — descartado", safe(host_name))
            continue
        clean_hosts[host_name.strip()] = info

    data["hosts"] = clean_hosts
    return data


def _load_state() -> dict:
    """Carrega estado atual. Se arquivo não existe ou é inválido, retorna estado vazio."""
    if not _STATE_FILE.exists():
        return {"version": 1, "hosts": {}, "updated_at": None}
    try:
        with _STATE_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return _validate_state(data)
    except (json.JSONDecodeError, OSError) as e:
        log.error("Falha ao ler %s: %s — usando estado vazio", _STATE_FILE, safe(e))
        return {"version": 1, "hosts": {}, "updated_at": None}


def _save_state(state: dict) -> None:
    """Persiste estado de forma atômica (write to .tmp, então rename)."""
    state["updated_at"] = _now_iso()
    tmp = _STATE_FILE.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    tmp.replace(_STATE_FILE)


def _append_history(event: dict) -> None:
    """Append-only log. Cada linha = 1 evento JSON."""
    event["at"] = _now_iso()
    with _HISTORY_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


# ── API Pública ────────────────────────────────────────────────────────────


def list_active_dict() -> dict[str, Any]:
    """
    Retorna dicionário {host_name: info} dos hosts em manutenção.
    🔧 USO INTERNO — para lookups rápidos via .keys()/.get().
    """
    return cast("dict[str, Any]", _load_state().get("hosts", {}))


def list_active() -> list:
    """
    Retorna LISTA de hosts em manutenção: [{host, operator, reason, ts, ...}].
    🌐 API PÚBLICA — formato idiomático REST (Sprint 6f).
    """
    hosts_dict = list_active_dict()
    return [
        {"host": host_name, **info}
        for host_name, info in hosts_dict.items()
        if isinstance(info, dict)
    ]


def is_in_maintenance(host_name: str) -> bool:
    """Helper rápido pra coletores: este host está em manutenção manual?"""
    return host_name in _load_state().get("hosts", {})


def get_info(host_name: str) -> dict[str, Any] | None:
    """Info de manutenção de um host específico (ou None)."""
    result = _load_state().get("hosts", {}).get(host_name)
    return cast("dict[str, Any] | None", result)


def mark(hosts: list[str], operator: str, reason: str, domain: str = "cftv") -> dict:
    """
    Marca 1+ hosts como em manutenção.

    Args:
        hosts: lista de host names (ex: ["CAM-8A-35", "CAM-8A-36"])
        operator: nome do operador (ex: "roberto")
        reason: motivo livre (ex: "Investigação switch 8º andar")
        domain: domínio do dashboard (ex: "cftv", "m365") — default cftv

    Returns:
        {"marked": [...], "already_in_maint": [...], "count": N}
    """
    if not hosts:
        return {"marked": [], "already_in_maint": [], "count": 0}

    operator = (operator or "").strip() or "anonymous"
    reason = (reason or "").strip() or "(sem motivo informado)"

    with _LOCK:
        state = _load_state()
        marked = []
        already = []

        for h in hosts:
            h = h.strip()
            if not h:
                continue
            if h in state["hosts"]:
                already.append(h)
                continue
            state["hosts"][h] = {
                "marked_by": operator,
                "marked_at": _now_iso(),
                "reason": reason,
                "domain": domain,
            }
            marked.append(h)
            _append_history(
                {
                    "action": "mark",
                    "host": h,
                    "by": operator,
                    "reason": reason,
                    "domain": domain,
                }
            )

        if marked:
            _save_state(state)
            log.info(
                "Manutenção: %d host(s) marcado(s) por %s — motivo: %s",
                len(marked),
                operator,
                reason,
            )

        return {
            "marked": marked,
            "already_in_maint": already,
            "count": len(marked),
        }


def clear(hosts: list[str], operator: str, note: str = "") -> dict:
    """
    Remove 1+ hosts da manutenção.

    Returns:
        {"cleared": [...], "not_found": [...], "count": N}
    """
    if not hosts:
        return {"cleared": [], "not_found": [], "count": 0}

    operator = (operator or "").strip() or "anonymous"
    note = (note or "").strip()

    with _LOCK:
        state = _load_state()
        cleared = []
        not_found = []

        for h in hosts:
            h = h.strip()
            if not h:
                continue
            if h not in state["hosts"]:
                not_found.append(h)
                continue
            prev = state["hosts"].pop(h)
            cleared.append(h)
            _append_history(
                {
                    "action": "clear",
                    "host": h,
                    "by": operator,
                    "note": note,
                    "previous": prev,
                }
            )

        if cleared:
            _save_state(state)
            log.info("Manutenção: %d host(s) liberado(s) por %s", len(cleared), safe(operator))

        return {
            "cleared": cleared,
            "not_found": not_found,
            "count": len(cleared),
        }


def history(limit: int = 100) -> list[dict]:
    """Retorna últimos N eventos (mais recente primeiro)."""
    if not _HISTORY_FILE.exists():
        return []

    lines = _HISTORY_FILE.read_text(encoding="utf-8").strip().split("\n")
    events = []
    for line in lines[-limit:]:
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    return list(reversed(events))  # mais recente primeiro


def stats() -> dict:
    """Estatísticas rápidas pra dashboard."""
    state = _load_state()
    hosts = state.get("hosts", {})

    by_domain: dict[str, int] = {}
    by_operator: dict[str, int] = {}
    for _h, info in hosts.items():
        d = info.get("domain", "unknown")
        op = info.get("marked_by", "unknown")
        by_domain[d] = by_domain.get(d, 0) + 1
        by_operator[op] = by_operator.get(op, 0) + 1

    return {
        "total_in_maintenance": len(hosts),
        "by_domain": by_domain,
        "by_operator": by_operator,
        "updated_at": state.get("updated_at"),
    }


# ============================================================
# Sprint 6c — Integração com pipeline do Zabbix (apply_filter)
# ============================================================
# Filtra/anota dados coletados do Zabbix com info de manutenção
# manual. Fail-open: qualquer erro retorna data original intacta.


def apply_filter(key: str, data):
    """
    Aplica filtro de manutenção manual sobre dados do collector Zabbix.

    Args:
        key: "hosts" | "problems" | "triggers" | "cftv"
        data: dados originais retornados pelo ZabbixCollector

    Returns:
        Dados enriquecidos/filtrados. Em caso de erro, retorna data original.
    """
    try:
        in_maint = set(list_active_dict().keys())  # 🔧 Sprint 6f: uso interno = dict
        if not in_maint:
            return data

        if key == "hosts":
            return _mf_filter_hosts_summary(data, in_maint)
        if key == "problems":
            return _mf_filter_problems(data, in_maint)
        if key == "triggers":
            return _mf_filter_triggers(data, in_maint)
        if key == "cftv":
            return _mf_filter_cftv(data, in_maint)
        return data
    except Exception as e:
        import logging

        logging.getLogger(__name__).warning(
            "apply_filter(%s) falhou: %s — retornando dados originais", key, e
        )
        return data


def _mf_filter_hosts_summary(data: dict, in_maint: set) -> dict:
    """Hosts é summary agregado: desconta manutenção do down e total."""
    if not isinstance(data, dict):
        return data
    out = dict(data)
    n = len(in_maint)
    out["maint_manual"] = n
    down = int(out.get("down", 0) or 0)
    new_down = max(0, down - n)
    delta = down - new_down
    out["down"] = new_down
    out["total"] = max(0, int(out.get("total", 0) or 0) - delta)
    total = out["total"]
    out["up_pct"] = round((int(out.get("up", 0) or 0) / total) * 100, 1) if total else 0
    return out


def _mf_filter_problems(data: dict, in_maint: set) -> dict:
    """Remove problems de hosts em manutenção e recalcula by_severity."""
    if not isinstance(data, dict):
        return data
    items = data.get("items", []) or []
    filtered = [p for p in items if p.get("host") not in in_maint]
    suppressed = len(items) - len(filtered)

    by_sev_keys = list((data.get("by_severity") or {}).keys())
    by_sev = dict.fromkeys(by_sev_keys, 0)
    for p in filtered:
        sev = p.get("severity", "not_classified")
        by_sev[sev] = by_sev.get(sev, 0) + 1

    return {
        "by_severity": by_sev,
        "items": filtered,
        "suppressed_by_maint": suppressed,
    }


def _mf_filter_triggers(data, in_maint: set):
    """Remove triggers de hosts em manutenção (data é lista direta)."""
    if not isinstance(data, list):
        return data
    return [t for t in data if t.get("host") not in in_maint]


def _mf_filter_cftv(data: dict, in_maint: set) -> dict:
    """
    Move hosts em manutenção manual de down_list → maint_list,
    espelhando a convenção nativa do Zabbix (maintenance_status=1).
    """
    if not isinstance(data, dict):
        return data
    out = dict(data)
    down_list = list(out.get("down_list", []) or [])
    maint_list = list(out.get("maint_list", []) or [])
    by_subcat = {k: dict(v) for k, v in (out.get("by_subcat") or {}).items()}

    new_down_list = []
    moved = 0
    for h in down_list:
        if not isinstance(h, dict):
            new_down_list.append(h)
            continue
        # checa tanto "host" quanto "name" (CFTV usa ambos)
        if (h.get("host") in in_maint) or (h.get("name") in in_maint):
            h_marked = dict(h)
            h_marked["maint_manual"] = True
            maint_list.append(h_marked)
            moved += 1
            subcat = h.get("subcat") or h.get("subcategory") or "outros"
            if subcat in by_subcat:
                by_subcat[subcat]["down"] = max(0, int(by_subcat[subcat].get("down", 0) or 0) - 1)
                by_subcat[subcat]["maint"] = int(by_subcat[subcat].get("maint", 0) or 0) + 1
        else:
            new_down_list.append(h)

    out["down_list"] = new_down_list
    out["maint_list"] = maint_list
    out["by_subcat"] = by_subcat
    out["down"] = max(0, int(out.get("down", 0) or 0) - moved)
    out["maint"] = int(out.get("maint", 0) or 0) + moved
    out["maint_manual_count"] = moved
    return out


# ── Sprint 6e: startup banner ──────────────────────────────────────────────
log.info(
    "maintenance_service ready | state=%s exists=%s | history=%s",
    _STATE_FILE,
    _STATE_FILE.exists(),
    _HISTORY_FILE,
)
