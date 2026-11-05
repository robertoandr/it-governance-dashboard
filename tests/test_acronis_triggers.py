"""Triggers do Acronis: classificação dos alertas, texto, Zabbix, arquivo e bloco do NOC."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
import requests

from app.services import acronis_triggers as at


def _alerta(tipo: str, maquina: str = "pc-01", sev: str = "warning", **detalhes: Any) -> dict[str, Any]:
    return {"type": tipo, "severity": sev, "details": {"resourceName": maquina, **detalhes}}


@pytest.fixture
def arquivo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    destino = tmp_path / "acronis-triggers.json"
    monkeypatch.setattr(at, "_ARQUIVO", str(destino))
    return destino


# ── Classificação ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("alerta", "esperado"),
    [
        (_alerta("EDRIncidentDetected", sev="critical", isMitigated="false"), "edr_critico"),
        (_alerta("EDRIncidentDetected", sev="critical", isMitigated="true"), None),
        (_alerta("EDRIncidentDetected", sev="warning", isMitigated="false"), None),
        (_alerta("EDRIncidentDetected", sev="warning", isMalicious="true"), "malware"),
        (_alerta("MaliciousUrlDetected", sev="error"), "malware"),
        (_alerta("QuarantineFailed"), "malware"),
        (_alerta("RansomwareDetected", sev="critical"), "malware"),
        (_alerta("ProtectionServiceNotWorking", sev="error"), "protecao"),
        (_alerta("FailToIsolate", sev="critical"), "protecao"),
        (_alerta("WSCProductConflictWithAntimalwareProtection"), "protecao"),
        (_alerta("OfferingItemIsNotSufficient", sev="critical"), "cota"),
        (_alerta("MachineOffline20"), "offline"),
        (_alerta("MachineOffline30", sev="critical"), "offline"),
        (_alerta("BackupFailed", sev="error"), "backup"),
        (_alerta("BackupDidNotStart", sev="critical"), "backup"),
        (_alerta("BackupWarning", sev="warning"), None),
        (_alerta("MiMonitoringSoftwareInstalled"), None),
        (_alerta("MiMonitoringMissingPatches"), None),
        ({"type": None}, None),
    ],
)
def test_categoria_do_alerta(alerta: dict[str, Any], esperado: str | None) -> None:
    assert at.categoria_do_alerta(alerta) == esperado


def test_classificar_junta_maquinas_sem_repetir_e_traz_todas_as_categorias() -> None:
    alertas = [
        _alerta("MachineOffline30", "Zeta"),
        _alerta("MachineOffline20", "alfa"),
        _alerta("MachineOffline30", "Zeta"),
        _alerta("OfferingItemIsNotSufficient", "beta"),
        _alerta("MiMonitoringSoftwareInstalled", "gama"),
        {"type": "QuarantineFailed", "details": {}},
    ]
    por_cat = at.classificar(alertas)
    assert set(por_cat) == {c.chave for c in at.CATEGORIAS}
    assert por_cat["offline"] == ["alfa", "Zeta"]
    assert por_cat["cota"] == ["beta"]
    assert por_cat["malware"] == ["?"]
    assert por_cat["edr_critico"] == []


def test_nome_da_maquina_sem_dominio() -> None:
    por_cat = at.classificar(
        [
            _alerta("MachineOffline30", "DESKTOP-1Q6H26V.grupogadens.com.br"),
            _alerta("MachineOffline30", "10.41.1.20"),
            _alerta("MachineOffline30", "SRV-HV1"),
        ]
    )
    assert por_cat["offline"] == ["10.41.1.20", "DESKTOP-1Q6H26V", "SRV-HV1"]


def test_texto() -> None:
    assert at.texto([]) == ""
    assert at.texto(["pc-01"]) == "1 máquina: pc-01"
    muitas = [f"pc-{i:02d}" for i in range(10)]
    assert at.texto(muitas) == "10 máquinas: " + ", ".join(muitas[:8]) + " e mais 2"
    assert len(at.texto([f"maquina-com-nome-bem-comprido-{i}" for i in range(8)])) <= 250


# ── Zabbix ─────────────────────────────────────────────────────────────────


def test_enviar_zabbix_sem_url_nao_chama(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ZABBIX_URL", raising=False)
    assert at.enviar_zabbix({"cota": ["a"]}) == 0


def test_enviar_zabbix_manda_um_valor_por_item_existente(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZABBIX_URL", "https://zbx.test")
    chamadas: list[tuple[str, Any]] = []

    def falso(metodo: str, params: Any) -> Any:
        chamadas.append((metodo, params))
        if metodo == "item.get":
            return [{"itemid": "1", "key_": "acronis.alerta[cota]"}, {"itemid": "2", "key_": "acronis.alerta[offline]"}]
        return {}

    monkeypatch.setattr("itgov.api.v1.cftv_monitoring._zbx", falso)
    assert at.enviar_zabbix({"cota": ["pc-01"], "offline": []}) == 2
    metodo, valores = chamadas[-1]
    assert metodo == "history.push"
    assert valores == [{"itemid": "1", "value": "1 máquina: pc-01"}, {"itemid": "2", "value": ""}]
    assert chamadas[0][1]["host"] == at.HOST_ZABBIX


def test_enviar_zabbix_falha_vira_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZABBIX_URL", "https://zbx.test")
    monkeypatch.setattr("itgov.api.v1.cftv_monitoring._zbx", MagicMock(side_effect=requests.ConnectionError("fora")))
    assert at.enviar_zabbix({"cota": ["a"]}) == 0


# ── Rodada e painel ─────────────────────────────────────────────────────────


def test_rodada_sem_acronis(monkeypatch: pytest.MonkeyPatch, arquivo: Path) -> None:
    monkeypatch.setattr(at.acronis_cyber, "configurado", lambda: False)
    assert at.rodada() == {"erro": "Acronis não configurado"}
    assert not arquivo.exists()


def test_rodada_grava_arquivo_e_envia(monkeypatch: pytest.MonkeyPatch, arquivo: Path) -> None:
    monkeypatch.setattr(at.acronis_cyber, "configurado", lambda: True)

    async def falso_buscar(limit: int) -> list[dict[str, Any]]:
        return [_alerta("OfferingItemIsNotSufficient", "pc-09", "critical"), _alerta("MiMonitoringSoftwareInstalled")]

    monkeypatch.setattr(at.acronis_cyber, "_buscar_alertas", falso_buscar)
    enviados: list[dict[str, list[str]]] = []
    monkeypatch.setattr(at, "enviar_zabbix", lambda por_cat: enviados.append(por_cat) or 6)

    resumo = at.rodada()
    assert resumo["cota"] == 1 and resumo["offline"] == 0 and resumo["zabbix_itens"] == 6
    assert enviados[0]["cota"] == ["pc-09"]
    dados = json.loads(arquivo.read_text(encoding="utf-8"))
    assert dados["alertas_lidos"] == 2
    cota = next(c for c in dados["categorias"] if c["chave"] == "cota")
    assert cota == {
        "chave": "cota",
        "nome": "Sem licença (cota)",
        "severidade": 3,
        "maquinas": ["pc-09"],
        "total": 1,
        "texto": "1 máquina: pc-09",
    }
    assert at.painel() == dados


def test_rodada_com_acronis_fora_mantem_leitura_anterior(monkeypatch: pytest.MonkeyPatch, arquivo: Path) -> None:
    arquivo.write_text(json.dumps({"em": "2026-11-05T12:00:00+00:00", "categorias": []}), encoding="utf-8")
    monkeypatch.setattr(at.acronis_cyber, "configurado", lambda: True)

    async def falha(limit: int) -> list[dict[str, Any]]:
        raise httpx.ConnectError("fora")

    monkeypatch.setattr(at.acronis_cyber, "_buscar_alertas", falha)
    enviar = MagicMock()
    monkeypatch.setattr(at, "enviar_zabbix", enviar)

    assert at.rodada()["erro"] == "Falha ao ler o Acronis"
    enviar.assert_not_called()  # não zera os triggers por falha de leitura
    dados = at.painel()
    assert dados["em"] == "2026-11-05T12:00:00+00:00"
    assert dados["erro"] == "Falha ao ler o Acronis"


def test_painel_sem_arquivo_ou_invalido(arquivo: Path) -> None:
    assert at.painel() == {}
    arquivo.write_text("não é json", encoding="utf-8")
    assert at.painel() == {}
    arquivo.write_text("[1, 2]", encoding="utf-8")
    assert at.painel() == {}


def test_agendador_nao_liga_em_teste(factory_app) -> None:
    assert at.iniciar_agendador(factory_app) is False


# ── Bloco no NOC ───────────────────────────────────────────────────────────


def test_noc_mostra_triggers_do_acronis(authed_client, monkeypatch: pytest.MonkeyPatch, arquivo: Path) -> None:
    from tests.test_cyber_page import _mock_fontes

    monkeypatch.setenv("ACRONIS_BASE_URL", "https://acronis.test")
    _mock_fontes(monkeypatch)
    arquivo.write_text(
        json.dumps(
            {
                "em": "2026-11-05T13:10:00+00:00",
                "alertas_lidos": 1588,
                "zabbix_itens": 6,
                "categorias": [
                    {
                        "chave": "cota",
                        "nome": "Sem licença (cota)",
                        "severidade": 3,
                        "maquinas": ["pc-09"],
                        "total": 1,
                        "texto": "",
                    },
                    {
                        "chave": "offline",
                        "nome": "Agente offline",
                        "severidade": 2,
                        "maquinas": [],
                        "total": 0,
                        "texto": "",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    html = authed_client.get("/gov/cyber/noc").get_data(as_text=True)
    assert "Triggers do Acronis" in html
    assert "1588 alertas" in html
    assert "pc-09" in html
    assert "Agente offline" in html


def test_noc_sem_leitura_ainda(authed_client, monkeypatch: pytest.MonkeyPatch, arquivo: Path) -> None:
    from tests.test_cyber_page import _mock_fontes

    monkeypatch.setenv("ACRONIS_BASE_URL", "https://acronis.test")
    _mock_fontes(monkeypatch)
    html = authed_client.get("/gov/cyber/noc").get_data(as_text=True)
    assert "Ainda sem leitura" in html
