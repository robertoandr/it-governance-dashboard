"""Dispositivos de rede da unidade para o botão "Dispositivos da rede" do Mapa de Câmeras."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any
from unittest.mock import patch

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app.services.mapa_dispositivos import (
    CanalAnalogico,
    CanalConferido,
    analogicas_dos_gravadores,
    dispositivos_da_unidade,
)
from itgov.services.intelbras_api import CameraGravador, LeituraGravador

FAIXAS = [(1, "10.41.0.0/16"), (2, "172.17.0.0/16")]


def _host(ip: str, **extra: Any) -> dict[str, Any]:
    return {"ip": ip, "hostname": "", "zabbix_host": "", "ia_nome": "", "vci": "", "tipo_sugerido": "outro", **extra}


class TestDispositivosDaUnidade:
    """Recorte por faixa, celulares fora, câmera com gravador e canal."""

    def test_so_a_unidade_e_sem_celulares(self) -> None:
        hosts = [
            _host("10.41.100.90", zabbix_host="Switch HPE", tipo_sugerido="switch"),
            _host("10.41.1.48", hostname="iPhone", tipo_sugerido="movel"),
            _host("10.41.1.49", vci="android-dhcp-14", tipo_sugerido="endpoint"),
            _host("172.17.1.10", tipo_sugerido="switch"),
        ]
        dados = dispositivos_da_unidade(hosts, FAIXAS, {1}, [])
        assert [d["ip"] for d in dados["dispositivos"]] == ["10.41.100.90"]
        assert dados["dispositivos"][0]["nome"] == "Switch HPE"
        assert dados["celulares"] == 2

    def test_camera_leva_gravador_e_canal(self) -> None:
        hosts = [_host("10.41.100.11", zabbix_host="Shopping_Ext_1"), _host("10.41.0.49", tipo_sugerido="camera")]
        canais = [CanalConferido("10.41.100.11", 17, "10.41.0.49"), CanalConferido("10.41.100.11", 18, "")]
        por_ip = {d["ip"]: d for d in dispositivos_da_unidade(hosts, FAIXAS, {1}, canais)["dispositivos"]}
        assert por_ip["10.41.100.11"]["tipo"] == "gravador"
        cam = por_ip["10.41.0.49"]
        assert (cam["tipo"], cam["gravador_ip"], cam["gravador_nome"], cam["canal"]) == (
            "camera",
            "10.41.100.11",
            "Shopping_Ext_1",
            17,
        )
        assert cam["nome"] == "Shopping_Ext_1 - canal 17"

    def test_camera_da_conferencia_entra_mesmo_sem_descoberta(self) -> None:
        canais = [CanalConferido("10.41.100.11", 3, "10.41.0.77")]
        dados = dispositivos_da_unidade([], FAIXAS, {1}, canais)["dispositivos"]
        assert [(d["tipo"], d["ip"]) for d in dados] == [("camera", "10.41.0.77"), ("gravador", "10.41.100.11")]

    def test_celular_que_e_camera_na_conferencia_nao_sai(self) -> None:
        # o nome parece de celular, mas a conferência diz que é câmera
        hosts = [_host("10.41.0.50", hostname="Galaxy-A15", tipo_sugerido="movel")]
        canais = [CanalConferido("10.41.100.11", 1, "10.41.0.50")]
        dados = dispositivos_da_unidade(hosts, FAIXAS, {1}, canais)
        assert "10.41.0.50" in {d["ip"] for d in dados["dispositivos"]}
        assert dados["celulares"] == 0

    def test_gravador_com_ip_de_camera_nao_vira_camera(self) -> None:
        canais = [CanalConferido("10.41.103.51", 1, ""), CanalConferido("10.41.100.174", 27, "10.41.103.51")]
        (unico,) = [
            d for d in dispositivos_da_unidade([], FAIXAS, {1}, canais)["dispositivos"] if d["ip"] == "10.41.103.51"
        ]
        assert (unico["tipo"], unico["gravador_ip"], unico["canal"]) == ("gravador", "", None)

    def test_inventario_informado_pelo_gravador(self) -> None:
        from app.services.inventario_rede import InfoEquipamento

        canais = [CanalConferido("10.41.100.11", 3, "10.41.0.77")]
        inv = {
            "10.41.0.77": InfoEquipamento(
                modelo="VIPC-1230-B-G2", serie="SN77", mac="aa:bb:cc:00:00:77", fabricante="Intelbras"
            ),
            "10.41.100.11": InfoEquipamento(modelo="NVD 3332", serie="NVR11", equipamento="NVR"),
        }
        por_ip = {d["ip"]: d for d in dispositivos_da_unidade([], FAIXAS, {1}, canais, inv)["dispositivos"]}
        cam, grav = por_ip["10.41.0.77"], por_ip["10.41.100.11"]
        assert (cam["modelo"], cam["serie"], cam["mac"], cam["marca"]) == (
            "VIPC-1230-B-G2",
            "SN77",
            "aa:bb:cc:00:00:77",
            "Intelbras",
        )
        assert (grav["modelo"], grav["serie"], grav["equipamento"]) == ("NVD 3332", "NVR11", "NVR")

    def test_sem_modelo_fica_vazio(self) -> None:
        (item,) = dispositivos_da_unidade([_host("10.41.0.9")], FAIXAS, {1}, [])["dispositivos"]
        assert (item["modelo"], item["serie"], item["equipamento"]) == ("", "", "")

    def test_ordena_ip_pelo_numero(self) -> None:
        hosts = [_host("10.41.0.10"), _host("10.41.0.9")]
        assert [d["ip"] for d in dispositivos_da_unidade(hosts, FAIXAS, {1}, [])["dispositivos"]] == [
            "10.41.0.9",
            "10.41.0.10",
        ]


class TestCamerasAnalogicas:
    """Câmera analógica entra pelo gravador + canal, sem IP."""

    def test_entra_com_chave_gravador_e_canal(self) -> None:
        hosts = [_host("10.41.100.11", zabbix_host="Shopping_Ext_1", online=True)]
        analogicas = [CanalAnalogico("10.41.100.11", 2, "PORTARIA 1"), CanalAnalogico("10.41.100.11", 1)]
        dados = dispositivos_da_unidade(hosts, FAIXAS, {1}, [], analogicas=analogicas)
        cams = [d for d in dados["dispositivos"] if d["analogica"]]
        assert [(d["canal"], d["nome"], d["chave"], d["ip"], d["tipo"]) for d in cams] == [
            (1, "Shopping_Ext_1 - canal 1 (analógica)", "10.41.100.11#1", "", "camera"),
            (2, "PORTARIA 1", "10.41.100.11#2", "", "camera"),
        ]
        assert cams[0]["gravador_nome"] == "Shopping_Ext_1" and cams[0]["online"]
        assert dados["analogicas"] == 2

    def test_so_da_unidade_e_sem_repetir(self) -> None:
        analogicas = [
            CanalAnalogico("10.41.100.11", 1),
            CanalAnalogico("10.41.100.11", 1, "repetida"),
            CanalAnalogico("172.17.1.151", 1),
        ]
        dados = dispositivos_da_unidade([], FAIXAS, {1}, [], analogicas=analogicas)
        assert [d["chave"] for d in dados["dispositivos"]] == ["10.41.100.11#1"]

    def test_camera_ip_da_conferencia_tambem_ganha_chave(self) -> None:
        canais = [CanalConferido("10.41.100.11", 17, "10.41.0.49")]
        (cam,) = [
            d for d in dispositivos_da_unidade([], FAIXAS, {1}, canais)["dispositivos"] if d["ip"] == "10.41.0.49"
        ]
        assert (cam["chave"], cam["analogica"]) == ("10.41.100.11#17", False)


class TestAnalogicasDosGravadores:
    """Critério de canal analógico em uso (o DVR lista todo canal como Local)."""

    def _leituras(self) -> dict[str, Any]:
        leitura = LeituraGravador(
            modelo="MHDX 1116",
            canais=6,
            sem_video=[3, 4, 5, 6],
            titulos={1: "CAM 1", 2: "PORTARIA", 3: "DOCA", 4: "CAM 4", 5: "S/ CÂMERA", 6: "CAM 6"},
            cameras=[CameraGravador(canal=c, tipo="analogica") for c in range(1, 7)]
            + [CameraGravador(canal=7, tipo="ip", ip="10.41.0.7")],
        )
        return {"10.41.100.11": {"leitura": leitura}, "10.41.100.12": {"leitura": None}}

    def test_criterio_do_cftv(self) -> None:
        cadastro = {"10.41.100.11": {6: {"situacao": "ativo", "analogica": True, "equipamento": "Fundos"}}}
        res = analogicas_dos_gravadores(self._leituras(), cadastro)
        # 1: com vídeo; 2: com vídeo e nome; 3: sem vídeo mas com nome; 4: sem vídeo e nome de fábrica (fora);
        # 5: "S/ CÂMERA" (fora); 6: conferida no local como câmera (entra, sem o nome de fábrica)
        assert [(a.canal, a.nome) for a in res] == [(1, ""), (2, "PORTARIA"), (3, "DOCA"), (6, "")]

    def test_conferencia_vazio_tira_e_ativa_sem_leitura_entra(self) -> None:
        cadastro = {
            "10.41.100.11": {1: {"situacao": "vazio", "analogica": False}},
            "10.41.100.12": {9: {"situacao": "ativo", "analogica": True, "equipamento": "Estoque"}},
        }
        res = analogicas_dos_gravadores(self._leituras(), cadastro)
        assert (CanalAnalogico("10.41.100.11", 1, "")) not in res
        assert CanalAnalogico("10.41.100.12", 9, "Estoque") in res


@pytest.fixture
def client_as(factory_app: Flask) -> Iterator[Callable[[str], FlaskClient]]:
    """Cliente logado com o perfil pedido."""
    from app.extensions import db
    from app.models.user import User

    def _make(role: str) -> FlaskClient:
        email = f"pytest-mapadisp-{role}@test.local"
        with factory_app.app_context():
            user = User.query.filter_by(email=email).first()
            if user is None:
                user = User(name=f"Pytest {role}", email=email, role=role)
                user.set_password("pytest-only-not-real")
                db.session.add(user)
                db.session.commit()
            user_id = user.id
        client = factory_app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user_id)
            sess["_fresh"] = True
        return client

    yield _make


@pytest.fixture
def unidade_mapa(factory_app: Flask) -> Iterator[str]:
    """Unidade com faixa própria e nome no MapaCameras."""
    from app.extensions import db
    from app.models.unidade import Unidade

    with factory_app.app_context():
        u = Unidade(nome="Unidade Mapa Teste", nome_mapa="MapaTeste", faixas_ip="10.99.0.0/16")
        db.session.add(u)
        db.session.commit()
        uid = u.id
    yield "MapaTeste"
    with factory_app.app_context():
        db.session.delete(db.session.get(Unidade, uid))
        db.session.commit()


class TestRota:
    """GET /gov/cameras/dispositivos-rede."""

    def test_lista_da_unidade_pelo_nome_no_mapa(
        self, client_as: Callable[[str], FlaskClient], unidade_mapa: str
    ) -> None:
        hosts = [_host("10.99.0.5", zabbix_host="AP Loja", tipo_sugerido="ap"), _host("10.99.0.6", hostname="iPhone")]
        with patch("itgov.api.v1.rede_monitoring.get_cached_rede_summary", return_value={"hosts": hosts}):
            resp = client_as("gestor").get(f"/gov/cameras/dispositivos-rede?unidade={unidade_mapa}")
        assert resp.status_code == 200
        corpo = resp.get_json()
        assert corpo["unidade"] == "MapaTeste"
        assert [(d["ip"], d["tipo"]) for d in corpo["dispositivos"]] == [("10.99.0.5", "ap")]
        assert corpo["celulares"] == 1

    def test_leva_analogicas_e_inventario_do_gravador(
        self, client_as: Callable[[str], FlaskClient], unidade_mapa: str
    ) -> None:
        leitura = LeituraGravador(
            modelo="MHDX 1116",
            serie="DVR-SN",
            canais=2,
            titulos={1: "PORTARIA"},
            cameras=[CameraGravador(canal=1, tipo="analogica"), CameraGravador(canal=2, tipo="analogica")],
            sem_video=[2],
        )
        with (
            patch("itgov.api.v1.rede_monitoring.get_cached_rede_summary", return_value={"hosts": []}),
            patch("app.services.cftv_gravadores.leituras", return_value={"10.99.0.9": {"leitura": leitura}}),
            patch("app.services.cftv_gravadores.cadastro_canais", return_value={}),
        ):
            corpo = client_as("gestor").get(f"/gov/cameras/dispositivos-rede?unidade={unidade_mapa}").get_json()
        assert corpo["analogicas"] == 1
        (cam,) = [d for d in corpo["dispositivos"] if d["analogica"]]
        assert (cam["chave"], cam["nome"], cam["ip"]) == ("10.99.0.9#1", "PORTARIA", "")

    def test_unidade_desconhecida_404(self, client_as: Callable[[str], FlaskClient]) -> None:
        with patch("itgov.api.v1.rede_monitoring.get_cached_rede_summary", return_value={"hosts": []}):
            assert client_as("admin").get("/gov/cameras/dispositivos-rede?unidade=Nenhuma").status_code == 404

    def test_operador_nao_pode(self, client_as: Callable[[str], FlaskClient]) -> None:
        assert client_as("operador").get("/gov/cameras/dispositivos-rede?unidade=X").status_code == 403
