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
    """Item de secureScoreControlProfiles — chave estável é ``id``."""
    defaults = {
        "id": "aad_mfa_admins",
        "controlCategory": "Identity",
        "maxScore": 10.0,
    }
    defaults.update(kwargs)
    return defaults


def _control_score(**kwargs) -> dict:
    """Item de secureScores.controlScores — referencia via ``controlName``."""
    defaults = {
        "controlName": "aad_mfa_admins",
        "controlCategory": "Identity",
        "score": 0.0,
        "scoreInPercentage": 0.0,
    }
    defaults.update(kwargs)
    return defaults


class TestBuildPoint:
    def test_ponto_contem_tags_e_fields_esperados(self) -> None:
        point = _build_point(_perfil(), _control_score(), _COLLECTED_AT)
        lp = point.to_line_protocol()

        assert "m365_secure_score_controls" in lp
        assert "control_name=aad_mfa_admins" in lp
        assert "category=Identity" in lp
        assert "max_score=10" in lp
        assert "current_score=0" in lp
        assert "on=false" in lp

    def test_pct_100_grava_on_true(self) -> None:
        point = _build_point(_perfil(), _control_score(score=10.0, scoreInPercentage=100.0), _COLLECTED_AT)

        assert "on=true" in point.to_line_protocol()

    def test_pct_parcial_grava_on_false(self) -> None:
        point = _build_point(_perfil(), _control_score(score=5.0, scoreInPercentage=50.0), _COLLECTED_AT)

        assert "on=false" in point.to_line_protocol()

    def test_perfil_ausente_ainda_grava_com_categoria_do_control_score(self) -> None:
        point = _build_point({}, _control_score(controlName="orfao", controlCategory="Apps"), _COLLECTED_AT)
        lp = point.to_line_protocol()

        assert "control_name=orfao" in lp
        assert "category=Apps" in lp
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
    def test_escreve_um_ponto_por_control_score_via_join(self, monkeypatch) -> None:
        perfis = [_perfil(id="a", maxScore=20.0), _perfil(id="b", maxScore=5.0)]
        scores = [_control_score(controlName="a"), _control_score(controlName="b")]

        collector = SecureScoreControlsCollector.__new__(SecureScoreControlsCollector)
        monkeypatch.setattr(collector, "_fetch_control_profiles", lambda: perfis)
        monkeypatch.setattr(collector, "_fetch_control_scores", lambda: scores)

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

    def test_catalogo_maior_que_control_scores_nao_gera_linhas_fantasma(self, monkeypatch) -> None:
        """Regressão do bug real: 449 perfis no catálogo vs 73 control_scores —
        só os control_scores (dados reais do tenant) viram pontos."""
        perfis = [_perfil(id=f"c{i}") for i in range(449)]
        scores = [_control_score(controlName="c0"), _control_score(controlName="c1")]

        collector = SecureScoreControlsCollector.__new__(SecureScoreControlsCollector)
        monkeypatch.setattr(collector, "_fetch_control_profiles", lambda: perfis)
        monkeypatch.setattr(collector, "_fetch_control_scores", lambda: scores)

        written = {}

        class _FakeWriteApi:
            def write(self, bucket, record):
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

    def test_sem_control_scores_nao_escreve(self, monkeypatch) -> None:
        collector = SecureScoreControlsCollector.__new__(SecureScoreControlsCollector)
        monkeypatch.setattr(collector, "_fetch_control_profiles", lambda: [_perfil()])
        monkeypatch.setattr(collector, "_fetch_control_scores", lambda: [])

        with patch("collector.jobs.m365_secure_score_controls_collector.InfluxDBClient") as mock_client:
            collector.collect()

        mock_client.assert_not_called()


class TestFetchControlScores:
    def test_retorna_control_scores_do_primeiro_valor(self) -> None:
        collector = SecureScoreControlsCollector.__new__(SecureScoreControlsCollector)
        payload = {"value": [{"controlScores": [{"controlName": "x"}]}]}
        with patch.object(collector, "_get", return_value=payload):
            resultado = collector._fetch_control_scores()

        assert resultado == [{"controlName": "x"}]

    def test_retorna_vazio_quando_value_vazio(self) -> None:
        collector = SecureScoreControlsCollector.__new__(SecureScoreControlsCollector)
        with patch.object(collector, "_get", return_value={"value": []}):
            resultado = collector._fetch_control_scores()

        assert resultado == []


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
