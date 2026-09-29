"""Tests for infra-setup/07_create_cftv_hosts.py (merge de vínculos em hosts existentes)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_MODULE_PATH = Path(__file__).resolve().parent.parent.parent / "infra-setup" / "07_create_cftv_hosts.py"
_spec = importlib.util.spec_from_file_location("cftv_hosts_script", _MODULE_PATH)
cftv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cftv)  # type: ignore[union-attr]


def _novo() -> dict:
    return {
        "name": "cam-loja-d01",
        "groups": [{"groupid": "10"}],
        "templates": [{"templateid": "100"}],
        "tags": [{"tag": "category", "value": "cftv"}, {"tag": "andar", "value": "T"}],
    }


def test_preserva_grupos_templates_e_tags_existentes() -> None:
    atual = {
        "hostid": "1",
        "hostgroups": [{"groupid": "5"}],
        "parentTemplates": [{"templateid": "200"}],
        "tags": [{"tag": "loja", "value": "Shopping"}, {"tag": "andar", "value": "velho"}],
    }

    r = cftv._somar_vinculos(atual, _novo())

    assert {g["groupid"] for g in r["groups"]} == {"5", "10"}
    assert {t["templateid"] for t in r["templates"]} == {"100", "200"}
    assert {"tag": "loja", "value": "Shopping"} in r["tags"]
    # tag presente no inventário vence o valor antigo, sem duplicar
    assert [t for t in r["tags"] if t["tag"] == "andar"] == [{"tag": "andar", "value": "T"}]
    assert r["name"] == "cam-loja-d01"


def test_host_sem_vinculos_recebe_so_os_do_inventario() -> None:
    r = cftv._somar_vinculos({"hostid": "1"}, _novo())

    assert r["groups"] == [{"groupid": "10"}]
    assert r["templates"] == [{"templateid": "100"}]
    assert r["tags"] == _novo()["tags"]
