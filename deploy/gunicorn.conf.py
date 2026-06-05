"""Gunicorn production configuration — IT Governance Dashboard."""

from __future__ import annotations

import multiprocessing
import os

# ── Binding ────────────────────────────────────────────────────────────────────
# Keep :8091 so nginx proxy_pass rules need no changes.
bind = os.getenv("GUNICORN_BIND", "127.0.0.1:8091")

# ── Workers ────────────────────────────────────────────────────────────────────
# Formula: (2 × cpu_count) + 1, capped at 9 to avoid memory bloat.
# Flask is sync but gthread allows concurrent requests within a worker
# without needing an async framework.
_cpu = multiprocessing.cpu_count()
workers = min(_cpu * 2 + 1, 9)
worker_class = "gthread"
threads = 4

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
# Load application code once in the master process and fork workers from it.
# Cuts per-worker startup time and total RSS; safe because Flask has no
# pre-fork state that must be isolated.
preload_app = True

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
