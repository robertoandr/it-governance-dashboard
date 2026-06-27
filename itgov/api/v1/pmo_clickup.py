"""PMO dashboard — integração ClickUp + score manual de governança."""

from __future__ import annotations

import os
import threading
import time
from datetime import UTC, datetime
from typing import Any

import requests
import structlog

log = structlog.get_logger(__name__)

CLICKUP_LIST_ID = os.getenv("CLICKUP_LIST_ID", "901321459571")
CLICKUP_WORKSPACE_ID = os.getenv("CLICKUP_WORKSPACE_ID", "9013344143")


def _token() -> str:
    return os.getenv("CLICKUP_TOKEN", "")


_CACHE: dict[str, Any] = {}
_CACHE_LOCK = threading.Lock()
_CACHE_TTL = 300  # 5 minutos

# IDs de campos customizados da lista "TI | Projetos"
_FIELD_CONCLUIDO = "7e92d245-0237-48d3-9c64-685b56b415fa"
_EQUIPE_LABELS = {0: "Sistemas e Integração", 1: "Infraestrutura e Suporte"}
_STATUS_MAP = {
    "a fazer": "todo",
    "in progress": "in_progress",
    "finalizado": "done",
    "complete": "done",
    "review": "review",
}
_PRIORITY_MAP = {
    "1": "urgent",
    "2": "high",
    "3": "normal",
    "4": "low",
    None: "normal",
}


def _headers() -> dict[str, str]:
    return {"Authorization": _token(), "Content-Type": "application/json"}


def _fetch_tasks() -> list[dict]:
    """Busca todas as tarefas da lista ClickUp."""
    if not _token():
        return []

    tasks: list[dict] = []
    page = 0
    while True:
        try:
            resp = requests.get(
                f"https://api.clickup.com/api/v2/list/{CLICKUP_LIST_ID}/task",
                headers=_headers(),
                params={
                    "page": page,
                    "include_closed": True,
                    "subtasks": False,
                },
                timeout=15,
            )
            if resp.status_code == 401:
                log.error("pmo.clickup_auth_failed")
                break
            if resp.status_code != 200:
                log.warning("pmo.clickup_error", status=resp.status_code)
                break
            data = resp.json()
            batch = data.get("tasks", [])
            tasks.extend(batch)
            if not batch or data.get("last_page"):
                break
            page += 1
        except Exception as exc:
            log.warning("pmo.clickup_request_failed", erro=str(exc))
            break

    return tasks


def _parse_custom_field(task: dict, field_id: str) -> Any:
    for cf in task.get("custom_fields", []):
        if cf.get("id") == field_id:
            return cf.get("value")
    return None


def _parse_task(task: dict) -> dict:
    """Converte uma tarefa ClickUp para o formato do dashboard."""
    status_raw = (task.get("status") or {}).get("status", "")
    status = _STATUS_MAP.get(status_raw.lower(), "todo")

    priority_raw = str((task.get("priority") or {}).get("orderindex", "3"))
    priority = _PRIORITY_MAP.get(priority_raw, "normal")

    assignees = [a.get("username", a.get("email", "?")) for a in task.get("assignees", [])]

    due_ts = task.get("due_date")
    due_date = None
    overdue = False
    if due_ts:
        due_dt = datetime.fromtimestamp(int(due_ts) / 1000, tz=UTC)
        due_date = due_dt.strftime("%d/%m/%Y")
        if status not in ("done",) and due_dt < datetime.now(UTC):
            overdue = True

    date_closed_ts = task.get("date_closed")
    date_closed = None
    if date_closed_ts:
        date_closed = datetime.fromtimestamp(int(date_closed_ts) / 1000, tz=UTC).strftime("%d/%m/%Y")

    concluido_val = _parse_custom_field(task, _FIELD_CONCLUIDO)
    pct_complete = 0
    if isinstance(concluido_val, dict):
        pct_complete = int(concluido_val.get("percent_complete", 0) or 0)
    elif status == "done":
        pct_complete = 100

    # Campo Equipe (dropdown)
    equipe = None
    for cf in task.get("custom_fields", []):
        if cf.get("name", "").lower() == "equipe" and cf.get("value") is not None:
            equipe = _EQUIPE_LABELS.get(int(cf["value"]), str(cf["value"]))
            break

    # Campo Projeto (labels)
    projeto_labels: list[str] = []
    for cf in task.get("custom_fields", []):
        if cf.get("name", "").lower() == "projeto":
            options = cf.get("type_config", {}).get("options", [])
            selected_ids = cf.get("value") or []
            id_to_label = {o["id"]: o["label"] for o in options}
            projeto_labels = [id_to_label.get(sid, sid) for sid in selected_ids]
            break

    return {
        "id": task.get("id"),
        "name": task.get("name", ""),
        "status": status,
        "status_raw": status_raw,
        "priority": priority,
        "assignees": assignees,
        "due_date": due_date,
        "date_closed": date_closed,
        "overdue": overdue,
        "pct_complete": pct_complete,
        "equipe": equipe,
        "projeto": projeto_labels,
        "url": task.get("url", ""),
    }


