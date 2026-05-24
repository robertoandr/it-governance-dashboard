"""
tests/test_maintenance_service.py — Unit tests do serviço de manutenção

Cobertura:
- CRUD básico (mark, clear)
- Contratos de retorno (list vs dict)
- Idempotência
- Edge cases
- Audit log
"""

import pytest

# ═══════════════════════════════════════════════════════════════════
# 🟢 GRUPO 1 — Estado inicial
# ═══════════════════════════════════════════════════════════════════


class TestEstadoInicial:
    """Estado fresco, sem hosts."""

    def test_list_active_vazio_retorna_lista(self, isolated_state):
        svc = isolated_state["svc"]
        result = svc.list_active()
        assert result == []
        assert isinstance(result, list)

    def test_list_active_dict_vazio_retorna_dict(self, isolated_state):
        svc = isolated_state["svc"]
        result = svc.list_active_dict()
        assert result == {}
        assert isinstance(result, dict)

    def test_is_in_maintenance_host_inexistente_false(self, isolated_state):
        svc = isolated_state["svc"]
        assert svc.is_in_maintenance("HOST-FANTASMA") is False

    def test_get_info_host_inexistente_none(self, isolated_state):
        svc = isolated_state["svc"]
        assert svc.get_info("HOST-FANTASMA") is None


# ═══════════════════════════════════════════════════════════════════
# 🟢 GRUPO 2 — mark() — marcar hosts
# ═══════════════════════════════════════════════════════════════════


class TestMark:
    """Testes da função mark()."""

    def test_mark_unico_host(self, isolated_state):
        svc = isolated_state["svc"]
        result = svc.mark(
            hosts=["CAM-8A-35"],
            operator="roberto",
            reason="Teste",
        )
        assert result["count"] == 1
        assert "CAM-8A-35" in result["marked"]
        assert result["already_in_maint"] == []

    def test_mark_multiplos_hosts(self, isolated_state):
        svc = isolated_state["svc"]
        result = svc.mark(
            hosts=["CAM-8A-35", "CAM-8A-36", "CAM-8A-37"],
            operator="roberto",
            reason="Switch caiu",
        )
        assert result["count"] == 3
        assert len(result["marked"]) == 3

    def test_mark_persiste_metadados(self, isolated_state):
        svc = isolated_state["svc"]
        svc.mark(
            hosts=["SRV-01"],
            operator="alice",
            reason="Patch SO",
            domain="m365",
        )
        info = svc.get_info("SRV-01")
        assert info is not None
        assert info["marked_by"] == "alice"
        assert info["reason"] == "Patch SO"
        assert info["domain"] == "m365"
        assert "marked_at" in info  # timestamp gerado

    def test_mark_idempotente_nao_duplica(self, isolated_state):
        """🛡️ Marcar 2x o mesmo host: 2ª vai pra 'already_in_maint'."""
        svc = isolated_state["svc"]
        svc.mark(hosts=["CAM-X"], operator="op", reason="r1")
        result2 = svc.mark(hosts=["CAM-X"], operator="op", reason="r2")

        assert result2["count"] == 0
        assert "CAM-X" in result2["already_in_maint"]
        # Razão original PRESERVADA (não sobrescreve)
        assert svc.get_info("CAM-X")["reason"] == "r1"

    def test_mark_lista_vazia_retorna_zero(self, isolated_state):
        svc = isolated_state["svc"]
        result = svc.mark(hosts=[], operator="op", reason="r")
        assert result["count"] == 0
        assert result["marked"] == []

    def test_mark_operator_vazio_vira_anonymous(self, isolated_state):
        svc = isolated_state["svc"]
        svc.mark(hosts=["H1"], operator="", reason="r")
        assert svc.get_info("H1")["marked_by"] == "anonymous"

    def test_mark_strip_whitespace(self, isolated_state):
        """Hosts e operator passam por strip()."""
        svc = isolated_state["svc"]
        svc.mark(hosts=["  HOST-X  "], operator="  bob  ", reason="r")
        assert svc.is_in_maintenance("HOST-X")
        assert svc.get_info("HOST-X")["marked_by"] == "bob"

    def test_mark_default_domain_cftv(self, isolated_state):
        svc = isolated_state["svc"]
        svc.mark(hosts=["H"], operator="op", reason="r")
        assert svc.get_info("H")["domain"] == "cftv"


