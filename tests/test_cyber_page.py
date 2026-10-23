"""Módulo Cyber: página /gov/cyber, helpers de investigação e correlação NOC."""

from __future__ import annotations

import pytest

from itgov.api.v1 import cyber


@pytest.fixture
def com_acronis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACRONIS_BASE_URL", "https://acronis.test")


_INCIDENTES = [
    {
        "resource_name": "pc-rh-01",
        "alert_type": "EDRIncidentDetected",
        "severity": "critical",
        "mitigation": "",
        "time": "2026-10-23T10:00:00Z",
    },
    {
        "resource_name": "pc-fin-02",
        "alert_type": "RansomwareDetected",
        "severity": "high",
        "mitigation": "mitigado",
        "time": "2026-10-23T09:00:00Z",
    },
    {
        "resource_name": "srv-web",
        "alert_type": "SuspiciousLogin",
        "severity": "warning",
        "mitigation": "",
        "time": "2026-10-23T08:00:00Z",
    },
]

_ACRONIS = {
    "total_agents": 294,
    "incidents_total": 400,
    "incidents_not_mitigated": 64,
    "incidents_mitigated": 282,
    "intrusion_attempts": 385,
    "patches_critical": 2,
    "patches_warning": 33,
    "edr_total": 348,
    "incidentes": _INCIDENTES,
}


# ── Helpers puros ───────────────────────────────────────────────────────────


def test_peso_severidade_ordena_do_critico_ao_info() -> None:
    assert cyber.peso_severidade("critical") > cyber.peso_severidade("high")
    assert cyber.peso_severidade("high") > cyber.peso_severidade("warning")
    assert cyber.peso_severidade("warning") > cyber.peso_severidade("info")
    assert cyber.peso_severidade("CRÍTICO") == cyber.peso_severidade("critical")
    assert cyber.peso_severidade(None) == 0
    assert cyber.peso_severidade("qualquer-coisa") == 0


def test_filtrar_por_severidade_exata() -> None:
    r = cyber.filtrar_incidentes(_INCIDENTES, severidade="critical")
    assert [i["resource_name"] for i in r] == ["pc-rh-01"]


def test_filtrar_por_tipo_substring_case_insensitive() -> None:
    r = cyber.filtrar_incidentes(_INCIDENTES, tipo="ransomware")
    assert [i["resource_name"] for i in r] == ["pc-fin-02"]


def test_filtrar_por_busca_casa_maquina_ou_tipo() -> None:
    assert len(cyber.filtrar_incidentes(_INCIDENTES, busca="pc-")) == 2
    assert len(cyber.filtrar_incidentes(_INCIDENTES, busca="login")) == 1


def test_filtros_combinam_por_e() -> None:
    assert cyber.filtrar_incidentes(_INCIDENTES, severidade="critical", tipo="ransomware") == []


def test_filtrar_sem_criterio_retorna_tudo() -> None:
    assert cyber.filtrar_incidentes(_INCIDENTES) == _INCIDENTES


# ── Correlação NOC ───────────────────────────────────────────────────────────


def test_montar_noc_critico_quando_ha_incidente_nao_mitigado() -> None:
    noc = cyber.montar_noc(_ACRONIS, {"enabled": True, "total": 0, "counts": {}}, [])
    assert noc["criticidade"] == "critico"
    assert noc["acronis"]["incidents_not_mitigated"] == 64


def test_montar_noc_critico_quando_fortigate_fora() -> None:
    forti = [{"name": "fw1", "api_up": True, "wans": []}, {"name": "fw2", "api_up": False, "wans": []}]
    noc = cyber.montar_noc({}, {"enabled": False}, forti)
    assert noc["forti"] == {"total": 2, "online": 1, "fora": 1, "wan_down": 0}
    assert noc["criticidade"] == "critico"


def test_montar_noc_conta_links_wan_caidos() -> None:
    forti = [{"name": "fw1", "api_up": True, "wans": [{"status": "down"}, {"status": "up"}]}]
    noc = cyber.montar_noc({}, {"enabled": False}, forti)
    assert noc["forti"]["wan_down"] == 1


def test_montar_noc_atencao_com_problema_zabbix_sem_critico() -> None:
    noc = cyber.montar_noc(
        {"incidents_not_mitigated": 0, "patches_critical": 0, "intrusion_attempts": 0},
        {"enabled": True, "total": 3, "counts": {"Alto": 3}},
        [{"name": "fw", "api_up": True, "wans": []}],
    )
    assert noc["criticidade"] == "atencao"
    assert noc["zabbix"]["total"] == 3


def test_montar_noc_ok_quando_tudo_limpo() -> None:
    noc = cyber.montar_noc(
        {"incidents_not_mitigated": 0, "patches_critical": 0, "intrusion_attempts": 0},
        {"enabled": True, "total": 0, "counts": {}},
        [{"name": "fw", "api_up": True, "wans": []}],
    )
    assert noc["criticidade"] == "ok"


def test_montar_noc_tolera_fontes_nulas() -> None:
    noc = cyber.montar_noc(None, None, None)
    assert noc["criticidade"] == "ok"
    assert noc["forti"]["total"] == 0


# ── Assembler + cache ────────────────────────────────────────────────────────


def _mock_fontes(monkeypatch: pytest.MonkeyPatch) -> None:
    cyber._cache_data = None
    monkeypatch.setattr(cyber, "_coletar_acronis", lambda: _ACRONIS)
    monkeypatch.setattr(cyber, "_coletar_zabbix", lambda: {"enabled": True, "total": 2, "counts": {"Alto": 2}})
    monkeypatch.setattr(cyber, "_coletar_forti", lambda: [{"name": "fw", "api_up": True, "wans": []}])


def test_montar_dashboard_junta_as_tres_fontes(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_fontes(monkeypatch)
    d = cyber.montar_dashboard()
    assert len(d["incidentes"]) == 3
    assert d["kpis"]["incidents_not_mitigated"] == 64
    assert d["noc"]["criticidade"] == "critico"


def test_coletar_degrada_quando_fonte_falha(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom() -> dict:
        raise RuntimeError("influx caiu")

    monkeypatch.setattr("itgov.api.v1.acronis_backup.get_cached_acronis_summary", _boom)
    assert cyber._coletar_acronis() == {}


# ── Página e endpoint JSON ───────────────────────────────────────────────────


def test_pagina_cyber_e_somente_leitura() -> None:
    from app.permissoes import POR_CHAVE

    assert POR_CHAVE["cyber"].alteravel is False


def test_pagina_cyber_404_sem_acronis(authed_client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ACRONIS_BASE_URL", raising=False)
    assert authed_client.get("/gov/cyber").status_code == 404


def test_pagina_cyber_renderiza_abas_e_incidentes(authed_client, com_acronis: None, monkeypatch) -> None:
    _mock_fontes(monkeypatch)
    resp = authed_client.get("/gov/cyber")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "NOC de Segurança" in html
    assert "pc-rh-01" in html
    assert "Incidentes (3)" in html


def test_pagina_cyber_filtra_por_severidade(authed_client, com_acronis: None, monkeypatch) -> None:
    _mock_fontes(monkeypatch)
    html = authed_client.get("/gov/cyber?severidade=critical").get_data(as_text=True)
    assert "pc-rh-01" in html
    assert "pc-fin-02" not in html


def test_endpoint_noc_json(authed_client, com_acronis: None, monkeypatch) -> None:
    _mock_fontes(monkeypatch)
    resp = authed_client.get("/api/v1/cyber/noc")
    assert resp.status_code == 200
    assert resp.get_json()["criticidade"] == "critico"
