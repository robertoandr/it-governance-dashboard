"""
🏛️ Testes UNITÁRIOS do governance_service

Cobre:
  • _load_yaml (cache TTL, mtime, fallback)
  • get_organization / get_domains / get_domain
  • compute_sla_current (cftv, m365, None)
  • get_domains_with_runtime (enriquecimento)
"""
import time

# ═══════════════════════════════════════════════════════════════════
# 📦 GRUPO 1 — Loading & Cache
# ═══════════════════════════════════════════════════════════════════

class TestLoadYaml:
    """_load_yaml — cache TTL + invalidação por mtime."""

    def test_load_carrega_arquivo_existente(self, gov_service):
        data = gov_service._load_yaml()
        assert "domains" in data
        assert "organization" in data
        assert data["organization"]["short_name"] == "TEST"

    def test_load_usa_cache_em_segunda_chamada(self, gov_service):
        d1 = gov_service._load_yaml()
        d2 = gov_service._load_yaml()
        # Mesmo objeto = cache hit
        assert d1 is d2

    def test_force_ignora_cache(self, gov_service):
        gov_service._load_yaml()
        loaded_at_1 = gov_service._yaml_cache["loaded_at"]
        time.sleep(0.01)
        gov_service._load_yaml(force=True)
        loaded_at_2 = gov_service._yaml_cache["loaded_at"]
        assert loaded_at_2 > loaded_at_1

    def test_arquivo_nao_existe_retorna_estrutura_vazia(self, gov_service, monkeypatch):
        from pathlib import Path
        monkeypatch.setattr(gov_service, "_OWNERS_FILE", Path("/tmp/nao_existe_xyz.yaml"))
        gov_service._yaml_cache["data"] = None  # força reload
        data = gov_service._load_yaml()
        assert data == {"domains": {}, "organization": {}}

    def test_yaml_invalido_mantem_cache_ou_volta_estrutura_vazia(
        self, gov_service, fake_owners_yaml
    ):
        # Primeiro load OK
        gov_service._load_yaml()

        # Corrompe o YAML
        fake_owners_yaml.write_text("isso: nao: eh: yaml: valido: [", encoding="utf-8")
        # Força reload via force
        result = gov_service._load_yaml(force=True)
        # Volta pro cache antigo OU pra estrutura vazia (ambos aceitáveis)
        assert "domains" in result


# ═══════════════════════════════════════════════════════════════════
# 🏢 GRUPO 2 — Organization
# ═══════════════════════════════════════════════════════════════════

class TestGetOrganization:
    def test_retorna_dict_completo(self, gov_service):
        org = gov_service.get_organization()
        assert org["name"] == "Test Org"
        assert org["short_name"] == "TEST"
        assert org["dashboard_title"] == "Dashboard de Teste"

    def test_arquivo_vazio_retorna_dict_vazio(self, gov_service, fake_owners_yaml):
        fake_owners_yaml.write_text("", encoding="utf-8")
        gov_service._yaml_cache["data"] = None
        org = gov_service.get_organization()
        assert org == {}


# ═══════════════════════════════════════════════════════════════════
# 📋 GRUPO 3 — get_domains
# ═══════════════════════════════════════════════════════════════════

class TestGetDomains:
    def test_retorna_lista(self, gov_service):
        domains = gov_service.get_domains()
        assert isinstance(domains, list)
        assert len(domains) == 4  # m365, cftv, rede, backup

    def test_cada_domain_tem_key_injetada(self, gov_service):
        domains = gov_service.get_domains()
        keys = [d["key"] for d in domains]
        assert "m365" in keys
        assert "cftv" in keys
        assert "rede" in keys
        assert "backup" in keys

    def test_ordenado_por_order(self, gov_service):
        domains = gov_service.get_domains()
        orders = [d["order"] for d in domains]
        assert orders == sorted(orders)
        # Confirma: m365(10), cftv(20), rede(30), backup(50)
        assert domains[0]["key"] == "m365"
        assert domains[-1]["key"] == "backup"

    def test_exclui_planned_quando_solicitado(self, gov_service):
        domains = gov_service.get_domains(include_planned=False)
        assert len(domains) == 2  # só m365 e cftv (active)
        assert all(d["status"] == "active" for d in domains)

    def test_inclui_planned_por_default(self, gov_service):
        domains = gov_service.get_domains()
        statuses = {d["status"] for d in domains}
        assert "active" in statuses
        assert "planned" in statuses


# ═══════════════════════════════════════════════════════════════════
# 🔍 GRUPO 4 — get_domain (singular)
# ═══════════════════════════════════════════════════════════════════

