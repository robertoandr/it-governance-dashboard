"""Testes para jobs/acronis_collector.py."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

import config as _cfg

# ── Injetar settings mockados antes de importar o módulo ────────────────────
_mock_settings = MagicMock()
_mock_settings.ACRONIS_BASE_URL = "https://acronis.example.com"
_mock_settings.ACRONIS_CLIENT_ID = "client-id"
_mock_settings.ACRONIS_CLIENT_SECRET = "client-secret"
_mock_settings.INFLUX_URL = "http://influxdb:8086"
_mock_settings.INFLUX_TOKEN = "influx-tok"
_mock_settings.INFLUX_ORG = "testorg"
_mock_settings.INFLUX_BUCKET_RAW = "governance_raw"
_cfg.settings = _mock_settings

from collector.jobs.acronis_collector import AcronisCollector, run  # noqa: E402

# ── Helpers ──────────────────────────────────────────────────────────────────


def _coletor() -> AcronisCollector:
    return AcronisCollector(
        base_url="https://acronis.example.com",
        client_id="client-id",
        client_secret="client-secret",
    )


def _resposta_token(expires_in: int = 3600) -> MagicMock:
    mock = MagicMock()
    mock.status_code = 200
    mock.json.return_value = {"access_token": "tok-abc", "expires_in": expires_in}
    mock.raise_for_status = MagicMock()
    return mock


def _resposta_json(payload: dict) -> MagicMock:
    mock = MagicMock()
    mock.status_code = 200
    mock.json.return_value = payload
    mock.raise_for_status = MagicMock()
    return mock


# ── Autenticação ─────────────────────────────────────────────────────────────


class TestGetToken:
    def test_token_adquirido_com_credenciais_basicas(self):
        coletor = _coletor()
        with patch("collector.jobs.acronis_collector.requests.post", return_value=_resposta_token()) as mock_post:
            token = coletor._get_token()

        assert token == "tok-abc"
        _, kwargs = mock_post.call_args
        assert "Basic " in kwargs["headers"]["Authorization"]

    def test_token_cacheado_nao_faz_nova_requisicao(self):
        coletor = _coletor()
        with patch("collector.jobs.acronis_collector.requests.post", return_value=_resposta_token()) as mock_post:
            coletor._get_token()
            coletor._get_token()

        assert mock_post.call_count == 1

    def test_token_expirado_renova(self):
        coletor = _coletor()
        with patch(
            "collector.jobs.acronis_collector.requests.post", return_value=_resposta_token(expires_in=1)
        ) as mock_post:
            coletor._get_token()
            # simula expiração forçando o timestamp para o passado
            coletor._token_expires_at = time.monotonic() - 1
            coletor._get_token()

        assert mock_post.call_count == 2

    def test_levanta_runtime_error_sem_access_token(self):
        coletor = _coletor()
        resposta = MagicMock()
        resposta.json.return_value = {"error": "invalid_client"}
        resposta.raise_for_status = MagicMock()
        with (
            patch("collector.jobs.acronis_collector.requests.post", return_value=resposta),
            pytest.raises(RuntimeError, match="token não retornado"),
        ):
            coletor._get_token()


# ── HTTP helpers ─────────────────────────────────────────────────────────────


class TestGet:
    def test_get_retorna_json_em_sucesso(self):
        coletor = _coletor()
        coletor._cached_token = "tok"
        coletor._token_expires_at = time.monotonic() + 3600
        payload = {"items": [{"id": "1"}]}
        with patch("collector.jobs.acronis_collector.requests.get", return_value=_resposta_json(payload)):
            resultado = coletor._get("/api/algum/endpoint")

        assert resultado == payload

    def test_get_faz_retry_em_429(self):
        coletor = _coletor()
        coletor._cached_token = "tok"
        coletor._token_expires_at = time.monotonic() + 3600

        resp_429 = MagicMock()
        resp_429.status_code = 429
        resp_429.headers = {"Retry-After": "1"}

        resp_ok = _resposta_json({"items": []})

        with (
            patch("collector.jobs.acronis_collector.requests.post", return_value=_resposta_token()),
            patch("collector.jobs.acronis_collector.requests.get", side_effect=[resp_429, resp_ok]),
            patch("collector.jobs.acronis_collector.time.sleep") as mock_sleep,
        ):
            resultado = coletor._get("/api/algum/endpoint")

        mock_sleep.assert_called_once_with(1)
        assert resultado == {"items": []}

    def test_get_levanta_erro_apos_max_tentativas(self):
        coletor = _coletor()
        coletor._cached_token = "tok"
        coletor._token_expires_at = time.monotonic() + 3600

        resp_429 = MagicMock()
        resp_429.status_code = 429
        resp_429.headers = {"Retry-After": "0"}

        with (
            patch("collector.jobs.acronis_collector.requests.post", return_value=_resposta_token()),
            patch("collector.jobs.acronis_collector.requests.get", return_value=resp_429),
            patch("collector.jobs.acronis_collector.time.sleep"),
            pytest.raises(RuntimeError, match="tentativas esgotadas"),
        ):
            coletor._get("/api/algum/endpoint")


# ── Paginação cursor-based ───────────────────────────────────────────────────


class TestPaginar:
    def test_pagina_unica_sem_cursor(self):
        coletor = _coletor()
        coletor._cached_token = "tok"
        coletor._token_expires_at = time.monotonic() + 3600

        pagina = {"items": [{"id": "a"}, {"id": "b"}]}
        with patch("collector.jobs.acronis_collector.requests.get", return_value=_resposta_json(pagina)):
            resultado = list(coletor._paginar("/api/recursos"))

        assert len(resultado) == 2

    def test_duas_paginas_com_cursor(self):
        coletor = _coletor()
        coletor._cached_token = "tok"
        coletor._token_expires_at = time.monotonic() + 3600

        pagina1 = {"items": [{"id": "1"}], "paging": {"cursors": {"after": "cursor-xyz"}}}
        pagina2 = {"items": [{"id": "2"}]}

        respostas = [_resposta_json(pagina1), _resposta_json(pagina2)]
        with patch("collector.jobs.acronis_collector.requests.get", side_effect=respostas):
            resultado = list(coletor._paginar("/api/recursos"))

        assert len(resultado) == 2
        assert resultado[0]["id"] == "1"
        assert resultado[1]["id"] == "2"

    def test_para_quando_items_vazio(self):
        coletor = _coletor()
        coletor._cached_token = "tok"
        coletor._token_expires_at = time.monotonic() + 3600

        pagina = {"items": [], "paging": {"cursors": {"after": "cursor-xyz"}}}
        with patch("collector.jobs.acronis_collector.requests.get", return_value=_resposta_json(pagina)):
            resultado = list(coletor._paginar("/api/recursos"))

        assert resultado == []


# ── Coleta de agentes ────────────────────────────────────────────────────────


class TestColetarAgentes:
    def test_conta_agentes_por_status(self):
        coletor = _coletor()
        # API usa campo booleano 'online'; outdated = current != latest
        agentes = [
            {
                "online": True,
                "installer_version": {"current": {"release_id": "26.5.1"}, "latest": {"release_id": "26.5.1"}},
                "tenant": {"name": "tenant-a"},
            },
            {
                "online": True,
                "installer_version": {"current": {"release_id": "26.5.1"}, "latest": {"release_id": "26.5.1"}},
                "tenant": {"name": "tenant-a"},
            },
            {"online": False, "installer_version": {}, "tenant": {"name": "tenant-b"}},
            {"online": False, "installer_version": {}, "tenant": {"name": "tenant-b"}},
            {"online": False, "installer_version": {}, "tenant": {"name": "tenant-b"}},
            # online mas desatualizado
            {
                "online": True,
                "installer_version": {"current": {"release_id": "25.7.1"}, "latest": {"release_id": "26.5.1"}},
                "tenant": {"name": "tenant-a"},
            },
        ]
        with patch.object(coletor, "_paginar", return_value=iter(agentes)):
            agentes_count, compliance, inventario = coletor._coletar_agentes()

        assert agentes_count == {"total": 6, "online": 3, "offline": 3, "outdated": 1}
        assert compliance["total"] == 6.0
        assert compliance["outdated"] == 1.0
        assert inventario == {"tenant-a": 3, "tenant-b": 3}

    def test_sem_agentes_retorna_zeros(self):
        coletor = _coletor()
        with patch.object(coletor, "_paginar", return_value=iter([])):
            agentes_count, compliance, inventario = coletor._coletar_agentes()

        assert agentes_count == {"online": 0, "offline": 0, "outdated": 0, "total": 0}
        assert compliance == {"total": 0.0, "outdated": 0.0, "compliant_pct": 0.0}
        assert inventario == {}


# ── Ciclo completo (collect) ─────────────────────────────────────────────────


class TestCollect:
    def test_escreve_measurements_no_influxdb(self):
        coletor = _coletor()

        with (
            patch.object(
                coletor,
                "_coletar_agentes",
                return_value=(
                    {"online": 5, "offline": 1, "outdated": 0, "total": 6},
                    {"total": 6.0, "outdated": 0.0, "compliant_pct": 100.0},
                    {"tenant-a": 6},
                ),
            ),
            patch("collector.jobs.acronis_collector.InfluxDBClient") as mock_influx,
        ):
            mock_write_api = MagicMock()
            mock_influx.return_value.__enter__.return_value.write_api.return_value = mock_write_api

            coletor.collect()

        mock_write_api.write.assert_called_once()
        _, kwargs = mock_write_api.write.call_args
        pontos = kwargs.get("record", [])
        nomes = [p._name for p in pontos]
        assert "gov_acronis_agents" in nomes
        assert "gov_acronis_version_compliance" in nomes
        assert "gov_acronis_tenant_inventory" in nomes


# ── Entry point run() ────────────────────────────────────────────────────────


class TestRun:
    def test_run_nao_levanta_excecao_em_falha(self):
        with patch("collector.jobs.acronis_collector.AcronisCollector") as mock_cls:
            mock_cls.return_value.collect.side_effect = RuntimeError("Falha de conexão")
            # não deve propagar a exceção
            run()

    def test_run_instancia_coletor_com_settings(self):
        with patch("collector.jobs.acronis_collector.AcronisCollector") as mock_cls:
            mock_cls.return_value.collect = MagicMock()
            run()

        mock_cls.assert_called_once_with(
            base_url=_mock_settings.ACRONIS_BASE_URL,
            client_id=_mock_settings.ACRONIS_CLIENT_ID,
            client_secret=_mock_settings.ACRONIS_CLIENT_SECRET,
        )
