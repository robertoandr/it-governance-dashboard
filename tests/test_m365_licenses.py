"""Testes de itgov/api/v1/m365_licenses.get_licenses_summary — LIC-01 (c) e (e).

Mocka _query_influxdb (seam já existente no módulo) para não depender de InfluxDB real.
"""

from __future__ import annotations

import json

import pytest

import itgov.api.v1.m365_licenses as m365_licenses


@pytest.fixture(autouse=True)
def _reset_cache():
    """Garante que cada teste começa sem cache do módulo (TTL 300s poluiria os testes)."""
    m365_licenses._invalidar_cache()
    yield
    m365_licenses._invalidar_cache()


@pytest.fixture
def isolated_costs_file(tmp_path, monkeypatch):
    """Redireciona _COSTS_FILE para um arquivo temporário isolado."""
    costs_file = tmp_path / "license_costs.json"
    costs_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(m365_licenses, "_COSTS_FILE", costs_file)
    return costs_file


def _write_costs(path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def _mock_rows(monkeypatch, rows: list[dict]) -> None:
    monkeypatch.setattr(m365_licenses, "_query_influxdb", lambda: rows)


class TestOverProvisionedWasteExclusion:
    """(c) — SKU acima do limite (consumed > total) não pode gerar 'desperdício'."""

    def test_desperdicio_zero_quando_acima_do_limite(self, monkeypatch, isolated_costs_file):
        _write_costs(
            isolated_costs_file,
            {"EXCHANGEENTERPRISE": {"friendly_name": "Exchange Online Plan 2", "cost_per_unit_brl": 45.0}},
        )
        _mock_rows(
            monkeypatch,
            [
                {
                    "sku_id": "id1",
                    "sku_name": "EXCHANGEENTERPRISE",
                    "consumed": 16,
                    "total": 10,
                    "available": 0,
                    "_time": "2026-07-03T00:00:00Z",
                }
            ],
        )
        result = m365_licenses.get_licenses_summary()
        lic = result["licenses"][0]
        assert lic["over_provisioned"] is True
        assert lic["desperdicio_brl"] == 0.0
        assert result["summary"]["desperdicio_total_brl"] == 0.0

    def test_desperdicio_zero_mesmo_se_available_positivo_incorretamente(self, monkeypatch, isolated_costs_file):
        """Regra deve ser explícita: mesmo que o coletor um dia pare de fazer o clamp
        (available > 0 por engano numa SKU estourada), o desperdício ainda deve sair 0."""
        _write_costs(
            isolated_costs_file,
            {"EXCHANGEENTERPRISE": {"friendly_name": "Exchange Online Plan 2", "cost_per_unit_brl": 45.0}},
        )
        _mock_rows(
            monkeypatch,
            [
                {
                    "sku_id": "id1",
                    "sku_name": "EXCHANGEENTERPRISE",
                    "consumed": 16,
                    "total": 10,
                    "available": 5,
                    "_time": "2026-07-03T00:00:00Z",
                }
            ],
        )
        result = m365_licenses.get_licenses_summary()
        lic = result["licenses"][0]
        assert lic["over_provisioned"] is True
        assert lic["desperdicio_brl"] == 0.0

    def test_desperdicio_normal_quando_dentro_do_limite(self, monkeypatch, isolated_costs_file):
        _write_costs(
            isolated_costs_file,
            {"O365_BUSINESS_ESSENTIALS": {"friendly_name": "Microsoft 365 Business Basic", "cost_per_unit_brl": 33.4}},
        )
        _mock_rows(
            monkeypatch,
            [
                {
                    "sku_id": "id2",
                    "sku_name": "O365_BUSINESS_ESSENTIALS",
                    "consumed": 12,
                    "total": 20,
                    "available": 8,
                    "_time": "2026-07-03T00:00:00Z",
                }
            ],
        )
        result = m365_licenses.get_licenses_summary()
        lic = result["licenses"][0]
        assert lic["over_provisioned"] is False
        assert lic["desperdicio_brl"] == pytest.approx(33.4 * 8, abs=0.01)


class TestFriendlyNameFlag:
    """(e) — sinaliza quando o SKU não tem nome amigável mapeado."""

    def test_has_friendly_name_true_quando_mapeado(self, monkeypatch, isolated_costs_file):
        _write_costs(
            isolated_costs_file,
            {"EXCHANGEENTERPRISE": {"friendly_name": "Exchange Online Plan 2", "cost_per_unit_brl": 45.0}},
        )
        _mock_rows(
            monkeypatch,
            [
                {
                    "sku_id": "id1",
                    "sku_name": "EXCHANGEENTERPRISE",
                    "consumed": 5,
                    "total": 10,
                    "available": 5,
                    "_time": "2026-07-03T00:00:00Z",
                }
            ],
        )
        lic = m365_licenses.get_licenses_summary()["licenses"][0]
        assert lic["has_friendly_name"] is True
        assert lic["friendly_name"] == "Exchange Online Plan 2"

    def test_has_friendly_name_false_quando_sem_mapeamento(self, monkeypatch, isolated_costs_file):
        _mock_rows(
            monkeypatch,
            [
                {
                    "sku_id": "id9",
                    "sku_name": "UNMAPPED_SKU_XYZ",
                    "consumed": 5,
                    "total": 10,
                    "available": 5,
                    "_time": "2026-07-03T00:00:00Z",
                }
            ],
        )
        lic = m365_licenses.get_licenses_summary()["licenses"][0]
        assert lic["has_friendly_name"] is False
        # fallback continua sendo o próprio sku_name (usado pelo template p/ exibir em itálico)
        assert lic["friendly_name"] == "UNMAPPED_SKU_XYZ"


class TestLicenseCostsCoverage:
    """(f) — SKUs referenciadas em _FREE_SKUS devem ter entrada em app/data/license_costs.json.

    Usa o seed versionado (_SEED_FILE) diretamente, não _COSTS_FILE — o volume persistente
    (default /app/data/license_costs.json) só existe dentro do container.
    """

    @pytest.fixture(autouse=True)
    def _use_seed_file(self, monkeypatch):
        monkeypatch.setattr(m365_licenses, "_COSTS_FILE", m365_licenses._SEED_FILE)

    def test_todas_as_free_skus_tem_entrada_mapeada(self):
        costs = m365_licenses._load_costs()
        faltando = sorted(sku for sku in m365_licenses._FREE_SKUS if sku not in costs)
        assert faltando == [], f"SKUs em _FREE_SKUS sem entrada em license_costs.json: {faltando}"

    def test_novas_skus_tem_category_free_e_custo_zero(self):
        costs = m365_licenses._load_costs()
        novas = [
            "WINDOWS_STORE",
            "Dynamics_365_Customer_Service_Enterprise_viral_trial",
            "Microsoft_Teams_Exploratory_Dept",
            "Power_Pages_vTrial_for_Makers",
        ]
        for sku in novas:
            assert sku in costs, f"{sku} não encontrado em license_costs.json"
            assert costs[sku]["category"] == "free"
            assert costs[sku]["cost_per_unit_brl"] == 0
            assert costs[sku]["friendly_name"]
