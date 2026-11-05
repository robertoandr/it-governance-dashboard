"""Alertas do Acronis no agente SIEM: resumo da janela, regras, análise e WhatsApp (item 3.5)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.extensions import db
from app.models.siem import SiemAchado, SiemAnalise, SiemEvento
from app.services import siem_acronis
from app.services import siem_agente as sa

_AGORA = datetime(2026, 11, 5, 18, 0, tzinfo=UTC)
_INICIO = _AGORA - timedelta(minutes=15)


def _alerta(minutos_atras: float, tipo: str, maquina: str = "PC-01.grupogadens.com.br", **det: Any) -> dict[str, Any]:
    quando = (_AGORA - timedelta(minutes=minutos_atras)).strftime("%Y-%m-%dT%H:%M:%S.123456789Z")
    return {
        "type": tipo,
        "severity": det.pop("severity", "warning"),
        "createdAt": quando,
        "details": {"resourceName": maquina, **det},
    }


def _alertas() -> list[dict[str, Any]]:
    return [
        _alerta(5, "MaliciousUrlDetected"),
        _alerta(6, "MaliciousUrlDetected"),
        _alerta(7, "MaliciousUrlDetected", "NT-02"),
        _alerta(8, "EDRIncidentDetected", "NT-03", severity="critical", isMitigated="false", isMalicious="false"),
        _alerta(9, "EDRIncidentDetected", "NT-04", severity="critical", isMitigated="true"),
        _alerta(10, "ProtectionServiceNotWorking", "PC-09"),
        _alerta(3, "MiMonitoringSoftwareInstalled", "PC-05", software_name="AnyDesk", software_version="9.0"),
        _alerta(3, "MiMonitoringSoftwareInstalled", "PC-05", software_name="7-Zip", software_version="24"),
        _alerta(60, "MaliciousUrlDetected", "ANTIGO"),  # fora da janela
        {"type": "MaliciousUrlDetected", "details": {"resourceName": "SEM-DATA"}},
    ]


def test_resumir_so_conta_a_janela_e_classifica() -> None:
    r = siem_acronis.resumir(_alertas(), _INICIO, _AGORA)
    assert r["ativos"] == 10 and r["novos"] == 8
    assert r["por_categoria"] == {
        "malware": ["PC-01 (2)", "NT-02"],
        "edr_critico": ["NT-03"],
        "protecao": ["PC-09"],
    }
    assert r["acesso_remoto_instalado"] == ["PC-05: AnyDesk 9.0"]
    assert r["por_tipo"][0] == ["MaliciousUrlDetected", 3]
    assert "ANTIGO" not in str(r) and "SEM-DATA" not in str(r)


def test_resumir_sem_alertas() -> None:
    r = siem_acronis.resumir([], _INICIO, _AGORA)
    assert r["novos"] == 0 and r["por_categoria"] == {} and siem_acronis.achados_por_regra(r) == []


def test_achados_por_regra() -> None:
    achados = {
        a.assinatura: a for a in siem_acronis.achados_por_regra(siem_acronis.resumir(_alertas(), _INICIO, _AGORA))
    }
    assert set(achados) == {
        "acronis-malware",
        "acronis-edr-critico",
        "acronis-protecao-falha",
        "acronis-acesso-remoto-instalado",
    }
    assert {a.fortigate for a in achados.values()} == {"Acronis"}
    assert achados["acronis-malware"].severidade == "alta"
    assert achados["acronis-malware"].evidencias == ["PC-01 (2)", "NT-02"]
    assert achados["acronis-acesso-remoto-instalado"].severidade == "media"


# ── no agente ────────────────────────────────────────────────────────────────


@pytest.fixture
def banco(factory_app, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    monkeypatch.delenv("ZABBIX_URL", raising=False)
    with factory_app.app_context():
        yield factory_app
        SiemEvento.query.delete()
        SiemAchado.query.delete()
        SiemAnalise.query.delete()
        db.session.commit()


def test_analise_sem_fortigate_grava_achados_do_acronis(banco, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REDE_IA_URL", raising=False)
    monkeypatch.setattr(sa, "alertas_acronis", _alertas)
    r = sa.analisar(_AGORA)
    assert r["eventos"] == 0 and r["achados"] == 4
    linhas = SiemAchado.query.all()
    assert {a.fortigate for a in linhas} == {"Acronis"} and {a.fonte for a in linhas} == {"regra"}


def test_ia_recebe_alertas_e_pode_apontar_acronis(banco, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDE_IA_URL", "http://ia.test/v1")
    monkeypatch.setenv("REDE_IA_KEY", "k")
    monkeypatch.setattr(sa, "alertas_acronis", _alertas)
    monkeypatch.setattr(sa, "resumo_acronis", lambda: {})
    contextos: list[dict[str, Any]] = []

    def ia(contexto: dict[str, Any]) -> dict[str, Any]:
        contextos.append(contexto)
        return {
            "achados": [
                {
                    "assinatura": "acronis-malware",
                    "fortigate": "Acronis",
                    "severidade": "alta",
                    "titulo": "URL maliciosa em 2 máquinas",
                    "resumo": "r",
                    "evidencias": ["PC-01"],
                    "recomendacao": "isolar",
                }
            ]
        }

    monkeypatch.setattr(sa, "perguntar_ia", ia)
    r = sa.analisar(_AGORA)
    assert r["ia"] is True
    assert contextos[0]["acronis_alertas"]["novos"] == 8
    malware = SiemAchado.query.filter_by(assinatura="acronis-malware").one()
    assert (malware.fortigate, malware.titulo, malware.fonte) == ("Acronis", "URL maliciosa em 2 máquinas", "ia")
    assert SiemAchado.query.filter_by(assinatura="acronis-edr-critico").one().fonte == "ia"  # regra mantida


def test_texto_graves_deixa_acronis_fora_do_whatsapp(banco, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REDE_IA_URL", raising=False)
    monkeypatch.setattr(sa, "alertas_acronis", _alertas)
    sa.analisar(_AGORA)
    assert SiemAchado.query.filter_by(severidade="alta").count() == 2
    assert sa.texto_graves(_AGORA) == ""


def test_alertas_acronis_sem_configuracao_ou_com_falha(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from itgov.services import acronis_cyber

    monkeypatch.setattr(acronis_cyber, "configurado", lambda: False)
    assert sa.alertas_acronis() == []
    monkeypatch.setattr(acronis_cyber, "configurado", lambda: True)

    async def falha(_limite: int) -> list[dict[str, Any]]:
        raise httpx.ConnectError("fora")

    monkeypatch.setattr(acronis_cyber, "_buscar_alertas", falha)
    assert sa.alertas_acronis() == []
