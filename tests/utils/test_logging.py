"""Testes para itgov/utils/logging.py — configuração do structlog."""

from __future__ import annotations

import logging

from itgov.utils.logging import configure_logging


class TestConfigureLogging:
    def test_default_json_format_sets_info_level(self) -> None:
        configure_logging()

        root_logger = logging.getLogger()
        assert root_logger.level == logging.INFO
        assert len(root_logger.handlers) == 1

    def test_console_format_uses_console_renderer(self) -> None:
        configure_logging(level="DEBUG", fmt="console")

        root_logger = logging.getLogger()
        assert root_logger.level == logging.DEBUG

    def test_unknown_level_falls_back_to_info(self) -> None:
        configure_logging(level="NOT_A_LEVEL")

        assert logging.getLogger().level == logging.INFO

    def test_silences_noisy_third_party_loggers(self) -> None:
        configure_logging()

        for noisy in ("httpx", "httpcore", "urllib3", "werkzeug"):
            assert logging.getLogger(noisy).level == logging.WARNING

    def test_replaces_existing_handlers_not_accumulates(self) -> None:
        configure_logging()
        configure_logging()

        assert len(logging.getLogger().handlers) == 1
