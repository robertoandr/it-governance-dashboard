"""Classificação de ativos e faixas de varredura em collectors/network_discovery.py."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

import collectors.network_discovery as nd


def _host(tcp: set[int] | None = None, udp: set[int] | None = None, **kw: str) -> nd.DiscoveredHost:
    h = nd.DiscoveredHost(ip="10.0.0.10", open_tcp=tcp or set(), open_udp=udp or set(), **kw)
    h.classify()
    return h


@pytest.mark.parametrize(
    ("host", "tipo"),
    [
        (_host({22, 10050}, mac="00:50:56:AA:BB:CC"), "vm"),
        (_host({3389}, mac="00:15:5d:01:02:03"), "vm"),
        (_host({22}, vendor="Proxmox Server Solutions GmbH"), "vm"),
        (_host({80, 9100}), "impressora"),
        (_host({631}, vendor="Brother Industries"), "impressora"),
        (_host(set(), vendor="Hewlett Packard"), "impressora"),
        (_host({80, 554}), "camera"),
        (_host({37777}), "camera"),
        (_host({443, 541}), "firewall"),
        (_host({22}, vendor="Ubiquiti Networks"), "ap"),
        (_host({22}, {161}, vendor="Cisco Systems"), "switch"),
        (_host({135, 445}, os_guess="Microsoft Windows 11"), "endpoint"),
        (_host({3389, 445}, os_guess="Microsoft Windows Server 2019"), "servidor"),
        (_host({22}), "servidor"),
        (_host({5432}), "servidor"),
        (_host({445}), "endpoint"),
        (_host(set(), {161}), "switch"),
        (_host({80}), "outro"),
    ],
)
def test_sugerir_tipo(host: nd.DiscoveredHost, tipo: str) -> None:
    assert host.tipo_sugerido == tipo, host.motivo
    assert host.motivo


def test_vm_tem_prioridade_sobre_portas_de_servidor() -> None:
    h = _host({22, 3389, 10050}, mac="00:0c:29:11:22:33")
    assert (h.tipo_sugerido, h.motivo) == ("vm", "MAC VMware")


def test_impressora_hp_com_ssh_nao_vira_impressora_so_pelo_fabricante() -> None:
    # Servidor HP (ProLiant) com SSH não é impressora
    assert _host({22}, vendor="Hewlett Packard Enterprise").tipo_sugerido == "servidor"


def test_scan_ports_inclui_assinaturas() -> None:
    for porta in (9100, 515, 631, 554, 37777, 445, 541):
        assert porta in nd.SCAN_PORTS


# ── Faixas ────────────────────────────────────────────────────────────────────


def _app_db(path: Path, linhas: list[tuple[str, int]]) -> str:
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE unidades (id INTEGER PRIMARY KEY, faixas_ip TEXT, ativo BOOLEAN)")
        conn.executemany("INSERT INTO unidades (faixas_ip, ativo) VALUES (?, ?)", linhas)
    return str(path)


def test_faixas_das_unidades_le_so_ativas(tmp_path: Path) -> None:
    db = _app_db(tmp_path / "app.db", [("10.41.0.0/16\n10.42.1.0/24", 1), ("10.99.0.0/24", 0), ("", 1)])
    assert nd.faixas_das_unidades(db) == ["10.41.0.0/16", "10.42.1.0/24"]


def test_faixas_das_unidades_sem_arquivo_ou_tabela(tmp_path: Path) -> None:
    assert nd.faixas_das_unidades(str(tmp_path / "nao-existe.db")) == []
    vazio = tmp_path / "vazio.db"
    sqlite3.connect(vazio).close()
    assert nd.faixas_das_unidades(str(vazio)) == []


def test_faixas_para_varrer_une_normaliza_e_filtra() -> None:
    with patch.object(nd, "SCAN_RANGES", ["172.29.0.0/22"]):
        faixas = nd.faixas_para_varrer(["172.29.0.5/22", "10.41.0.0/16", "10.0.0.0/8", "lixo", "10.50.1.7"])
    # duplicada normalizada some, /8 é grande demais, "lixo" é inválida, IP solto vira /32
    assert faixas == ["172.29.0.0/22", "10.41.0.0/16", "10.50.1.7/32"]


# ── run_scan ──────────────────────────────────────────────────────────────────


def test_run_scan_nao_registra_no_zabbix_por_padrao() -> None:
    hosts = [_host({9100})]
    with (
        patch.object(nd, "faixas_das_unidades", return_value=[]),
        patch.object(nd, "_scan_range", return_value=hosts),
        patch.object(nd, "_write_influx") as influx,
        patch.object(nd, "_sync_zabbix") as sync,
        patch.object(nd, "ZABBIX_AUTOREGISTER", False),
        patch.object(nd, "ZABBIX_TOKEN", "tok"),
    ):
        nd.run_scan()
    influx.assert_called_once()
    sync.assert_not_called()


def test_run_scan_registra_quando_habilitado_e_varre_faixas_das_unidades() -> None:
    with (
        patch.object(nd, "SCAN_RANGES", ["172.29.0.0/22"]),
        patch.object(nd, "faixas_das_unidades", return_value=["10.41.0.0/16"]),
        patch.object(nd, "_scan_range", return_value=[_host({22})]) as scan,
        patch.object(nd, "_write_influx"),
        patch.object(nd, "_sync_zabbix") as sync,
        patch.object(nd, "ZABBIX_AUTOREGISTER", True),
        patch.object(nd, "ZABBIX_TOKEN", "tok"),
    ):
        nd.run_scan()
    assert [c.args[0] for c in scan.call_args_list] == ["172.29.0.0/22", "10.41.0.0/16"]
    sync.assert_called_once()


def test_run_scan_influx_sem_token_zabbix() -> None:
    with (
        patch.object(nd, "faixas_das_unidades", return_value=[]),
        patch.object(nd, "_scan_range", return_value=[_host({80})]),
        patch.object(nd, "_write_influx") as influx,
        patch.object(nd, "ZABBIX_TOKEN", ""),
    ):
        nd.run_scan()
    influx.assert_called_once()
