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
            if "hostids" not in params:  # inventário de IPs do Zabbix
                return [{"ip": ip} for lista in interfaces.values() for ip in (i["ip"] for i in lista)]
            return interfaces.get(params["hostids"][0], [])
        if metodo in ("host.get", "hostgroup.get", "template.get"):
            return [{"host": "cam-ja-existe", "groupid": "g1", "templateid": "t1"}]
        if metodo == "host.create":
            return {"hostids": ["999"]}
        if metodo == "host.update":
            return {"hostids": [params["hostid"]]}
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


# ── cópias, hosts repetidos e câmeras sem host ──────────────────────────────


def test_aplicar_conflito_com_host_online_desativa_a_copia(banco, zabbix) -> None:
    r = sync.aplicar(
        [_correcao("h17", "10.41.0.49", "10.41.100.177")], {"10.41.100.177"}, {"10.41.100.177": "Corredor ch9"}
    )
    assert r == {sync.DESATIVADO: 1}
    assert ("host.update", {"hostid": "h17", "status": 1}) in zabbix
    assert not [m for m, _ in zabbix if m == "hostinterface.update"]
    [linha] = sync.ultimas()
    assert "Corredor ch9" in linha["detalhe"]


def _dev(hostid: str, ip: str, gravador: str, canal: str, status: str = "up", subcat: str = "camera") -> dict[str, Any]:
    return {
        "hostid": hostid,
        "host": f"cam-{hostid}",
        "name": f"Câmera {hostid}",
        "ip": ip,
        "gravador": gravador,
        "canal": canal,
        "status": status,
        "subcat": subcat,
        "is_gravador": False,
    }


def test_duplicados_fica_o_confirmado_pelo_gravador_depois_online_depois_mais_antigo() -> None:
    confirmados = {"Corredor": {9: CameraGravador(canal=9, tipo="ip", ip="10.41.100.177")}}
    devices = [
        _dev("10855", "10.41.100.177", "Corredor", "1"),
        _dev("10900", "10.41.100.177", "Corredor", "9"),  # o gravador confirma: fica
        _dev("10800", "10.41.100.177", "Shopping novo", "23"),
        _dev("10001", "10.41.10.112", "Shopping 1", "1", status="down"),
        _dev("10002", "10.41.10.112", "Shopping 1", "1"),  # online: fica
        _dev("10003", "10.41.10.113", "Shopping 1", "2"),  # sozinho
        _dev("10004", "10.41.10.114", "", "", subcat="facial"),
        _dev("10005", "10.41.10.114", "", "", subcat="facial"),  # facial não entra
    ]
    pares = {(d["hostid"], fica["hostid"]) for d, fica in sync.duplicados(devices, confirmados)}
    assert pares == {("10855", "10900"), ("10800", "10900"), ("10001", "10002")}
    # Sem confirmação nem diferença de status: fica o mais antigo
    [(d, fica)] = sync.duplicados([_dev("20", "10.1.1.1", "A", "1"), _dev("10", "10.1.1.1", "B", "2")], {})
    assert (d["hostid"], fica["hostid"]) == ("20", "10")


def _card_grav(gravador: str, titulo: str, ip: str) -> dict[str, Any]:
    return {"gravador": gravador, "titulo": titulo, "gravador_host": {"ip": ip}}


def test_ips_dos_gravadores_so_leitura_fresca_com_video() -> None:
    cards = [_card_grav("Shopping novo", "Shopping_Novo", "10.41.102.172")]
    acessos = _acesso(
        CameraGravador(canal=1, tipo="ip", ip="10.41.100.10"),
        CameraGravador(canal=2, tipo="ip", ip="10.41.100.11"),  # sem vídeo
        CameraGravador(canal=3, tipo="analogica"),
        CameraGravador(canal=4, tipo="ip", ip="192.168.1.108"),  # IP de fábrica
        CameraGravador(canal=5, tipo="ip", ip="10.41.102.172"),  # o próprio gravador
        sem_video=[2],
    )
    assert {c: cam.ip for c, cam in sync._ips_dos_gravadores(cards, acessos, _AGORA)["Shopping novo"].items()} == {
        1: "10.41.100.10"
    }
    assert sync._ips_dos_gravadores(cards, _acesso(idade=timedelta(hours=3)), _AGORA) == {}


