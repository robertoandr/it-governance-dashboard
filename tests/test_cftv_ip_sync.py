"""Agente que corrige no Zabbix o IP das câmeras que mudaram de endereço."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.extensions import db
from app.models.cftv_ip import CftvIpCorrecao
from app.services import cftv_ip_sync as sync
from itgov.services.intelbras_api import CameraGravador, LeituraGravador

_AGORA = datetime(2026, 11, 3, 15, 0, tzinfo=UTC)


def _cam(canal: str, ip: str, status: str = "down", hostid: str | None = None) -> dict[str, Any]:
    return {
        "hostid": hostid or f"h{canal}",
        "host": f"cam-ch{canal}",
        "name": f"Câmera canal {canal}",
        "canal": canal,
        "ip": ip,
        "status": status,
        "is_gravador": False,
    }


def _card(*dispositivos: dict[str, Any], snmp: dict | None = None) -> dict[str, Any]:
    return {
        "titulo": "Shopping_Novo",
        "gravador_host": {"ip": "10.41.102.172", "canais_snmp": snmp or {}},
        "dispositivos": list(dispositivos),
    }


def _acesso(*cameras: CameraGravador, sem_video: list[int] | None = None, idade: timedelta = timedelta(minutes=10)):
    return {
        "10.41.102.172": {
            "leitura": LeituraGravador(canais=24, cameras=list(cameras), sem_video=sem_video or []),
            "lido_ok_em": _AGORA - idade,
        }
    }


# ── candidatos ──────────────────────────────────────────────────────────────


def test_camera_fora_com_ip_novo_no_gravador_vira_candidata() -> None:
    card = _card(_cam("17", "10.41.0.49"), _cam("18", "10.41.0.84", status="up"), _cam("24", "10.41.0.54"))
    acessos = _acesso(
        CameraGravador(canal=17, tipo="ip", ip="10.41.100.177"),
        CameraGravador(canal=18, tipo="ip", ip="10.41.100.176"),  # zabbix up: não mexe
        CameraGravador(canal=24, tipo="ip", ip="10.41.0.54"),  # mesmo IP: nada a corrigir
    )
    [c] = sync.candidatos([card], acessos, _AGORA)
    assert (c.hostid, c.canal, c.ip_antigo, c.ip_novo, c.fonte) == ("h17", 17, "10.41.0.49", "10.41.100.177", "api")


def test_canal_sem_video_leitura_velha_ou_ip_invalido_nao_corrige() -> None:
    card = _card(_cam("1", "10.0.0.1"), _cam("2", "10.0.0.2"), _cam("3", "10.0.0.3"))
    cams = (
        CameraGravador(canal=1, tipo="ip", ip="10.0.0.11"),
        CameraGravador(canal=2, tipo="ip", ip="192.168.1.108"),  # padrão de fábrica
        CameraGravador(canal=3, tipo="ip", ip="8.8.8.8"),  # fora da rede interna
    )
    assert sync.candidatos([card], _acesso(*cams, sem_video=[1]), _AGORA) == []
    # Leitura de mais de LEITURA_FRESCA não serve
    velha = _acesso(CameraGravador(canal=1, tipo="ip", ip="10.0.0.11"), idade=timedelta(hours=5))
    assert sync.candidatos([card], velha, _AGORA) == []


def test_sem_leitura_usa_o_snmp_do_gravador() -> None:
    snmp = {"5": {"status": "Connected", "ip": "10.41.100.7"}, "6": {"status": "Unconnect", "ip": "10.41.100.8"}}
    card = _card(_cam("5", "10.41.100.75"), _cam("6", "10.41.100.80"), snmp=snmp)
    [c] = sync.candidatos([card], {}, _AGORA)
    assert (c.canal, c.ip_novo, c.fonte) == (5, "10.41.100.7", "snmp")


def test_ip_do_proprio_gravador_e_ignorado() -> None:
    card = _card(_cam("1", "10.0.0.1"))
    assert sync.candidatos([card], _acesso(CameraGravador(canal=1, tipo="ip", ip="10.41.102.172")), _AGORA) == []


# ── aplicar ─────────────────────────────────────────────────────────────────


@pytest.fixture
def banco(factory_app) -> Iterator[Any]:
    with factory_app.app_context():
        yield factory_app
        CftvIpCorrecao.query.delete()
        db.session.commit()


@pytest.fixture
def zabbix(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict]]:
    chamadas: list[tuple[str, dict]] = []
    interfaces = {
        "h17": [{"interfaceid": "i1", "ip": "10.41.0.49"}, {"interfaceid": "i2", "ip": "10.41.0.49"}],
        "h18": [{"interfaceid": "i3", "ip": "10.9.9.9"}],
    }

    def _zbx(metodo: str, params: dict) -> Any:
        chamadas.append((metodo, params))
        if metodo == "hostinterface.get":
            return interfaces.get(params["hostids"][0], [])
        return {"interfaceids": [params["interfaceid"]]}

    from itgov.api.v1 import cftv_monitoring

    monkeypatch.setattr(cftv_monitoring, "_zbx", _zbx)
    return chamadas


def _correcao(hostid: str, antigo: str, novo: str) -> sync.Correcao:
    return sync.Correcao(hostid, f"cam-{hostid}", "Câmera", "Shopping_Novo", 17, antigo, novo, "api")


def test_aplicar_troca_as_interfaces_e_registra(banco, zabbix) -> None:
    r = sync.aplicar([_correcao("h17", "10.41.0.49", "10.41.100.177")], set())
    assert r == {sync.CORRIGIDO: 1}
    assert [p for m, p in zabbix if m == "hostinterface.update"] == [
        {"interfaceid": "i1", "ip": "10.41.100.177"},
        {"interfaceid": "i2", "ip": "10.41.100.177"},
    ]
    [linha] = sync.ultimas()
    assert (linha["situacao"], linha["ip_antigo"], linha["ip_novo"]) == ("corrigido", "10.41.0.49", "10.41.100.177")


def test_aplicar_conflito_e_host_sem_interface_no_ip_antigo(banco, zabbix) -> None:
    r = sync.aplicar(
        [_correcao("h17", "10.41.0.49", "10.41.100.5"), _correcao("h18", "10.41.0.84", "10.41.100.176")],
        {"10.41.100.5"},
    )
    assert r == {sync.CONFLITO: 1, sync.FALHOU: 1}
    assert not [m for m, _ in zabbix if m == "hostinterface.update"]
    # O mesmo conflito na rodada seguinte não gera outra linha
    sync.aplicar([_correcao("h17", "10.41.0.49", "10.41.100.5")], {"10.41.100.5"})
    assert CftvIpCorrecao.query.filter_by(situacao=sync.CONFLITO).count() == 1


def test_aplicar_respeita_o_limite_por_rodada(banco, zabbix, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sync, "MAX_POR_RODADA", 1)
    sync.aplicar([_correcao("h17", "10.41.0.49", "10.41.100.177"), _correcao("h18", "10.9.9.9", "10.41.1.1")], set())
    assert CftvIpCorrecao.query.count() == 1


def test_aplicar_zabbix_com_erro_registra_falha(banco, monkeypatch: pytest.MonkeyPatch) -> None:
    from itgov.api.v1 import cftv_monitoring

    def _zbx(metodo: str, params: dict) -> Any:
        raise RuntimeError("Zabbix API: sem permissão")

    monkeypatch.setattr(cftv_monitoring, "_zbx", _zbx)
    assert sync.aplicar([_correcao("h17", "10.41.0.49", "10.41.100.177")], set()) == {sync.FALHOU: 1}


# ── rodada ──────────────────────────────────────────────────────────────────


def test_rodada_le_os_gravadores_e_corrige(banco, zabbix, monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from app.services import cftv_gravadores
    from app.views import dashboards
    from itgov.api.v1 import cftv_monitoring

    card = _card(_cam("17", "10.41.0.49"))
    card["unidade"] = "Shopping"
    monkeypatch.setattr(
        dashboards, "_visao_cftv", lambda f, disparar_leitura=True: SimpleNamespace(visao={"cards": [card]})
    )
    lidos: list = []
    monkeypatch.setattr(cftv_gravadores, "atualizar", lambda alvos: lidos.extend(alvos) or len(alvos))
    monkeypatch.setattr(
        cftv_gravadores, "leituras", lambda: _acesso(CameraGravador(canal=17, tipo="ip", ip="10.41.100.177"))
    )
    monkeypatch.setattr(cftv_monitoring, "get_cached_cftv_summary", lambda: {"devices": [{"ip": "10.41.0.49"}]})
    r = sync.rodada(_AGORA)
    assert [a.ip for a in lidos] == ["10.41.102.172"]
    assert r == {"lidos": 1, "candidatos": 1, sync.CORRIGIDO: 1}


def test_ip_valido() -> None:
    assert sync.ip_valido("10.41.100.7")
    assert not sync.ip_valido("0.0.0.0")
    assert not sync.ip_valido("8.8.8.8")
    assert not sync.ip_valido("fe80::1")
    assert not sync.ip_valido("lixo")
