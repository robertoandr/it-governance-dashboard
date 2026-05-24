"""
🌐 Testes de INTEGRAÇÃO dos endpoints REST /api/governance/*

Cobre:
  • GET /api/governance/owners
  • GET /api/governance/organization
  • GET /api/governance/domain/<key>
"""


# ═══════════════════════════════════════════════════════════════════
# 👥 GRUPO 1 — /api/governance/owners
# ═══════════════════════════════════════════════════════════════════


class TestOwnersEndpoint:
    def test_owners_retorna_200(self, gov_client):
        r = gov_client.get("/api/governance/owners")
        assert r.status_code == 200

    def test_owners_estrutura_basica(self, gov_client):
        r = gov_client.get("/api/governance/owners")
        body = r.get_json()
        assert "organization" in body
        assert "domains" in body
        assert "count" in body

    def test_owners_count_correto(self, gov_client):
        r = gov_client.get("/api/governance/owners")
        body = r.get_json()
        # Fixture tem 4 domains: m365, cftv (active) + rede, backup (planned)
        assert body["count"]["total"] == 4
        assert body["count"]["active"] == 2
        assert body["count"]["planned"] == 2

    def test_owners_domains_ordenados(self, gov_client):
        r = gov_client.get("/api/governance/owners")
        body = r.get_json()
        keys = [d["key"] for d in body["domains"]]
        assert keys == ["m365", "cftv", "rede", "backup"]

    def test_owners_active_tem_sla_current(self, gov_client):
        r = gov_client.get("/api/governance/owners")
        body = r.get_json()
        cftv = next(d for d in body["domains"] if d["key"] == "cftv")
        # Fixture inject up_pct=97.3
        assert cftv["sla"]["current"] == 97.3
        assert cftv["sla"]["status"] == "ok"

    def test_owners_organization_presente(self, gov_client):
        r = gov_client.get("/api/governance/owners")
        body = r.get_json()
        assert body["organization"]["short_name"] == "TEST"


# ═══════════════════════════════════════════════════════════════════
# 🏢 GRUPO 2 — /api/governance/organization
# ═══════════════════════════════════════════════════════════════════


class TestOrganizationEndpoint:
    def test_organization_retorna_200(self, gov_client):
        r = gov_client.get("/api/governance/organization")
        assert r.status_code == 200

    def test_organization_estrutura(self, gov_client):
        r = gov_client.get("/api/governance/organization")
        body = r.get_json()
        assert body["name"] == "Test Org"
        assert body["short_name"] == "TEST"
        assert body["dashboard_title"] == "Dashboard de Teste"


# ═══════════════════════════════════════════════════════════════════
# 🔍 GRUPO 3 — /api/governance/domain/<key>
# ═══════════════════════════════════════════════════════════════════


class TestDomainDetailEndpoint:
    def test_domain_existente_retorna_200(self, gov_client):
        r = gov_client.get("/api/governance/domain/m365")
        assert r.status_code == 200

    def test_domain_existente_estrutura(self, gov_client):
        r = gov_client.get("/api/governance/domain/cftv")
        body = r.get_json()
        assert body["key"] == "cftv"
        assert body["name"] == "CFTV"
        assert body["owner"]["team"] == "TI Infra"

    def test_domain_active_tem_sla_current(self, gov_client):
        r = gov_client.get("/api/governance/domain/cftv")
        body = r.get_json()
        assert "current" in body["sla"]
        assert body["sla"]["current"] == 97.3

    def test_domain_planned_sem_sla_current(self, gov_client):
        r = gov_client.get("/api/governance/domain/rede")
        body = r.get_json()
        assert body["status"] == "planned"
        # 'sla' pode nem existir; se existir, sem 'current'
        assert "current" not in body.get("sla", {})

    def test_domain_inexistente_retorna_404(self, gov_client):
        r = gov_client.get("/api/governance/domain/nao_existe_xyz")
        assert r.status_code == 404
        body = r.get_json()
        assert body["error"] == "domain_not_found"
        assert body["key"] == "nao_existe_xyz"


# ═══════════════════════════════════════════════════════════════════
# 🛡️ GRUPO 4 — Contratos REST (compatibilidade frontend)
# ═══════════════════════════════════════════════════════════════════


class TestContratoREST:
    def test_owners_content_type_json(self, gov_client):
        r = gov_client.get("/api/governance/owners")
        assert "application/json" in r.content_type

    def test_owners_domains_is_array(self, gov_client):
        r = gov_client.get("/api/governance/owners")
        body = r.get_json()
        assert isinstance(body["domains"], list)

    def test_domain_404_tem_json_estruturado(self, gov_client):
        r = gov_client.get("/api/governance/domain/xyz")
        body = r.get_json()
        assert "error" in body
        assert "key" in body
