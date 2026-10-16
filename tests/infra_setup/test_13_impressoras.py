"""Tests for infra-setup/13_impressoras.py (impressoras com o template do modelo)."""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

_MODULE_PATH = Path(__file__).resolve().parent.parent.parent / "infra-setup" / "13_impressoras.py"
_spec = importlib.util.spec_from_file_location("impressoras_script", _MODULE_PATH)
imp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(imp)  # type: ignore[union-attr]


def _linha(marca: str, modelo: str) -> object:
    return imp.Impressora("Shopping", "Cobrança", marca, modelo, "10.41.103.201", "ZER4")


@pytest.mark.parametrize(
    ("marca", "modelo", "template"),
    [
        ("Lexmark", "CX725", imp.TPL_LEXMARK_COR),
        ("Lexmark", "XM1246", imp.TEMPLATE),  # mono: índices do template colorido não servem
        ("RICOH", "IM C2500", imp.TPL_RICOH),
        ("Samsung", "SL-M4070FR", imp.TEMPLATE),
        ("Xerox", "VersaLink C7130", imp.TEMPLATE),
        ("HP", "Color LaserJet Pro MFP 4303", imp.TEMPLATE),
    ],
)
def test_template_do_modelo(marca: str, modelo: str, template: str) -> None:
    assert imp.template_do_modelo(_linha(marca, modelo)) == template


def test_nome_e_tags() -> None:
    linha = _linha("Samsung", "SL-M4070FR")
    assert imp.nome_visivel(linha) == "Impressora Shopping - Cobrança (Samsung SL-M4070FR)"
    assert imp.nome_tecnico(linha) == "impressora_10.41.103.201"
    tags = imp.tags(linha, [{"tag": "origem", "value": "dashboard-rede"}, {"tag": "loja", "value": "velha"}])
    assert {"tag": "origem", "value": "dashboard-rede"} in tags
    assert {"tag": "loja", "value": "Shopping"} in tags
    assert [t["tag"] for t in tags].count("loja") == 1


def test_lista_sem_ip_repetido() -> None:
    ips = [i.ip for i in imp.IMPRESSORAS]
    assert len(ips) == len(set(ips)) == 17


def test_trigger_ignora_nivel_desconhecido() -> None:
    assert imp.expr_baixo() == (
        "last(/snmp-impressora-printer-mib/impressora.suprimento.pct[{#SNMPINDEX}])>=0"
        " and last(/snmp-impressora-printer-mib/impressora.suprimento.pct[{#SNMPINDEX}])<10"
    )


def test_js_tira_serie_e_peca_da_descricao() -> None:
    node = subprocess.run(["which", "node"], capture_output=True, text=True).stdout.strip()
    if not node:
        pytest.skip("node não instalado")
    entrada = json.dumps(
        [
            {"{#SNMPINDEX}": "1", "{#DESC}": "Black Toner Cartridge S/N:CRUM-18081712345"},
            {"{#SNMPINDEX}": "2", "{#DESC}": "Fuser, PN 115R00114"},
            {"{#SNMPINDEX}": "3", "{#DESC}": "Preto Cartridge  W2150XC"},
        ]
    )
    codigo = f"var value = {json.dumps(entrada)};\nconsole.log((function() {{{imp.JS_LIMPA_DESC}}})());"
    saida = json.loads(subprocess.run([node, "-e", codigo], capture_output=True, text=True, check=True).stdout)
    assert [s["{#DESC}"] for s in saida] == ["Black Toner Cartridge", "Fuser", "Preto Cartridge W2150XC"]
