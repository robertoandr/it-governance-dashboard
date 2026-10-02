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


@pytest.fixture(autouse=True)
def _sem_graph(monkeypatch):
    """Assinaturas (teste/renovação) vêm do Graph — nos testes, nenhuma por padrão."""
    monkeypatch.setattr(m365_licenses, "_assinaturas", lambda: {})


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


class TestCategoriasEPrecos:
    """Totais só com licenças pagas; preço de lista como estimativa; renovação da Microsoft."""

    def _rows(self) -> list[dict]:
        return [
            {"sku_id": "a", "sku_name": "O365_BUSINESS_ESSENTIALS", "consumed": 268, "total": 269, "available": 1},
            {"sku_id": "b", "sku_name": "SHAREPOINTSTORAGE", "consumed": 0, "total": 2000, "available": 2000},
            {"sku_id": "c", "sku_name": "THREAT_INTELLIGENCE", "consumed": 1, "total": 300, "available": 299},
            {"sku_id": "d", "sku_name": "FLOW_FREE", "consumed": 114, "total": 10000, "available": 9886},
            {"sku_id": "e", "sku_name": "PROJECT_P1", "consumed": 4, "total": 5, "available": 1},
        ]

    def _assinaturas(self) -> dict:
        from datetime import date

        from itgov.services.m365_assinaturas import AssinaturaSku

        return {
            "THREAT_INTELLIGENCE": AssinaturaSku(
                sku_name="THREAT_INTELLIGENCE", teste=True, renovacao=date(2026, 10, 4), ativas=1, suspensas=0
            ),
            "O365_BUSINESS_ESSENTIALS": AssinaturaSku(
                sku_name="O365_BUSINESS_ESSENTIALS", teste=False, renovacao=date(2027, 2, 3), ativas=3, suspensas=0
            ),
        }

    def _resumo(self, monkeypatch, isolated_costs_file, costs: dict | None = None) -> dict:
        _write_costs(isolated_costs_file, costs or {})
        _mock_rows(monkeypatch, self._rows())
        monkeypatch.setattr(m365_licenses, "_assinaturas", self._assinaturas)
        m365_licenses._invalidar_cache()
        return m365_licenses.get_licenses_summary()

    def _sku(self, resumo: dict, nome: str) -> dict:
        return next(x for x in resumo["licenses"] if x["sku_name"] == nome)

    def test_teste_capacidade_e_gratuita_ficam_fora_dos_totais(self, monkeypatch, isolated_costs_file):
        resumo = self._resumo(monkeypatch, isolated_costs_file)
        assert self._sku(resumo, "THREAT_INTELLIGENCE")["categoria"] == "teste"
        assert self._sku(resumo, "SHAREPOINTSTORAGE")["categoria"] == "capacidade"
        assert self._sku(resumo, "FLOW_FREE")["categoria"] == "gratuito"
        s = resumo["summary"]
        assert s["total_seats"] == 269 + 5
        assert s["total_unassigned"] == 1 + 1  # antes somava 2000 GB de SharePoint + 299 do teste
        assert s["total_skus_teste"] == 1

    def test_preco_de_lista_vira_estimativa(self, monkeypatch, isolated_costs_file):
        resumo = self._resumo(monkeypatch, isolated_costs_file)
        basic = self._sku(resumo, "O365_BUSINESS_ESSENTIALS")
        assert basic["preco_origem"] == "estimado"
        assert basic["cost_per_unit_brl"] == m365_licenses.PRECOS_LISTA_BRL["O365_BUSINESS_ESSENTIALS"]
        assert basic["custo_mensal_brl"] == round(268 * basic["cost_per_unit_brl"], 2)
        assert resumo["summary"]["custo_estimado_brl"] == basic["custo_mensal_brl"]

    def test_sku_paga_sem_preco_de_lista_pede_valor(self, monkeypatch, isolated_costs_file):
        resumo = self._resumo(monkeypatch, isolated_costs_file)
        assert self._sku(resumo, "PROJECT_P1")["preco_origem"] == "sem_preco"
        assert resumo["summary"]["skus_sem_preco"] == 1

    def test_custo_informado_vale_mais_que_o_estimado(self, monkeypatch, isolated_costs_file):
        resumo = self._resumo(
            monkeypatch, isolated_costs_file, {"O365_BUSINESS_ESSENTIALS": {"cost_per_unit_brl": 30.0}}
        )
        basic = self._sku(resumo, "O365_BUSINESS_ESSENTIALS")
        assert basic["preco_origem"] == "informado"
        assert basic["cost_per_unit_brl"] == 30.0
        assert resumo["summary"]["custo_estimado_brl"] == 0.0

    def test_teste_nao_tem_custo(self, monkeypatch, isolated_costs_file):
        teste = self._sku(self._resumo(monkeypatch, isolated_costs_file), "THREAT_INTELLIGENCE")
        assert teste["preco_origem"] == "nao_se_aplica" and teste["custo_mensal_brl"] == 0
        assert teste["teste_vence"] == "2026-10-04"
        assert teste["friendly_name"] == "Defender for Office 365 Plano 2"

    def test_renovacao_vem_da_microsoft_se_nao_informada(self, monkeypatch, isolated_costs_file):
        basic = self._sku(self._resumo(monkeypatch, isolated_costs_file), "O365_BUSINESS_ESSENTIALS")
        assert basic["renewal_date"] == "2027-02-03" and basic["renewal_origem"] == "microsoft"
        resumo = self._resumo(
            monkeypatch, isolated_costs_file, {"O365_BUSINESS_ESSENTIALS": {"renewal_date": "2027-01-15"}}
        )
        basic = self._sku(resumo, "O365_BUSINESS_ESSENTIALS")
        assert basic["renewal_date"] == "2027-01-15" and basic["renewal_origem"] == "informado"


def test_pagina_licencas_marca_estimado_e_teste(authed_client, monkeypatch, isolated_costs_file):
    _mock_rows(monkeypatch, TestCategoriasEPrecos()._rows())
    monkeypatch.setattr(m365_licenses, "_assinaturas", TestCategoriasEPrecos()._assinaturas)
    html = authed_client.get("/gov/licenses").get_data(as_text=True)
    assert "estimado" in html
    assert "Assinaturas de teste" in html and "Defender for Office 365 Plano 2" in html
    assert "sem preço — editar" in html
    assert "openEdit($event" not in html  # Alpine CSP


def test_salvar_custo_vazio_volta_para_estimado(authed_client, isolated_costs_file):
    resp = authed_client.post(
        "/gov/licenses/update", json={"sku_name": "O365_BUSINESS_ESSENTIALS", "cost_per_unit_brl": ""}
    )
    assert resp.get_json() == {"ok": True}
    assert json.loads(isolated_costs_file.read_text())["O365_BUSINESS_ESSENTIALS"]["cost_per_unit_brl"] == 0.0
