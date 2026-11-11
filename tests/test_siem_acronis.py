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


# ── Atividades (o que o syslog traria) ──────────────────────────────────────


def _atividade(minutos_atras: float, tipo: str, codigo: str = "ok", usuario: str = "BY_SCHEDULE", **extra: Any) -> dict:
    quando = (_AGORA - timedelta(minutes=minutos_atras)).strftime("%Y-%m-%dT%H:%M:%S.123456789Z")
    return {
        "type": tipo,
        "completedAt": quando,
        "startedByUser": usuario,
        "result": {"code": codigo, **extra.pop("result", {})},
        **extra,
    }


def _legado(minutos_atras: float, titulo: str, usuario: str, **ctx: Any) -> dict:
    return _atividade(
        minutos_atras,
        "F5CD89C9-3CD1-4DBC-97F3-82BF93687B46",
        usuario=usuario,
        context={"title": titulo, "PlanName": "Complete protection", **ctx},
    )


def _atividades() -> list[dict[str, Any]]:
    falha_patch = {"warnings": [{"context": {"errCode": "1603", "product": "DYMOConnect"}}]}
    return [
        _legado(2, "Revoking plan 'Complete protection' with ID 'x' from the resource with ID 'a'", "ana@empresa.com"),
        _legado(3, "Revoking plan 'Complete protection' with ID 'x' from the resource with ID 'b'", "ana@empresa.com"),
        _legado(
            4,
            "Removing agent 'PC-07.empresa.local' from the management server",
            "Empresa",
            Persistent={"Name": "PC-07.empresa.local"},
        ),
        _legado(5, "Editing plan 'Complete protection' with ID 'x'", "ana@empresa.com"),
        _legado(5, "Editing plan 'Complete protection' with ID 'x'", "Empresa"),  # edição do próprio Acronis
        _atividade(
            6,
            "cti.a.p.tm.activity.v1.0~a.rds_ai_assistant.video.convert.v1.0",
            usuario="ti@empresa.com",
            context={"session_id": "s1"},
        ),
        _atividade(
            6,
            "cti.a.p.tm.activity.v1.0~a.rds_ai_assistant.video.summarize.v1.0",
            usuario="ti@empresa.com",
            context={"session_id": "s1"},
        ),
        _atividade(
            7,
            "SystemPatchInstallation",
            "error",
            context={"appName": "DYMO DYMOConnect", "computerName": "PC-01.empresa.local"},
            result=falha_patch,
        ),
        _atividade(
            7, "UserPatchInstallation", "cancelled", context={"appName": "Teams", "computerName": "PC-02"}
        ),  # só pede login
        _atividade(8, "UpdateAgent", "timedout", context={"machineAddress": "NT-09"}),
        _atividade(9, "WinScan"),
        _atividade(  # o nome da máquina vem do agente, não do contexto
            9,
            "UserPatchInstallation",
            "error",
            context={"appName": "Foxit Reader"},
            executorHostname="NT-33.empresa.local",
        ),
        _atividade(
            90, "SystemPatchInstallation", "error", context={"appName": "Velho", "computerName": "ANTIGO"}
        ),  # fora da janela
        {"type": "WinScan", "result": {"code": "ok"}},  # sem data
    ]


def test_resumir_atividades_separa_o_que_importa() -> None:
    r = siem_acronis.resumir_atividades(_atividades(), _INICIO, _AGORA)
    assert r["total"] == 12
    assert r["protecao_removida"] == [
        "ana@empresa.com: plano 'Complete protection' revogado de 2 máquina(s)",
        "Empresa: agente PC-07 removido",
    ]
    assert r["planos_editados"] == ["ana@empresa.com: plano 'Complete protection' editado 1x"]
    assert r["planos_editados_automatico"] == 1
    assert r["sessoes_remotas"] == ["ti@empresa.com: 1 sessão(ões)"]
    assert r["patches_falhos"] == ["NT-33: Foxit Reader", "PC-01: DYMO DYMOConnect (erro 1603)"]
    assert r["patches_falhos_total"] == 2
    assert r["atualizacao_agente_falha"] == ["NT-09"]
    assert r["relevantes"] == 7
    assert ["plano revogado (ok)", 2] in r["por_tipo"]


def test_atividades_sem_nada_relevante_nao_geram_achado() -> None:
    r = siem_acronis.resumir_atividades([_atividade(3, "WinScan"), _atividade(4, "TpScan")], _INICIO, _AGORA)
    assert (r["total"], r["relevantes"]) == (2, 0)
    assert siem_acronis.achados_de_atividades(r) == []


def test_achados_de_atividades() -> None:
    achados = {
        a.assinatura: a
        for a in siem_acronis.achados_de_atividades(siem_acronis.resumir_atividades(_atividades(), _INICIO, _AGORA))
    }
    assert set(achados) == {"acronis-protecao-removida", "acronis-plano-alterado", "acronis-patch-falhou"}
    removida = achados["acronis-protecao-removida"]
    assert (removida.fortigate, removida.severidade) == ("Acronis", "media")
    assert "Empresa: agente PC-07 removido" in removida.evidencias
    assert achados["acronis-plano-alterado"].severidade == "baixa"


def test_analise_junta_atividades_e_manda_para_a_ia(banco, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDE_IA_URL", "http://ia.test/v1")
    monkeypatch.setenv("REDE_IA_KEY", "k")
    monkeypatch.setattr(sa, "alertas_acronis", lambda: [])
    monkeypatch.setattr(sa, "resumo_acronis", lambda: {})
    desde: list[datetime] = []

    def atividades(inicio: datetime) -> list[dict[str, Any]]:
        desde.append(inicio)
        return _atividades()

    monkeypatch.setattr(sa, "atividades_acronis", atividades)
    contextos: list[dict[str, Any]] = []
    monkeypatch.setattr(sa, "perguntar_ia", lambda c: contextos.append(c) or {"achados": []})
    r = sa.analisar(_AGORA)
    assert r["ia"] is True and r["achados"] == 3
    assert desde == [_AGORA - sa.INTERVALO_ANALISE]
    assert contextos[0]["acronis_atividades"]["sessoes_remotas"] == ["ti@empresa.com: 1 sessão(ões)"]
    assert {a.assinatura for a in SiemAchado.query.all()} == {
        "acronis-protecao-removida",
        "acronis-plano-alterado",
        "acronis-patch-falhou",
    }


def test_atividades_acronis_sem_configuracao_ou_com_falha(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from itgov.services import acronis_vulnerabilidades

    monkeypatch.setattr(acronis_vulnerabilidades, "configurado", lambda: False)
    assert sa.atividades_acronis(_INICIO) == []
    monkeypatch.setattr(acronis_vulnerabilidades, "configurado", lambda: True)

    async def falha(_desde: datetime) -> list[dict[str, Any]]:
        raise httpx.ConnectError("fora")

    monkeypatch.setattr(acronis_vulnerabilidades, "buscar_atividades_desde", falha)
    assert sa.atividades_acronis(_INICIO) == []

    async def ok(_desde: datetime) -> list[dict[str, Any]]:
        return [{"type": "WinScan"}]

    monkeypatch.setattr(acronis_vulnerabilidades, "buscar_atividades_desde", ok)
    assert sa.atividades_acronis(_INICIO) == [{"type": "WinScan"}]
