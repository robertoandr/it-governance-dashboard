"""Configuração centralizada do structlog com correlation_id.

Chamar configure_logging() uma vez no startup da aplicação (app.py).
"""

from __future__ import annotations

import logging
import sys

import structlog


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    """Configura structlog + logging stdlib com output JSON ou console.

    Args:
        level: Nível de log ("DEBUG", "INFO", "WARNING", "ERROR").
        fmt: "json" para produção, "console" para desenvolvimento.
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    if fmt == "console":
        renderer: structlog.types.Processor = structlog.dev.ConsoleRenderer()
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(log_level)

    # Silencia loggers ruidosos de libs externas
    for noisy in ("httpx", "httpcore", "urllib3", "werkzeug"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
