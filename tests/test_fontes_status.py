"""Situação das fontes de dados na Visão Geral."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from unittest.mock import patch

import httpx
import pytest

from app.services import fontes_status as fs
from app.services.fontes_status import FonteStatus
from app.services.health_checker import CheckResult


def _cliente(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _rodar(fid: str, configurada: bool, checagem, handler=lambda r: httpx.Response(200, json={})) -> FonteStatus:
    async def _go() -> FonteStatus:
        async with _cliente(handler) as c:
            return await fs._checar(c, fid, fid.title(), configurada, checagem)

    return asyncio.run(_go())


@pytest.fixture
def env_completo(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in {
        "ZENDESK_SUBDOMAIN": "acme",
        "ZENDESK_EMAIL": "ti@acme",
        "ZENDESK_API_TOKEN": "tok",
        "AZURE_TENANT_ID": "t",
        "AZURE_CLIENT_ID": "c",
        "AZURE_CLIENT_SECRET": "s",
        "CLICKUP_TOKEN": "pk",
        "ACRONIS_BASE_URL": "https://acr.example",
        "ACRONIS_CLIENT_ID": "a",
        "ACRONIS_CLIENT_SECRET": "b",
    }.items():
        monkeypatch.setenv(k, v)


# ── estados ─────────────────────────────────────────────────────────────────


def test_nao_configurada_nao_chama_nada() -> None:
    def explode(_r: httpx.Request) -> httpx.Response:
        raise AssertionError("não deveria chamar a rede")

    st = _rodar("clickup", False, fs._clickup, explode)
    assert st.estado == "nao_configurada"
    assert st.latencia_ms is None


def test_online(env_completo: None) -> None:
    st = _rodar("clickup", True, fs._clickup, lambda r: httpx.Response(200, json={"user": {"id": 1}}))
    assert st.estado == "online"
    assert st.latencia_ms is not None


def test_http_de_erro_vira_offline_so_com_codigo(env_completo: None) -> None:
    st = _rodar("clickup", True, fs._clickup, lambda r: httpx.Response(401, text="token pk_segredo inválido"))
    assert st.estado == "offline"
    assert st.detalhe == "HTTP 401"


def test_erro_de_rede_nao_vaza_mensagem(env_completo: None) -> None:
    def falha(r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"falhou ao conectar em {r.url}")

    st = _rodar("acronis", True, fs._acronis, falha)
    assert st.estado == "offline"
    assert st.detalhe == "ConnectError"


def test_prazo_estourado_vira_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fs, "PRAZO_S", 0.05)

    async def lenta(_c: httpx.AsyncClient) -> str:
        await asyncio.sleep(1)
        return ""

    st = _rodar("lenta", True, lenta)
    assert st.estado == "offline"
    assert "sem resposta" in st.detalhe


# ── checagens específicas ───────────────────────────────────────────────────


def test_zendesk_credencial_invalida_volta_anonimo(env_completo: None) -> None:
    anonimo = lambda r: httpx.Response(200, json={"user": {"id": None, "name": "Anonymous user"}})  # noqa: E731
    assert _rodar("zendesk", True, fs._zendesk, anonimo).estado == "offline"

    def ok(r: httpx.Request) -> httpx.Response:
        assert r.url.host == "acme.zendesk.com"
        assert r.headers["Authorization"].startswith("Basic ")
        return httpx.Response(200, json={"user": {"id": 42}})

    assert _rodar("zendesk", True, fs._zendesk, ok).estado == "online"


def test_m365_e_acronis_exigem_access_token(env_completo: None) -> None:
    sem_token = lambda r: httpx.Response(200, json={})  # noqa: E731
    com_token = lambda r: httpx.Response(200, json={"access_token": "x"})  # noqa: E731
    assert _rodar("m365", True, fs._m365, sem_token).estado == "offline"
    assert _rodar("m365", True, fs._m365, com_token).estado == "online"
    assert _rodar("acronis", True, fs._acronis, com_token).estado == "online"


def test_zabbix_mostra_versao() -> None:
    async def ok(_self: object) -> CheckResult:
        return CheckResult(ok=True, latency_ms=3.0, extra={"version": "7.4.14"})

    with patch.object(fs.HealthChecker, "check_zabbix", ok):
        st = _rodar("zabbix", True, fs._zabbix)
    assert st.estado == "online" and st.detalhe == "versão 7.4.14"


def test_influxdb_offline() -> None:
    async def fora(_self: object) -> CheckResult:
        return CheckResult(ok=False, latency_ms=2.0, error="HTTP 503")

    with patch.object(fs.HealthChecker, "check_influxdb", fora):
        st = _rodar("influxdb", True, fs._influxdb)
    assert st.estado == "offline" and st.detalhe == "HTTP 503"


def test_configuracao_depende_das_variaveis(env_completo: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB__TOKEN", raising=False)
    conf = {fid: c for fid, _n, c, _f in fs._fontes()}
    assert conf["zendesk"] and conf["m365"] and conf["clickup"] and conf["acronis"]
    assert conf["github"] is False
    monkeypatch.delenv("CLICKUP_TOKEN")
    assert {fid: c for fid, _n, c, _f in fs._fontes()}["clickup"] is False


# ── página ──────────────────────────────────────────────────────────────────


def test_visao_geral_mostra_fontes(authed_client) -> None:
    fontes = [
        FonteStatus(id="influxdb", nome="InfluxDB", estado="online", latencia_ms=4, verificado_em="01/10 19:30"),
        FonteStatus(
            id="zendesk",
            nome="Zendesk",
            estado="offline",
            detalhe="HTTP 401",
            latencia_ms=90,
            verificado_em="01/10 19:30",
        ),
        FonteStatus(id="github", nome="GitHub", estado="nao_configurada", detalhe="credenciais ausentes"),
    ]
    with patch("app.services.fontes_status.status_fontes", return_value=fontes):
        html = authed_client.get("/gov/").get_data(as_text=True)
    assert 'aria-label="Fontes de dados"' in html
    assert "Zendesk: offline — HTTP 401 (90 ms)" in html
    assert "GitHub" not in html  # fonte não configurada (decisão: sem integração) não aparece
    assert "bg-red-500" in html and "bg-green-500" in html
    assert "Fonte: InfluxDB" not in html