# ═══════════════════════════════════════════════════════════════════
# 🔴 GRUPO 3 — clear() — liberar hosts
# ═══════════════════════════════════════════════════════════════════


class TestClear:
    """Testes da função clear()."""

    def test_clear_host_existente(self, populated_state):
        svc = populated_state["svc"]
        result = svc.clear(hosts=["CAM-8A-35"], operator="op")
        assert result["count"] == 1
        assert "CAM-8A-35" in result["cleared"]
        assert svc.is_in_maintenance("CAM-8A-35") is False

    def test_clear_host_inexistente_vai_pra_not_found(self, isolated_state):
        svc = isolated_state["svc"]
        result = svc.clear(hosts=["FANTASMA"], operator="op")
        assert result["count"] == 0
        assert "FANTASMA" in result["not_found"]

    def test_clear_misto_existente_e_inexistente(self, populated_state):
        svc = populated_state["svc"]
        result = svc.clear(
            hosts=["CAM-8A-35", "FANTASMA"],
            operator="op",
        )
        assert result["count"] == 1
        assert "CAM-8A-35" in result["cleared"]
        assert "FANTASMA" in result["not_found"]

    def test_clear_todos_zera_state(self, populated_state):
        svc = populated_state["svc"]
        all_hosts = list(svc.list_active_dict().keys())
        svc.clear(hosts=all_hosts, operator="op")
        assert svc.list_active() == []


# ═══════════════════════════════════════════════════════════════════
# 🟣 GRUPO 4 — Contratos da API (Sprint 6f)
# ═══════════════════════════════════════════════════════════════════


class TestContratos:
    """🛡️ Sprint 6f: list_active() retorna list, list_active_dict() retorna dict."""

    def test_list_active_retorna_list_de_dicts(self, populated_state):
        svc = populated_state["svc"]
        result = svc.list_active()
        assert isinstance(result, list)
        assert len(result) == 3
        for item in result:
            assert isinstance(item, dict)
            assert "host" in item  # ← Sprint 6f: campo "host" injetado
            assert "marked_by" in item
            assert "reason" in item
            assert "domain" in item

    def test_list_active_dict_retorna_dict_keyed_by_host(self, populated_state):
        svc = populated_state["svc"]
        result = svc.list_active_dict()
        assert isinstance(result, dict)
        assert "CAM-8A-35" in result
        # No dict, o host_name é a CHAVE (não está dentro do value)
        assert "host" not in result["CAM-8A-35"]

    def test_dois_contratos_mesmo_conteudo(self, populated_state):
        """list e dict devem refletir o MESMO estado."""
        svc = populated_state["svc"]
        as_list = svc.list_active()
        as_dict = svc.list_active_dict()
        assert len(as_list) == len(as_dict)
        hosts_from_list = {item["host"] for item in as_list}
        assert hosts_from_list == set(as_dict.keys())


# ═══════════════════════════════════════════════════════════════════
# 📊 GRUPO 5 — stats() e history()
# ═══════════════════════════════════════════════════════════════════


class TestStatsHistory:
    """Estatísticas e audit log."""

    def test_stats_estado_vazio(self, isolated_state):
        svc = isolated_state["svc"]
        s = svc.stats()
        assert s["total_in_maintenance"] == 0
        assert s["by_domain"] == {}
        assert s["by_operator"] == {}

    def test_stats_agrupado_por_domain(self, isolated_state):
        svc = isolated_state["svc"]
        svc.mark(hosts=["A"], operator="x", reason="r", domain="cftv")
        svc.mark(hosts=["B"], operator="x", reason="r", domain="cftv")
        svc.mark(hosts=["C"], operator="x", reason="r", domain="m365")
        s = svc.stats()
        assert s["total_in_maintenance"] == 3
        assert s["by_domain"] == {"cftv": 2, "m365": 1}

    def test_history_registra_mark(self, isolated_state):
        svc = isolated_state["svc"]
        svc.mark(hosts=["H1"], operator="alice", reason="razao_x")
        events = svc.history(limit=10)
        assert len(events) >= 1
        assert events[0]["action"] == "mark"
        assert events[0]["host"] == "H1"
        assert events[0]["by"] == "alice"
        assert events[0]["reason"] == "razao_x"

    def test_history_registra_clear(self, populated_state):
        svc = populated_state["svc"]
        svc.clear(hosts=["CAM-8A-35"], operator="bob", note="ok")
        events = svc.history(limit=10)
        # Primeiro evento = clear (mais recente)
        assert events[0]["action"] == "clear"
        assert events[0]["host"] == "CAM-8A-35"
        assert events[0]["by"] == "bob"

    def test_history_ordem_decrescente(self, isolated_state):
        """history() retorna mais recente PRIMEIRO."""
        svc = isolated_state["svc"]
        svc.mark(hosts=["A"], operator="op", reason="r")
        svc.mark(hosts=["B"], operator="op", reason="r")
        events = svc.history(limit=10)
        assert events[0]["host"] == "B"  # mais recente
        assert events[1]["host"] == "A"  # mais antigo


