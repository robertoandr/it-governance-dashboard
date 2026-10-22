"""Inventário por IP (modelo, série, MAC), abas e busca da página Rede."""

from __future__ import annotations

from typing import Any

import pytest

from app.services.inventario_rede import (
    InfoEquipamento,
    casa_busca,
    grupo,
    inventario,
    rotulo_equipamento,
    tipo_gravador,
)
from itgov.services.intelbras_api import CameraGravador, LeituraGravador


def _host(ip: str, **extra: Any) -> dict[str, Any]:
    return {"ip": ip, "hostname": "", "zabbix_host": "", "ia_nome": "", "tipo_sugerido": "outro", **extra}


@pytest.mark.parametrize(
    ("modelo", "analogicas", "esperado"),
    [
        ("MHDX 1116", 0, "DVR"),
        ("HDCVI 3116 Geração 2", 0, "DVR"),
        ("NVD 1432", 0, "NVR"),
        ("iNVD 7032", 0, "NVR"),
        ("DS-7616NI-K2", 0, "NVR"),
        ("DS-7208HGHI-F1", 0, "DVR"),
        ("", 4, "DVR"),
        ("", 0, "Gravador"),
    ],
)
def test_tipo_gravador(modelo: str, analogicas: int, esperado: str) -> None:
    assert tipo_gravador(modelo, analogicas) == esperado


class TestInventario:
    """Gravador primeiro; descoberta e IA só completam o que faltou."""

    def _leituras(self) -> dict[str, dict[str, Any]]:
        leitura = LeituraGravador(
            modelo="iNVD 7032",
            serie="NVR-SERIE",
            cameras=[
                CameraGravador(
                    canal=3,
                    tipo="ip",
                    ip="10.41.103.45",
                    mac="80:85:44:00:00:45",
                    modelo="VIP-3230-B",
                    serie="CAM-SERIE",
                    fabricante="Intelbras",
                ),
                CameraGravador(canal=4, tipo="analogica"),
            ],
        )
        return {"10.41.100.11": {"leitura": leitura}, "10.41.100.12": {"leitura": None}}

    def test_gravador_e_camera_pelo_gravador(self) -> None:
        inv = inventario(self._leituras(), [])
        grav, cam = inv["10.41.100.11"], inv["10.41.103.45"]
        assert (grav.modelo, grav.serie, grav.equipamento, grav.fonte) == ("iNVD 7032", "NVR-SERIE", "NVR", "gravador")
        assert (cam.modelo, cam.serie, cam.mac, cam.fabricante) == (
            "VIP-3230-B",
            "CAM-SERIE",
            "80:85:44:00:00:45",
            "Intelbras",
        )
        assert (cam.gravador_ip, cam.canal, cam.equipamento) == ("10.41.100.11", 3, "")
        assert "10.41.100.12" not in inv

    def test_gravador_com_analogica_vira_dvr(self) -> None:
        leitura = LeituraGravador(modelo="", cameras=[CameraGravador(canal=1, tipo="analogica")])
        assert inventario({"10.0.0.1": {"leitura": leitura}}, [])["10.0.0.1"].equipamento == "DVR"

    def test_descoberta_completa_mac_e_fabricante(self) -> None:
        hosts = [
            _host("10.41.103.45", mac="AA:AA:AA:AA:AA:AA", vendor="Outro"),
            _host("10.41.1.9", mac="BC:24:11:00:00:09", vendor="Ricoh"),
        ]
        inv = inventario(self._leituras(), hosts)
        # o gravador vale mais que a descoberta
        assert (inv["10.41.103.45"].mac, inv["10.41.103.45"].fabricante) == ("80:85:44:00:00:45", "Intelbras")
        assert (inv["10.41.1.9"].mac, inv["10.41.1.9"].fabricante, inv["10.41.1.9"].fonte) == (
            "bc:24:11:00:00:09",
            "Ricoh",
            "rede",
        )

    def test_modelo_da_ia_so_quando_falta(self) -> None:
        hosts = [_host("10.41.1.9", ia_modelo="Ricoh IM C2000"), _host("10.41.103.45", ia_modelo="Chute da IA")]
        inv = inventario(self._leituras(), hosts)
        assert (inv["10.41.1.9"].modelo, inv["10.41.1.9"].fonte) == ("Ricoh IM C2000", "ia")
        assert inv["10.41.103.45"].modelo == "VIP-3230-B"

    def test_host_sem_nada_fica_fora(self) -> None:
        assert inventario({}, [_host("10.41.1.10")]) == {}


class TestGrupo:
    """Aba de cada host: gravadores, câmeras ou outros."""

    def test_pela_conferencia_e_pelo_gravador(self) -> None:
        assert grupo(_host("10.0.0.1"), None, {"10.0.0.1"}, set()) == "gravadores"
        assert grupo(_host("10.0.0.2"), None, set(), {"10.0.0.2"}) == "cameras"
        assert grupo(_host("10.0.0.3"), InfoEquipamento(equipamento="NVR"), set(), set()) == "gravadores"

    def test_camera_dvr_da_descoberta_separa_pelo_nome(self) -> None:
        assert grupo(_host("10.0.0.4", tipo_sugerido="camera", zabbix_host="NVR Shopping"), None, set(), set()) == (
            "gravadores"
        )
        assert grupo(_host("10.0.0.5", tipo_sugerido="camera"), InfoEquipamento(modelo="MHDX 1116"), set(), set()) == (
            "gravadores"
        )
        assert (
            grupo(_host("10.0.0.6", tipo_sugerido="camera", hostname="cam-portaria"), None, set(), set()) == "cameras"
        )

    def test_resto_e_outros(self) -> None:
        assert grupo(_host("10.0.0.7", tipo_sugerido="impressora"), None, set(), set()) == "outros"

    def test_rotulo_equipamento(self) -> None:
        assert rotulo_equipamento(_host("10.0.0.1"), InfoEquipamento(equipamento="DVR")) == "DVR"
        assert rotulo_equipamento(_host("10.0.0.2", zabbix_host="NVR Loja"), None) == "NVR"
        assert rotulo_equipamento(_host("10.0.0.3"), None) == "Gravador"


class TestBusca:
    """Pesquisa por IP ou tipo (e também nome, modelo, série e MAC)."""

    def test_por_ip_e_por_tipo(self) -> None:
        h = _host("10.41.103.45", tipo_sugerido="camera")
        assert casa_busca(h, None, "10.41.103", "Câmera / DVR")
        assert casa_busca(h, None, "CÂMERA", "Câmera / DVR")
        assert not casa_busca(h, None, "impressora", "Câmera / DVR")

    def test_por_equipamento_modelo_serie_e_mac(self) -> None:
        info = InfoEquipamento(modelo="iNVD 7032", serie="ABC123", mac="80:85:44:00:00:45", equipamento="NVR")
        h = _host("10.41.100.11")
        for termo in ("nvr", "invd", "abc123", "80:85:44"):
            assert casa_busca(h, info, termo, "Outro"), termo

    def test_vazio_casa_tudo(self) -> None:
        assert casa_busca(_host("10.0.0.1"), None, "  ", "Outro")
