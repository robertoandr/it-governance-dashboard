"""Gunicorn production configuration — IT Governance Dashboard."""

from __future__ import annotations

import multiprocessing
import os

# ── Binding ────────────────────────────────────────────────────────────────────
# Keep :8091 so nginx proxy_pass rules need no changes.
bind = os.getenv("GUNICORN_BIND", "127.0.0.1:8091")

# ── Workers ────────────────────────────────────────────────────────────────────
# FIX(#156): workers=1 required because the refresh thread must run in the same
# process that serves HTTP requests. With preload_app=True and multiple workers,
# threads start in the master and never survive fork() — workers see a frozen
# _cache (last_refresh: null) forever. Single worker + many threads maintains
# concurrency without the fork-isolation bug.
_cpu = multiprocessing.cpu_count()
workers = 1
worker_class = "gthread"
threads = min(_cpu * 4, 16)

# ── Timeouts ───────────────────────────────────────────────────────────────────
timeout = 60  # kill worker if silent for 60 s (handles stuck requests)
graceful_timeout = 30  # wait 30 s for in-flight requests on SIGTERM
keepalive = 5  # seconds to keep idle connections open

# ── Memory leak protection ─────────────────────────────────────────────────────
max_requests = 1000  # recycle worker after N requests
max_requests_jitter = 100  # random ±100 to avoid thundering-herd on restart

# ── Logging ────────────────────────────────────────────────────────────────────
# "-" routes to stdout/stderr, captured by journald when running under systemd.
accesslog = "-"
errorlog = "-"
loglevel = os.getenv("LOG_LEVEL", "info")
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" rt=%(L)ss'

# ── Performance ────────────────────────────────────────────────────────────────
# FIX(#156): preload_app disabled. With preload_app=True, _start() runs in the
# master and the refresh thread starts there — but threads don't survive fork(),
# so workers always have a stale _cache. With preload_app=False, each worker
# imports the app independently and _start() runs in the worker process.
# With workers=1 (above), there's exactly one refresh thread serving requests.
preload_app = False

# ── Server mechanics ───────────────────────────────────────────────────────────
# systemd sends SIGTERM on stop — graceful_timeout handles in-flight requests.
# PID file not needed (systemd tracks PIDs directly via cgroups).
# PrivateTmp=true in the systemd unit gives gunicorn its own /tmp mount,
# preventing "Permission denied: /home/zabbix" from the control socket.
worker_tmp_dir = "/tmp"
forwarded_allow_ips = "127.0.0.1"  # only trust X-Forwarded-* from localhost nginx
proxy_protocol = False


# ── Lifecycle hooks ────────────────────────────────────────────────────────────


def on_starting(server: object) -> None:
    """Log startup with worker configuration."""
    import logging

    logging.basicConfig(level=logging.INFO)
    logging.getLogger("gunicorn.error").info(
        "gunicorn starting: bind=%s workers=%d worker_class=%s threads=%d",
        bind,
        workers,
        worker_class,
        threads,
    )


def on_exit(server: object) -> None:
    """Log clean shutdown."""
    import logging

    logging.getLogger("gunicorn.error").info("gunicorn exiting cleanly")