# ═══════════════════════════════════════════════════════════════════
# 🛡️ GRUPO 6 — Robustez (validações Sprint 6e)
# ═══════════════════════════════════════════════════════════════════


class TestRobustez:
    """JSON corrompido, valores inválidos, etc."""

    def test_state_corrompido_volta_vazio(self, isolated_state):
        """JSON inválido no arquivo: deve retornar estado vazio (não crash)."""
        svc = isolated_state["svc"]
        isolated_state["state_file"].write_text("{ corrompido !!!", encoding="utf-8")
        assert svc.list_active() == []

    def test_state_lista_em_vez_de_dict_volta_vazio(self, isolated_state):
        """_validate_state: lista no topo deve ser rejeitada."""
        svc = isolated_state["svc"]
        isolated_state["state_file"].write_text("[]", encoding="utf-8")
        assert svc.list_active() == []

    def test_apply_filter_sem_hosts_passthrough(self, isolated_state):
        """Sem hosts em manutenção, apply_filter retorna data intacta."""
        svc = isolated_state["svc"]
        data = {"items": [{"host": "X"}]}
        assert svc.apply_filter("problems", data) == data


# ═══════════════════════════════════════════════════════════════════
# 🔌 GRUPO 7 — apply_filter() — Integração com Zabbix
# ═══════════════════════════════════════════════════════════════════
# Sprint 6c: filtros que enriquecem/anotam dados do collector Zabbix
# antes de chegarem no dashboard. Fail-open por design.


class TestApplyFilterHosts:
    """apply_filter('hosts', ...) — summary agregado."""

    def test_filter_hosts_desconta_maint_do_down(self, populated_state):
        """3 hosts em maint, down=5 → novo down=2, maint_manual=3."""
        svc = populated_state["svc"]
        data = {"total": 100, "up": 95, "down": 5, "up_pct": 95.0}
        result = svc.apply_filter("hosts", data)

        assert result["maint_manual"] == 3
        assert result["down"] == 2  # 5 - 3 hosts em maint
        assert result["total"] == 97  # desconta delta=3 do total
        # up_pct recalculado: 95 / 97 = 97.9
        assert result["up_pct"] == pytest.approx(97.9, abs=0.1)

    def test_filter_hosts_down_zero_nao_quebra(self, populated_state):
        """Down=0 (impossível na prática, mas testamos): não vai negativo."""
        svc = populated_state["svc"]
        data = {"total": 100, "up": 100, "down": 0, "up_pct": 100.0}
        result = svc.apply_filter("hosts", data)
        assert result["down"] == 0  # max(0, ...) protege
        assert result["maint_manual"] == 3

    def test_filter_hosts_data_invalida_retorna_original(self, populated_state):
        """data não-dict: retorna intacto."""
        svc = populated_state["svc"]
        assert svc.apply_filter("hosts", "string-invalida") == "string-invalida"
        assert svc.apply_filter("hosts", None) is None