def test_faltantes_um_host_por_camera_e_pula_outro_equipamento() -> None:
    cards = [_card_grav("A", "Grav_A", "10.0.0.1"), _card_grav("B", "Grav_B", "10.0.0.2")]
    confirmados = {
        "A": {
            1: CameraGravador(canal=1, tipo="ip", ip="10.41.1.1"),
            2: CameraGravador(canal=2, tipo="ip", ip="10.41.1.2"),
        },
        "B": {
            7: CameraGravador(canal=7, tipo="ip", ip="10.41.1.1"),  # a mesma câmera do A ch1
            8: CameraGravador(canal=8, tipo="ip", ip="10.41.1.3"),  # já tem host
            9: CameraGravador(canal=9, tipo="ip", ip="10.41.1.4"),  # conferência: outro equipamento
        },
    }
    novos = sync.faltantes(cards, confirmados, {"10.41.1.3"}, {("10.0.0.2", 9)})
    assert [(card["gravador"], cam.canal, tambem) for card, cam, tambem in novos] == [
        ("A", 1, ["Grav_B ch7"]),
        ("A", 2, []),
    ]


def test_arrumar_hosts_desativa_repetido_e_cria_o_que_falta(banco, zabbix) -> None:
    cards = [_card_grav("Shopping novo", "Shopping_Novo", "10.41.102.172")]
    acessos = _acesso(
        CameraGravador(canal=1, tipo="ip", ip="10.41.100.10", modelo="VIP-1230", fabricante="ITB"),
        CameraGravador(canal=2, tipo="ip", ip="10.41.0.49"),  # já tem host (interface do h17)
    )
    devices = [_dev("10", "10.41.5.5", "Shopping novo", "3"), _dev("11", "10.41.5.5", "Shopping novo", "4")]
    r = sync.arrumar_hosts(cards, devices, acessos, _AGORA)
    assert r == {sync.DESATIVADO: 1, sync.CRIADO: 1}
    assert ("host.update", {"hostid": "11", "status": 1}) in zabbix
    [criado] = [p for m, p in zabbix if m == "host.create"]
    assert criado["host"] == "cam-shopping-novo-ch01"
    assert criado["name"] == "Câmera Shopping novo - canal 1"
    assert criado["interfaces"][0]["ip"] == "10.41.100.10"
    tags = {t["tag"]: t["value"] for t in criado["tags"]}
    assert tags == {
        "category": "cftv",
        "subcategory": "camera",
        "dvr": "Shopping novo",
        "canal": "1",
        "andar": "Shopping novo",
        "origem": "dashboard",
        "vendor": "Intelbras",
        "model": "VIP-1230",
        "model_source": "gravador",
    }
    assert {r["situacao"] for r in sync.ultimas()} == {"desativado", "criado"}


def test_arrumar_hosts_nao_cria_para_ip_em_correcao_e_respeita_limite(
    banco, zabbix, monkeypatch: pytest.MonkeyPatch
) -> None:
    cards = [_card_grav("Shopping novo", "Shopping_Novo", "10.41.102.172")]
    acessos = _acesso(
        CameraGravador(canal=1, tipo="ip", ip="10.41.100.10"),
        CameraGravador(canal=2, tipo="ip", ip="10.41.100.11"),
        CameraGravador(canal=3, tipo="ip", ip="10.41.100.12"),
    )
    monkeypatch.setattr(sync, "MAX_CRIAR_POR_RODADA", 1)
    r = sync.arrumar_hosts(cards, [], acessos, _AGORA, ignorar={"10.41.100.10"})
    assert r == {sync.CRIADO: 1}
    [criado] = [p for m, p in zabbix if m == "host.create"]
    assert criado["interfaces"][0]["ip"] == "10.41.100.11"


def test_nome_tecnico_repetido_ganha_o_ip(banco, zabbix) -> None:
    card = _card_grav("Já existe", "X", "10.0.0.1")
    cam = CameraGravador(canal=1, tipo="ip", ip="10.41.9.9")
    hostid = sync._criar_host_camera(card, cam, {"cam-ja-existe-ch01"})
    assert hostid == "999"
    [criado] = [p for m, p in zabbix if m == "host.create"]
    assert criado["host"] == "cam-ja-existe-ch01-10-41-9-9"
