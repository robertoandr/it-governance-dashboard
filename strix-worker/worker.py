"""Worker de pentest: executa os runs do Strix enfileirados pelo dashboard.

Roda no container dedicado ``itgov-strix`` — o **único** com acesso ao socket do
Docker (o Strix sobe containers-sandbox). O app nunca fala com o Docker: ele só
grava linhas em ``cyber_pentest_runs``; este worker as pega.

Ciclo (concorrência 1): pega o run ``aprovado`` mais antigo, **revalida o alvo**
pela allowlist, marca ``rodando``, roda ``strix -n --target <url>`` com timeout,
resume os achados e grava ``concluido``/``falhou`` de volta.

Config por ambiente:
- ``PENTEST_DB_URL``: banco do dashboard (padrão: o SQLite do app,
  ``sqlite:////app/data/app.db``, com o volume ``app_data`` montado).
- ``PENTEST_ALVOS``: allowlist (JSON), idêntica à do app.
- ``STRIX_LLM`` / ``LLM_API_KEY`` / ``LLM_API_BASE``: LLM do Strix (OmniRoute).
- ``PENTEST_WORK_DIR`` (padrão ``/data``; o Strix grava em ``<dir>/strix_runs``),
  ``PENTEST_TIMEOUT_S`` (padrão 3600), ``PENTEST_POLL_S`` (padrão 10).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, Text, create_engine, event, select, update
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError
from strix_run import resumir_achados, status_do_exit, url_do_alvo

log = structlog.get_logger("strix-worker")

_WORK_DIR = Path(os.getenv("PENTEST_WORK_DIR", "/data"))
_RUNS_DIR = _WORK_DIR / "strix_runs"
_TIMEOUT_S = int(os.getenv("PENTEST_TIMEOUT_S", "3600"))
_POLL_S = int(os.getenv("PENTEST_POLL_S", "10"))
_LOG_TAIL = 4000  # últimos caracteres da saída guardados na linha

# Espelho das colunas usadas de ``app.models.pentest.PentestRun``. Declarar os
# tipos (em vez de SQL cru) faz o SQLAlchemy gravar as datas no formato que o app
# lê de volta, também no SQLite.
_runs = Table(
    "cyber_pentest_runs",
    MetaData(),
    Column("id", Integer, primary_key=True),
    Column("alvo_chave", String(80)),
    Column("alvo_url", String(500)),
    Column("status", String(12)),
    Column("exit_code", Integer),
    Column("achados_json", Text),
    Column("relatorio_path", String(500)),
    Column("log_tail", Text),
    Column("iniciado_em", DateTime(timezone=True)),
    Column("terminado_em", DateTime(timezone=True)),
)


def _agora() -> datetime:
    return datetime.now(UTC)


def _proximo_aprovado(conn: Connection) -> dict[str, Any] | None:
    """O run ``aprovado`` mais antigo, ou ``None``."""
    linha = (
        conn.execute(
            select(_runs.c.id, _runs.c.alvo_chave, _runs.c.alvo_url)
            .where(_runs.c.status == "aprovado")
            .order_by(_runs.c.id)
            .limit(1)
        )
        .mappings()
        .first()
    )
    return dict(linha) if linha else None


def _status_atual(conn: Connection, run_id: int) -> str:
    valor = conn.execute(select(_runs.c.status).where(_runs.c.id == run_id)).scalar_one_or_none()
    return str(valor or "")


def _atualizar(engine: Engine, run_id: int, **valores: Any) -> None:
    with engine.begin() as conn:
        conn.execute(update(_runs).where(_runs.c.id == run_id).values(**valores))


def _rodar_strix(url: str) -> tuple[int | None, Path, str]:
    """Roda o Strix headless contra a URL.

    Returns:
        ``(exit_code, run_dir, saida)``. ``exit_code`` é ``None`` no timeout.
    """
    _RUNS_DIR.mkdir(parents=True, exist_ok=True)
    antes = set(os.listdir(_RUNS_DIR))
    cmd = ["strix", "-n", "--target", url]
    log.info("strix.rodando", cmd=" ".join(cmd))
    try:
        proc = subprocess.run(  # noqa: S603 - comando fixo, URL vem da allowlist revalidada
            cmd,
            cwd=_WORK_DIR,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_S,
            check=False,
        )
        saida = (proc.stdout or "") + (proc.stderr or "")
        code: int | None = proc.returncode
    except subprocess.TimeoutExpired as exc:
        saida = (exc.stdout or "") + (exc.stderr or "") if isinstance(exc.stdout, str) else ""
        saida += f"\n[timeout após {_TIMEOUT_S}s]"
        code = None
    # O diretório criado por este run é o que apareceu em strix_runs/.
    novos = sorted(set(os.listdir(_RUNS_DIR)) - antes)
    run_dir = _RUNS_DIR / novos[-1] if novos else _RUNS_DIR
    return code, run_dir, saida


def _processar(engine: Engine, run: dict[str, Any]) -> None:
    """Executa um run e grava o resultado."""
    run_id = int(run["id"])
    url = url_do_alvo(run["alvo_chave"], run.get("alvo_url") or "")
    if url is None:
        _atualizar(
            engine,
            run_id,
            status="falhou",
            log_tail="alvo fora da allowlist atual — não executado",
            terminado_em=_agora(),
        )
        log.warning("strix.alvo_fora_da_allowlist", run_id=run_id, alvo=run["alvo_chave"])
        return

    _atualizar(engine, run_id, status="rodando", iniciado_em=_agora())
    try:
        code, run_dir, saida = _rodar_strix(url)
    except OSError as exc:  # strix ausente, diretório sem permissão etc.
        _atualizar(engine, run_id, status="falhou", log_tail=f"erro ao executar o Strix: {exc}", terminado_em=_agora())
        log.error("strix.erro_execucao", run_id=run_id, erro=str(exc))
        return
    achados, relatorio = resumir_achados(run_dir)

    with engine.begin() as conn:
        # Respeita um cancelamento feito enquanto rodava.
        if _status_atual(conn, run_id) == "cancelado":
            log.info("strix.cancelado_durante_execucao", run_id=run_id)
            return
        conn.execute(
            update(_runs)
            .where(_runs.c.id == run_id)
            .values(
                status=status_do_exit(code),
                exit_code=code,
                achados_json=json.dumps(achados),
                relatorio_path=relatorio,
                log_tail=saida[-_LOG_TAIL:],
                terminado_em=_agora(),
            )
        )
    log.info("strix.terminou", run_id=run_id, status=status_do_exit(code), exit_code=code, achados=achados)


def _criar_engine(dsn: str) -> Engine:
    """Cria o engine da fila, com espera por lock quando o banco é SQLite.

    O app e o worker compartilham o mesmo ``app.db``. Sem ``busy_timeout`` o
    worker recebe ``database is locked`` em qualquer escrita concorrente do app
    e o pentest nunca sai de "aprovado". O ``timeout`` do driver sqlite3 define
    o ``busy_timeout``; um listener reforça o WAL e o timeout a cada conexão.

    Args:
        dsn: URL de conexão (SQLite por padrão).

    Returns:
        Engine configurado para conviver com o app no mesmo arquivo.
    """
    if dsn.startswith("sqlite"):
        engine = create_engine(dsn, pool_pre_ping=True, connect_args={"timeout": 30})

        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_conn: Any, _rec: Any) -> None:
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA busy_timeout=30000")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

        return engine
    return create_engine(dsn, pool_pre_ping=True)


def main() -> int:
    """Loop do worker: um run por vez, consultando a fila a cada ``PENTEST_POLL_S``."""
    dsn = os.getenv("PENTEST_DB_URL", "sqlite:////app/data/app.db")
    engine = _criar_engine(dsn)
    log.info("strix.worker_no_ar", runs_dir=str(_RUNS_DIR), timeout_s=_TIMEOUT_S)
    while True:
        try:
            with engine.connect() as conn:
                run = _proximo_aprovado(conn)
            if run:
                _processar(engine, run)
                continue
        except SQLAlchemyError as exc:  # banco ocupado/indisponível: tenta no próximo ciclo
            log.error("strix.erro_banco", erro=str(exc))
        time.sleep(_POLL_S)


if __name__ == "__main__":
    sys.exit(main())