class TestApplyFilterProblems:
    """apply_filter('problems', ...) — remove problems de hosts em maint."""

    def test_filter_problems_remove_hosts_em_maint(self, populated_state):
        svc = populated_state["svc"]
        data = {
            "items": [
                {"host": "CAM-8A-35", "severity": "high"},  # em maint
                {"host": "CAM-8A-36", "severity": "warning"},  # em maint
                {"host": "OUTRO-HOST", "severity": "high"},  # NÃO em maint
            ],
            "by_severity": {"high": 2, "warning": 1, "average": 0},
        }
        result = svc.apply_filter("problems", data)

        assert len(result["items"]) == 1
        assert result["items"][0]["host"] == "OUTRO-HOST"
        assert result["suppressed_by_maint"] == 2

    def test_filter_problems_recalcula_by_severity(self, populated_state):
        svc = populated_state["svc"]
        data = {
            "items": [
                {"host": "CAM-8A-35", "severity": "high"},
                {"host": "OK-HOST", "severity": "warning"},
            ],
            "by_severity": {"high": 1, "warning": 1, "average": 0},
        }
        result = svc.apply_filter("problems", data)
        # high zera (era do host em maint), warning fica 1
        assert result["by_severity"]["high"] == 0
        assert result["by_severity"]["warning"] == 1


class TestApplyFilterTriggers:
    """apply_filter('triggers', ...) — lista direta de triggers."""

    def test_filter_triggers_remove_hosts_em_maint(self, populated_state):
        svc = populated_state["svc"]
        data = [
            {"host": "CAM-8A-35", "trigger": "CPU high"},
            {"host": "OK-HOST", "trigger": "Disk full"},
        ]
        result = svc.apply_filter("triggers", data)
        assert len(result) == 1
        assert result[0]["host"] == "OK-HOST"

    def test_filter_triggers_data_nao_lista_passthrough(self, populated_state):
        svc = populated_state["svc"]
        assert svc.apply_filter("triggers", {"foo": "bar"}) == {"foo": "bar"}


class TestApplyFilterCFTV:
    """apply_filter('cftv', ...) — move down → maint_list."""

    def test_filter_cftv_move_down_para_maint(self, populated_state):
        svc = populated_state["svc"]
        data = {
            "down": 3,
            "maint": 0,
            "down_list": [
                {"host": "CAM-8A-35", "subcat": "cameras"},
                {"host": "OUTRO-CAM", "subcat": "cameras"},
            ],
            "maint_list": [],
            "by_subcat": {"cameras": {"down": 2, "maint": 0}},
        }
        result = svc.apply_filter("cftv", data)

        # CAM-8A-35 está em maint → movido
        assert result["maint_manual_count"] == 1
        assert len(result["down_list"]) == 1
        assert result["down_list"][0]["host"] == "OUTRO-CAM"
        assert len(result["maint_list"]) == 1
        assert result["maint_list"][0]["maint_manual"] is True
        assert result["down"] == 2  # 3 - 1
        assert result["maint"] == 1
        # by_subcat recalculado
        assert result["by_subcat"]["cameras"]["down"] == 1
        assert result["by_subcat"]["cameras"]["maint"] == 1

    def test_filter_cftv_funciona_com_name_em_vez_de_host(self, populated_state):
        """CFTV às vezes usa 'name' em vez de 'host'."""
        svc = populated_state["svc"]
        data = {
            "down": 1,
            "maint": 0,
            "down_list": [{"name": "CAM-8A-35", "subcat": "cameras"}],
            "maint_list": [],
            "by_subcat": {},
        }
        result = svc.apply_filter("cftv", data)
        assert result["maint_manual_count"] == 1

    def test_filter_cftv_sem_hosts_em_maint_passthrough(self, isolated_state):
        """Sem maint = sem mudança."""
        svc = isolated_state["svc"]
        data = {"down": 1, "down_list": [{"host": "X"}]}
        result = svc.apply_filter("cftv", data)
        assert result == data  # estado vazio → retorna original


class TestApplyFilterGeral:
    """Comportamentos transversais do apply_filter."""

    def test_key_invalido_retorna_data_original(self, populated_state):
        svc = populated_state["svc"]
        data = {"foo": "bar"}
        assert svc.apply_filter("key_que_nao_existe", data) == data

    def test_fail_open_em_caso_de_exception(self, populated_state, monkeypatch):
        """🛡️ Se algo dá ruim internamente, retorna data original (fail-open)."""
        svc = populated_state["svc"]

        # Sabota um filtro pra forçar exception
        def broken(*args, **kwargs):
            raise RuntimeError("simulated failure")

        monkeypatch.setattr(svc, "_mf_filter_problems", broken)

        data = {"items": [{"host": "X"}], "by_severity": {}}
        result = svc.apply_filter("problems", data)
        # Não levanta exception, retorna dados originais
        assert result == data
