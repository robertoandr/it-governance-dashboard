"""Tests for collector/jobs/m365_secure_score_controls_collector.py."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import config as _cfg

_mock_settings = MagicMock()
_mock_settings.AZURE_TENANT_ID = "tenant"
_mock_settings.AZURE_CLIENT_ID = "client"
_mock_settings.AZURE_CLIENT_SECRET = "secret"
_mock_settings.INFLUX_URL = "http://localhost:8086"
_mock_settings.INFLUX_TOKEN = "influx-tok"
_mock_settings.INFLUX_ORG = "testorg"
_mock_settings.INFLUX_BUCKET_RAW = "governance_raw"
_cfg.settings = _mock_settings

from collector.jobs.m365_secure_score_controls_collector import (  # noqa: E402
    SecureScoreControlsCollector,
    _build_point,
    run,
)

_COLLECTED_AT = datetime(2026, 7, 6, 12, 0, tzinfo=UTC)


def _perfil(**kwargs) -> dict:
    defaults = {
        "controlName": "EnableMFA",
        "controlCategory": "Identity",
        "implementationStatus": "notImplemented",
        "score": 0.0,
        "maxScore": 10.0,
    }
    defaults.update(kwargs)
    return defaults


class TestBuildPoint:
    def test_ponto_contem_tags_e_fields_esperados(self) -> None:
        point = _build_point(_perfil(), _COLLECTED_AT)
        lp = point.to_line_protocol()

        assert "m365_secure_score_controls" in lp
        assert "control_name=EnableMFA" in lp
        assert "category=Identity" in lp
        assert "max_score=10" in lp
        assert "current_score=0" in lp
        assert "on=false" in lp

    def test_controle_implementado_grava_on_true(self) -> None:
        point = _build_point(_perfil(implementationStatus="implemented", score=10.0), _COLLECTED_AT)

        assert "on=true" in point.to_line_protocol()

    def test_campos_ausentes_nao_quebram(self) -> None:
        point = _build_point({}, _COLLECTED_AT)
        lp = point.to_line_protocol()

        assert "control_name=unknown" in lp
        assert "category=Outros" in lp
        assert "max_score=0" in lp


class TestSecureScoreControlsCollectorInitFalhaSemCredenciais:
    def test_falha_sem_azure_credentials(self) -> None:
        _cfg.settings.AZURE_TENANT_ID = ""
        try:
            with patch("collector.jobs.m365_secure_score_controls_collector.settings", _cfg.settings):
                try:
                    SecureScoreControlsCollector()
                    raised = False
                except RuntimeError:
                    raised = True
            assert raised
        finally:
            _cfg.settings.AZURE_TENANT_ID = "tenant"


class TestCollect:
    def test_escreve_um_ponto_por_controle(self, monkeypatch) -> None:
        perfis = [_perfil(controlName="A", maxScore=20.0), _perfil(controlName="B", maxScore=5.0)]

        collector = SecureScoreControlsCollector.__new__(SecureScoreControlsCollector)
        monkeypatch.setattr(collector, "_fetch_control_profiles", lambda: perfis)

        written = {}

        class _FakeWriteApi:
            def write(self, bucket, record):
                written["bucket"] = bucket
                written["record"] = record

        class _FakeClient:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def write_api(self, write_options=None):
                return _FakeWriteApi()

        with patch(
            "collector.jobs.m365_secure_score_controls_collector.InfluxDBClient",
            return_value=_FakeClient(),
        ):
            collector.collect()

        assert len(written["record"]) == 2
        assert written["bucket"] == "governance_raw"

    def test_lista_vazia_nao_escreve(self, monkeypatch) -> None:
        collector = SecureScoreControlsCollector.__new__(SecureScoreControlsCollector)
        monkeypatch.setattr(collector, "_fetch_control_profiles", lambda: [])

        with patch("collector.jobs.m365_secure_score_controls_collector.InfluxDBClient") as mock_client:
            collector.collect()

        mock_client.assert_not_called()


class TestRun:
    def test_run_ignora_sem_credenciais(self, monkeypatch) -> None:
        monkeypatch.setattr(_cfg.settings, "AZURE_TENANT_ID", "")
        with patch(
            "collector.jobs.m365_secure_score_controls_collector.settings",
            _cfg.settings,
        ):
            run()  # não deve levantar exceção
        monkeypatch.setattr(_cfg.settings, "AZURE_TENANT_ID", "tenant")

    def test_run_captura_excecao_do_collect(self) -> None:
        with patch("collector.jobs.m365_secure_score_controls_collector.SecureScoreControlsCollector") as mock_cls:
            mock_cls.return_value.collect.side_effect = RuntimeError("boom")
            run()  # não deve propagar a exceção
