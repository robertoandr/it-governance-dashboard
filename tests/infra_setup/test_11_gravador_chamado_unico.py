"""Tests for infra-setup/11_gravador_chamado_unico.py (gravador caiu/reiniciou: um chamado só)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

_MODULE_PATH = Path(__file__).resolve().parent.parent.parent / "infra-setup" / "11_gravador_chamado_unico.py"
_spec = importlib.util.spec_from_file_location("gravador_chamado_unico", _MODULE_PATH)
gc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gc)  # type: ignore[union-attr]


def _tags(**kw: str) -> list[dict[str, str]]:
    return [{"tag": k, "value": v} for k, v in kw.items()]


def test_chave_gravador_prefere_tag_dvr() -> None:
    assert gc.chave_gravador({"host": "dvr-shopping_1", "tags": _tags(dvr="Shopping 1")}) == "Shopping 1"
    assert gc.chave_gravador({"host": "DVR-1", "tags": _tags(subcategory="dvr")}) == "DVR-1"


def test_gravador_da_camera_por_dvr_ou_parent_nvr() -> None:
    assert gc.gravador_da_camera({"tags": _tags(dvr="Shopping 1")}) == "Shopping 1"
    assert gc.gravador_da_camera({"tags": _tags(parent_nvr="nvr-centro-01")}) == "nvr-centro-01"
    assert gc.gravador_da_camera({"tags": _tags(subcategory="facial")}) == ""


def test_deps_faltando_nao_repete_nem_duplica() -> None:
    assert gc.deps_faltando([{"triggerid": "1"}], ["1", "2", "2", "3"]) == ["2", "3"]
    assert gc.deps_faltando([{"triggerid": "1"}, {"triggerid": "2"}], ["2", "1"]) == []


def test_expressoes_preservam_macro_e_esperam_o_gravador() -> None:
    absent = gc.CANAIS["Camera {#SNMPINDEX} absent"]
    assert '="Empty"' in absent and "{$CAM.STATUS.CONFIAVEL}=1" in absent
    assert "enterprises.deviceUpTime)>=15m" in absent
    assert '="Unconnect"' in gc.CANAIS["Camera {#SNMPINDEX} disconnected"]
    assert "time()<015000 or time()>023000" in gc.EXPR_REINICIOU


def test_canal_sem_camera_cadastrada_nao_alerta() -> None:
    for descricao in ("Camera {#SNMPINDEX} absent", "Camera {#SNMPINDEX} disconnected"):
        assert '/Intelbras NVR SNMP/remoteCam[IpAddr{#SNMPINDEX}])<>"192.168.0.0"' in gc.CANAIS[descricao]


class _Api:
    """Zabbix falso: registra os updates de dependências."""

    def __init__(self) -> None:
        self.updates: list[dict[str, Any]] = []
        self.trigger = self
        self.triggerprototype = self

    def update(self, **kw: Any) -> None:
        self.updates.append(kw)


def test_somar_deps_mantem_as_existentes() -> None:
    api = _Api()
    mudou = gc._somar_deps(api, {"triggerid": "9", "dependencies": [{"triggerid": "1"}]}, ["1", "2"], False, False)
    assert mudou
    assert api.updates == [{"triggerid": "9", "dependencies": [{"triggerid": "1"}, {"triggerid": "2"}]}]


def test_somar_deps_dry_run_e_sem_mudanca_nao_gravam() -> None:
    api = _Api()
    assert gc._somar_deps(api, {"triggerid": "9", "dependencies": []}, ["1"], False, True)
    assert not gc._somar_deps(api, {"triggerid": "9", "dependencies": [{"triggerid": "1"}]}, ["1"], False, False)
    assert api.updates == []