class TestGetDomain:
    def test_retorna_domain_existente(self, gov_service):
        d = gov_service.get_domain("m365")
        assert d is not None
        assert d["name"] == "Microsoft 365"
        assert d["key"] == "m365"
        assert d["owner"]["team"] == "TI Cloud"

    def test_retorna_none_para_inexistente(self, gov_service):
        assert gov_service.get_domain("inexistente_xyz") is None

    def test_retorna_planned_normalmente(self, gov_service):
        d = gov_service.get_domain("rede")
        assert d is not None
        assert d["status"] == "planned"
        assert d["eta"] == "2026-Q3"

    def test_nao_muta_cache_interno(self, gov_service):
        """get_domain retorna cópia — mutar o resultado não afeta cache."""
        d = gov_service.get_domain("m365")
        d["name"] = "MUTADO"
        # Segunda chamada deve trazer original
        d2 = gov_service.get_domain("m365")
        assert d2["name"] == "Microsoft 365"


# ═══════════════════════════════════════════════════════════════════
# 📊 GRUPO 5 — compute_sla_current
# ═══════════════════════════════════════════════════════════════════

class TestComputeSlaCurrent:
    """Lógica de SLA dinâmico baseado no _cache global."""

    def test_cftv_retorna_up_pct(self, gov_service, fake_cache_runtime):
        sla = gov_service.compute_sla_current("cftv", fake_cache_runtime)
        assert sla == 97.3

    def test_cftv_sem_cache_retorna_none(self, gov_service):
        assert gov_service.compute_sla_current("cftv", {}) is None

    def test_m365_com_mfa_e_sh_ok(self, gov_service):
        cache = {
            "mfa": {"pct": 90.0},
            "service_health": [{"status": "serviceOperational"}],
        }
        # MFA*0.4 + 100*0.6 = 36 + 60 = 96.0
        assert gov_service.compute_sla_current("m365", cache) == 96.0

    def test_m365_com_issue_no_service_health(self, gov_service):
        cache = {
            "mfa": {"pct": 90.0},
            "service_health": [{"status": "serviceDegradation"}],
        }
        # MFA*0.4 + 95*0.6 = 36 + 57 = 93.0
        assert gov_service.compute_sla_current("m365", cache) == 93.0

    def test_m365_sem_mfa_usa_so_service_health(self, gov_service):
        cache = {"service_health": []}
        # Sem SH problems → 100%
        assert gov_service.compute_sla_current("m365", cache) == 100.0

    def test_m365_cache_totalmente_vazio(self, gov_service):
        # Sem MFA, sem SH → SH default 100%
        result = gov_service.compute_sla_current("m365", {})
        assert result == 100.0

    def test_domain_desconhecido_retorna_none(self, gov_service):
        assert gov_service.compute_sla_current("rede", {}) is None
        assert gov_service.compute_sla_current("xyz", {}) is None


# ═══════════════════════════════════════════════════════════════════
# 🚀 GRUPO 6 — get_domains_with_runtime
# ═══════════════════════════════════════════════════════════════════

class TestGetDomainsWithRuntime:
    def test_enriquece_active_com_sla_current(self, gov_service, fake_cache_runtime):
        domains = gov_service.get_domains_with_runtime(fake_cache_runtime)
        cftv = next(d for d in domains if d["key"] == "cftv")
        assert "current" in cftv["sla"]
        assert cftv["sla"]["current"] == 97.3

    def test_calcula_status_ok_quando_acima_do_target(self, gov_service, fake_cache_runtime):
        domains = gov_service.get_domains_with_runtime(fake_cache_runtime)
        cftv = next(d for d in domains if d["key"] == "cftv")
        # current=97.3 ≥ target=95.0 → ok
        assert cftv["sla"]["status"] == "ok"

    def test_status_warn_quando_abaixo_do_target(self, gov_service):
        bad_cache = {"cftv": {"up_pct": 80.0}}  # < 95
        domains = gov_service.get_domains_with_runtime(bad_cache)
        cftv = next(d for d in domains if d["key"] == "cftv")
        assert cftv["sla"]["current"] == 80.0
        assert cftv["sla"]["status"] == "warn"

    def test_planned_nao_recebe_sla_current(self, gov_service, fake_cache_runtime):
        domains = gov_service.get_domains_with_runtime(fake_cache_runtime)
        rede = next(d for d in domains if d["key"] == "rede")
        # Planned não tem 'sla' nem 'current'
        assert "current" not in rede.get("sla", {})

    def test_cache_vazio_active_fica_sem_current(self, gov_service):
        domains = gov_service.get_domains_with_runtime({})
        cftv = next(d for d in domains if d["key"] == "cftv")
        # Sem cache, compute retorna None → sem 'current'
        assert "current" not in cftv.get("sla", {})