def get_cached_pmo() -> dict:
    """Retorna dados PMO com cache TTL 5 min."""
    with _CACHE_LOCK:
        if _CACHE.get("ts") and time.monotonic() - _CACHE["ts"] < _CACHE_TTL:
            return _CACHE["data"]

    log.info("pmo.cache.miss")
    raw_tasks = _fetch_tasks()

    if not raw_tasks and not _token():
        result = _empty_result(reason="token_missing")
    elif not raw_tasks:
        result = _empty_result(reason="no_tasks")
    else:
        tasks = [_parse_task(t) for t in raw_tasks]
        result = _build_summary(tasks)

    with _CACHE_LOCK:
        _CACHE["data"] = result
        _CACHE["ts"] = time.monotonic()

    return result


def _build_summary(tasks: list[dict]) -> dict:
    total = len(tasks)
    done = sum(1 for t in tasks if t["status"] == "done")
    in_progress = sum(1 for t in tasks if t["status"] == "in_progress")
    todo = sum(1 for t in tasks if t["status"] == "todo")
    overdue = sum(1 for t in tasks if t["overdue"])
    pct_medio = int(sum(t["pct_complete"] for t in tasks) / total) if total else 0

    infra = [t for t in tasks if t.get("equipe") == "Infraestrutura e Suporte"]
    sistemas = [t for t in tasks if t.get("equipe") == "Sistemas e Integração"]
    sem_equipe = [t for t in tasks if not t.get("equipe")]

    # Projetos ativos (in_progress) ordenados por vencimento
    ativos = sorted(
        [t for t in tasks if t["status"] == "in_progress"],
        key=lambda x: (x["overdue"], x["due_date"] or "9999"),
        reverse=True,
    )

    # Últimos concluídos
    concluidos = sorted(
        [t for t in tasks if t["status"] == "done" and t["date_closed"]],
        key=lambda x: x["date_closed"] or "",
        reverse=True,
    )[:5]

    return {
        "has_data": True,
        "token_ok": bool(_token()),
        "total": total,
        "done": done,
        "in_progress": in_progress,
        "todo": todo,
        "overdue": overdue,
        "pct_medio": pct_medio,
        "taxa_conclusao": round(done / total * 100, 1) if total else 0,
        "infra_count": len(infra),
        "sistemas_count": len(sistemas),
        "sem_equipe_count": len(sem_equipe),
        "ativos": ativos,
        "concluidos": concluidos,
        "todas_tarefas": tasks,
        "updated_at": datetime.now(UTC).strftime("%d/%m %H:%M"),
    }


def _empty_result(reason: str = "") -> dict:
    return {
        "has_data": False,
        "token_ok": bool(_token()),
        "reason": reason,
        "total": 0,
        "done": 0,
        "in_progress": 0,
        "todo": 0,
        "overdue": 0,
        "pct_medio": 0,
        "taxa_conclusao": 0,
        "infra_count": 0,
        "sistemas_count": 0,
        "sem_equipe_count": 0,
        "ativos": [],
        "concluidos": [],
        "todas_tarefas": [],
        "updated_at": datetime.now(UTC).strftime("%d/%m %H:%M"),
    }


def invalidar_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.pop("ts", None)
