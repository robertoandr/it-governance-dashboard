"""Licenças do Acronis (uso × contratado) e o bloco na página de licenças."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from itgov.services import acronis_licencas as al
from tests.test_m365_licenses import isolated_costs_file  # noqa: F401 -- fixture usada por sem_m365

_AGORA = datetime(2026, 11, 5, 15, 0, tzinfo=UTC)

# Formato real (05/11): offering_items com quota.value e status; usages com name/value
_OFERTAS = [
    {"name": "local_storage", "status": 1, "quota": {"value": None}},
    {"name": "pw_base_workstations", "status": 1, "quota": {"value": 300}},
    {"name": "pw_pack_adv_management", "status": 1, "quota": {"value": 300}},
    {"name": "pw_pack_adv_security", "status": 1, "quota": {"value": 300}},
    {"name": "pw_pack_adv_dlp", "status": 1, "quota": {"value": 30}},
    {"name": "pw_pack_adv_security_xdr", "status": 1, "quota": {"value": None}},
    {"name": "pw_base_servers", "status": 0, "quota": {"value": 10}},
    {"name": "pw_base_vms", "status": 1, "quota": None},
]
_USOS = [
    {"name": "pw_base_workstations", "value": 279},
    {"name": "pw_pack_adv_management", "value": 286},
    {"name": "pw_pack_adv_security", "value": 1},
    {"name": "pw_pack_adv_security_xdr", "value": 278},
    {"name": "total_protected_workloads", "value": 282},
    {"name": "immutable_storage", "value": 161165623296},
    {"name": "pw_pack_adv_dlp", "value": "x"},
]


def test_montar_junta_cota_e_uso() -> None:
    lic = al.montar(_OFERTAS, _USOS, _AGORA)
    assert [i.chave for i in lic.itens] == [
        "pw_base_workstations",
        "pw_pack_adv_management",
        "pw_pack_adv_security",
        "pw_pack_adv_security_xdr",
        "pw_pack_adv_dlp",
    ]
    est = lic.itens[0]
    assert (est.nome, est.usado, est.contratado, est.uso_pct, est.livres, est.nivel) == (
        "Estações de trabalho (proteção)", 279, 300, 93.0, 21, "atencao",
    )  # fmt: skip
    mgmt = lic.itens[1]
    assert (mgmt.uso_pct, mgmt.nivel) == (95.3, "critico")
    xdr = lic.itens[3]
    assert (xdr.contratado, xdr.uso_pct, xdr.livres, xdr.nivel) == (None, None, None, "ok")
    dlp = lic.itens[4]
    assert (dlp.usado, dlp.contratado, dlp.nivel) == (0, 30, "ok")
    assert [i.chave for i in lic.em_alerta] == ["pw_pack_adv_management", "pw_base_workstations"]


def test_acima_da_cota_e_critico() -> None:
    lic = al.montar(
        [{"name": "pw_base_workstations", "status": 1, "quota": 10}],
        [{"name": "pw_base_workstations", "value": 12}],
        _AGORA,
    )
    item = lic.itens[0]
    assert (item.livres, item.nivel) == (-2, "critico")


def test_item_desconhecido_usa_nome_da_api() -> None:
    lic = al.montar([{"name": "pw_novo", "status": 1, "quota": {"value": 5}}], [], _AGORA)
    assert lic.itens[0].nome == "pw_novo"


def test_obter_sem_acronis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ACRONIS_BASE_URL", raising=False)
    assert al.obter() is None and al.carregando() is False


def test_carregar_falha_vira_none(monkeypatch: pytest.MonkeyPatch) -> None:
    async def falha() -> al.LicencasAcronis:
        raise httpx.ConnectError("fora")

    monkeypatch.setattr(al, "_buscar", falha)
    assert al._carregar() is None


# ── Página ──────────────────────────────────────────────────────────────────


@pytest.fixture
def sem_m365(monkeypatch: pytest.MonkeyPatch, isolated_costs_file: Path) -> None:  # noqa: F811 -- fixture importada
    """A página também lê o M365: dados falsos, como nos testes de licenças."""
    from itgov.api.v1 import m365_licenses
    from tests.test_m365_licenses import TestCategoriasEPrecos, _mock_rows

    _mock_rows(monkeypatch, TestCategoriasEPrecos()._rows())
    monkeypatch.setattr(m365_licenses, "_assinaturas", TestCategoriasEPrecos()._assinaturas)


def test_pagina_licencas_mostra_acronis(
    authed_client, sem_m365: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app.services import acronis_triggers

    arquivo = tmp_path / "t.json"
    arquivo.write_text(
        json.dumps({"categorias": [{"chave": "cota", "maquinas": ["SRV-HV1", "app"]}]}), encoding="utf-8"
    )
    monkeypatch.setattr(acronis_triggers, "_ARQUIVO", str(arquivo))
    monkeypatch.setattr(al, "obter", lambda: al.montar(_OFERTAS, _USOS, _AGORA))
    monkeypatch.setattr(al, "carregando", lambda: False)
    html = authed_client.get("/gov/licenses").get_data(as_text=True)
    assert "Acronis Cyber Protect" in html
    assert "Advanced Management (patch e vulnerabilidade)</strong>: 286 de 300 (95.3%)" in html
    assert "2 máquinas sem licença suficiente: SRV-HV1, app" in html
    assert "lido às 12:00" in html  # Brasília
    assert "sem cota" in html


def test_pagina_licencas_sem_acronis_nao_mostra_bloco(
    authed_client, sem_m365: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(al, "obter", lambda: None)
    monkeypatch.setattr(al, "carregando", lambda: False)
    assert "Acronis Cyber Protect" not in authed_client.get("/gov/licenses").get_data(as_text=True)
