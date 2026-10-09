"""zb2/zb3: equipamentos por tipo e hardware dos computadores (Acronis) na página Zabbix."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from itgov.api.v1 import zabbix_monitoring as zm
from itgov.services import acronis_inventario as inv


def _attrs(
    placa: dict,
    cpus: list[str],
    *,
    so: str = "Microsoft Windows 11 Pro",
    produto: str = "1",
    wifi: bool = False,
    discos: list[dict] | None = None,
) -> list[dict]:
    rede = [{"type": "Native 802.11", "name": "Wi-Fi"}] if wifi else [{"type": "802.3", "name": "Ethernet"}]
    return [
        {"name": "atp", "kvs": [{"key": "last_logged_in_user", "value": "ana (GRUPOGADENS)"}]},
        {"name": "agent", "kvs": [{"key": "os_name", "value": so}, {"key": "os_product_type", "value": produto},
                                  {"key": "memory_size", "value": "17179869184"}, {"key": "online", "value": "true"}]},
        {"name": "default", "kvs": [{"key": "ip", "value": "172.29.1.10"}]},
        {"name": "hwi", "kvs": [{"key": "cores", "value": "4"}, {"key": "serial_number", "value": "PE07"},
                                {"key": "scan_time", "value": "1791542082974163700"}],
         "details": {"motherBoards": [placa], "cpus": [{"name": c} for c in cpus], "networkAdapters": rede,
                     "disks": discos if discos is not None else [{"size": 256 * 1024**3, "availableSpace": 64 * 1024**3,
                                                                  "mediaType": "SSD"}]}},
    ]  # fmt: skip


@pytest.mark.parametrize(
    ("modelo", "fabricante", "so", "produto", "cpu", "wifi", "tipo"),
    [
        ("Virtual Machine", "Microsoft Corporation", "Windows Server 2012 R2", "3", "Intel Xeon E5-2430", False, "vm"),
        ("440BX Desktop Reference Platform", "Intel Corporation", "Windows Server", "3", "Xeon", False, "vm"),
        ("", "", "Microsoft Windows Server 2016 Datacenter", "3", "Intel Xeon E5-2430 v2", False, "servidor"),
        ("NP730QFG", "SAMSUNG", "Windows 11", "1", "13th Gen Intel(R) Core(TM) i5-1335U", False, "notebook"),
        ("E1504GAB", "ASUS", "Windows 11", "1", "Intel(R) Core(TM) i3-N305", True, "notebook"),
        ("Wish_KLS", "ACER", "Windows 11", "1", "Intel(R) Core(TM) i7-7700HQ CPU @ 2.80GHz", True, "notebook"),
        ("NP940XGK", "SAMSUNG", "Windows 11", "1", "Intel(R) Core(TM) Ultra 5 125H", True, "notebook"),
        ("E1504FA", "ASUS", "Windows 11", "1", "", True, "notebook"),
        ("H510M H", "ASRock", "Windows 10", "1", "Intel(R) Core(TM) i7-10700KF CPU @ 3.80GHz", False, "desktop"),
        ("32CA", "HP", "Windows 11", "1", "12th Gen Intel(R) Core(TM) i5-12500", True, "desktop"),
        ("H61M", "Gigabyte", "Windows 10", "1", "", False, "desktop"),
    ],
)
def test_tipo_computador(modelo: str, fabricante: str, so: str, produto: str, cpu: str, wifi: bool, tipo: str) -> None:
    assert inv.tipo_computador(modelo, fabricante, so, produto, cpu, wifi) == tipo


def test_montar_computador_junta_namespaces() -> None:
    rec = {"id": "1", "name": "DESKTOP-ABC.grupogadens.com.br"}
    c = inv.montar_computador(rec, _attrs({"manufacturer": "LENOVO", "model": "LNVNB161216"},
                                          ["Intel(R) Core(TM) i5-10210U CPU @ 1.60GHz"]))  # fmt: skip
    assert c is not None
    assert (c.nome, c.tipo, c.fabricante, c.modelo) == ("DESKTOP-ABC", "notebook", "LENOVO", "LNVNB161216")
    assert c.cpu == "Intel Core i5-10210U @ 1.60GHz" and c.nucleos == 4
    assert (c.ram_gb, c.disco_gb, c.disco_livre_gb, c.disco_tipo) == (16.0, 256.0, 64.0, "SSD")
    assert c.disco_uso_pct == 75.0
    assert c.usuario == "ana (GRUPOGADENS)" and c.online and c.ip == "172.29.1.10" and c.serie == "PE07"
    assert c.inventario_em is not None and c.inventario_em.year == 2026


def test_montar_computador_sem_hwi_e_cpu_em_lista() -> None:
    assert inv.montar_computador({"id": "1", "name": "x"}, [{"name": "agent", "kvs": []}]) is None
    attrs = _attrs({"manufacturer": "Microsoft Corporation", "model": "Virtual Machine"}, [], discos=[])
    attrs[3]["kvs"].append({"key": "cpu", "value": "['Intel(R) Xeon(R) E5-2680 v4', 'Intel(R) Xeon(R) E5-2680 v4']"})
    c = inv.montar_computador({"id": "1", "name": "SRV-FILE"}, attrs)
    assert c is not None and c.tipo == "vm" and c.cpu == "Intel Xeon E5-2680 v4"
    assert c.disco_gb == 0 and c.disco_uso_pct == 0.0


def test_por_tipo_na_ordem() -> None:
    def _c(nome: str, tipo: str) -> inv.Computador:
        return inv.Computador(nome=nome, tipo=tipo)

    dados = inv.InventarioComputadores(
        atualizado_em=datetime.now(UTC), computadores=[_c("a", "desktop"), _c("b", "notebook"), _c("c", "notebook")]
    )
    assert dados.por_tipo == {"notebook": 2, "desktop": 1}


def test_obter_inventario_nao_bloqueia(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("ACRONIS_BASE_URL", "ACRONIS_CLIENT_ID", "ACRONIS_CLIENT_SECRET"):
        monkeypatch.delenv(var, raising=False)
    assert inv.obter_inventario() is None and not inv.carregando()

    for var in ("ACRONIS_BASE_URL", "ACRONIS_CLIENT_ID", "ACRONIS_CLIENT_SECRET"):
        monkeypatch.setenv(var, "x")
    inv._cache.limpar()
    with patch.object(inv, "aquecer") as aquecer:
        assert inv.obter_inventario() is None
        assert inv.carregando()
        aquecer.assert_called_once()
    valor = inv.InventarioComputadores(atualizado_em=datetime.now(UTC), computadores=[])
    with patch.object(inv, "_carregar", return_value=valor):
        inv._cache.get(inv._carregar)
        assert inv.obter_inventario() is valor and not inv.carregando()
    inv._cache.limpar()


def test_hosts_por_tipo_pelos_grupos() -> None:
    def _h(*grupos: str) -> dict:
        return {"hostgroups": [{"name": g} for g in grupos]}

    hosts = [_h("CFTV/Cameras"), _h("CFTV/Cameras"), _h("CFTV/NVRs"), _h("CFTV/DVRs"), _h("Links WAN"),
             _h("Zabbix servers"), _h("Backup"), _h("Qualquer")]  # fmt: skip
    assert zm.hosts_por_tipo(hosts) == {
        "Câmeras": 2, "Gravadores (DVR/NVR)": 2, "Links WAN": 1, "Servidores": 1, "Checagens de serviço": 1, "Outros": 1,
    }  # fmt: skip


def test_buscar_hosts_por_tipo_tolera_falha() -> None:
    with patch.object(zm, "_zbx", side_effect=RuntimeError("fora")):
        assert zm._buscar_hosts_por_tipo() == {}


_RESUMO = {"uptime_pct": 99.0, "hosts_up": 280, "hosts_down": 0, "total_monitorado": 284, "top_host_down": "—",
           "score_risco": 90.0, "criticos": 0, "altos": 0, "medios": 0, "avisos": 0, "total_problemas": 0,
           "top_incidente": "—"}  # fmt: skip


@pytest.fixture
def pagina_zabbix(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ZABBIX_URL", "https://zbx.local")
    computadores = [
        inv.montar_computador({"id": "1", "name": "NOTE-ANA"}, _attrs({"manufacturer": "LENOVO", "model": "X1"},
                                                                      ["Intel Core i5-1335U"])),
        inv.montar_computador({"id": "2", "name": "PC-BETO"}, _attrs({"manufacturer": "ASRock", "model": "H510M"},
                                                                     ["Intel Core i5-10400"])),
    ]  # fmt: skip
    dados = inv.InventarioComputadores(atualizado_em=datetime.now(UTC), computadores=computadores, sem_inventario=2)
    with (
        patch.object(zm, "get_cached_zabbix_summary", return_value=_RESUMO),
        patch.object(zm, "get_cached_problems", return_value=[]),
        patch.object(zm, "get_cached_hosts_por_tipo", return_value={"Câmeras": 249, "Links WAN": 2}),
        patch.object(inv, "obter_inventario", return_value=dados),
        patch.object(inv, "carregando", return_value=False),
        patch.object(inv, "configurado", return_value=True),
    ):
        yield


def test_pagina_zabbix_mostra_tipos_e_hardware(authed_client, pagina_zabbix) -> None:
    html = authed_client.get("/gov/zabbix").get_data(as_text=True)
    for trecho in ("Equipamentos por tipo", "Câmeras", "249", "Notebook", "Desktop", "NOTE-ANA", "PC-BETO",
                   "Intel Core i5-1335U", "16 GB", "256 GB SSD", "75.0% usado", "2 máquinas sem inventário"):  # fmt: skip
        assert trecho in html, trecho


def test_pagina_zabbix_filtra_computadores(authed_client, pagina_zabbix) -> None:
    html = authed_client.get("/gov/zabbix?tipo=desktop").get_data(as_text=True)
    assert "PC-BETO" in html and "NOTE-ANA" not in html
    html = authed_client.get("/gov/zabbix?q=lenovo").get_data(as_text=True)
    assert "NOTE-ANA" in html and "PC-BETO" not in html
