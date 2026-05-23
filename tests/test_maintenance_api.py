"""
tests/test_maintenance_api.py — Integration tests dos endpoints REST

Cobertura:
- GET endpoints (público)
- POST endpoints (com OPS_PIN)
- Contratos JSON
- Status codes
- Autenticação
"""

import json


# ═══════════════════════════════════════════════════════════════════
# 🌐 GRUPO 1 — Endpoints GET (leitura)
# ═══════════════════════════════════════════════════════════════════


class TestGetEndpoints:
    """Endpoints públicos de leitura."""

    def test_get_maintenance_vazio_200(self, client):
        r = client.get("/api/maintenance")
        assert r.status_code == 200
        body = r.get_json()
        assert body["ok"] is True
        assert body["hosts"] == []
        assert isinstance(body["hosts"], list)

    def test_get_stats_estrutura(self, client):
        r = client.get("/api/maintenance/stats")
        assert r.status_code == 200
        body = r.get_json()
        assert body["ok"] is True
        assert "stats" in body
        assert "total_in_maintenance" in body["stats"]
        assert "by_domain" in body["stats"]
        assert "by_operator" in body["stats"]

    def test_get_history_vazio(self, client):
        r = client.get("/api/maintenance/history")
        assert r.status_code == 200
        body = r.get_json()
        assert body["ok"] is True
        assert body["events"] == []

    def test_get_history_respeita_limit(self, client):
        r = client.get("/api/maintenance/history?limit=10")
        assert r.status_code == 200
        body = r.get_json()
        assert body["limit"] == 10

    def test_get_history_clamp_limit(self, client):
        """limit > 500 deve ser clampado pra 500."""
        r = client.get("/api/maintenance/history?limit=9999")
        body = r.get_json()
        assert body["limit"] == 500

    def test_get_debug_endpoint(self, client):
        r = client.get("/api/maintenance/_debug")
        assert r.status_code == 200
        body = r.get_json()
        assert body["ok"] is True
        assert body["module"] == "maintenance_service"
        assert "active_count" in body


# ═══════════════════════════════════════════════════════════════════
# 🔐 GRUPO 2 — Autenticação OPS_PIN
# ═══════════════════════════════════════════════════════════════════


class TestAutenticacao:
    """Endpoints de escrita exigem PIN válido."""

    def test_mark_sem_pin_401(self, client):
        r = client.post(
            "/api/maintenance/mark", json={"hosts": ["X"], "operator": "op", "reason": "r"}
        )
        assert r.status_code == 401
        body = r.get_json()
        assert body["error"] == "invalid_pin"

    def test_mark_pin_errado_401(self, client):
        r = client.post(
            "/api/maintenance/mark",
            json={"hosts": ["X"], "operator": "op", "reason": "r"},
            headers={"X-Ops-Pin": "PIN_ERRADO"},
        )
        assert r.status_code == 401

    def test_mark_pin_via_header_funciona(self, client, ops_pin):
        r = client.post(
            "/api/maintenance/mark",
            json={"hosts": ["H1"], "operator": "op", "reason": "r"},
            headers={"X-Ops-Pin": ops_pin},
        )
        assert r.status_code == 200
        assert r.get_json()["ok"] is True

    def test_mark_pin_via_body_funciona(self, client, ops_pin):
        r = client.post(
            "/api/maintenance/mark",
            json={
                "pin": ops_pin,
                "hosts": ["H1"],
                "operator": "op",
                "reason": "r",
            },
        )
        assert r.status_code == 200


# ═══════════════════════════════════════════════════════════════════
# ✏️ GRUPO 3 — POST /mark
# ═══════════════════════════════════════════════════════════════════


class TestMarkEndpoint:
    """POST /api/maintenance/mark."""

    def test_mark_sucesso(self, client, ops_pin):
        r = client.post(
            "/api/maintenance/mark",
            json={"hosts": ["CAM-1"], "operator": "rob", "reason": "tst"},
            headers={"X-Ops-Pin": ops_pin},
        )
        assert r.status_code == 200
        body = r.get_json()
        assert body["count"] == 1
        assert "CAM-1" in body["marked"]

    def test_mark_sem_hosts_400(self, client, ops_pin):
        r = client.post(
            "/api/maintenance/mark",
            json={"operator": "op", "reason": "r"},
            headers={"X-Ops-Pin": ops_pin},
        )
        assert r.status_code == 400
        assert r.get_json()["error"] == "invalid_hosts"

    def test_mark_sem_operator_400(self, client, ops_pin):
        r = client.post(
            "/api/maintenance/mark",
            json={"hosts": ["X"], "reason": "r"},
            headers={"X-Ops-Pin": ops_pin},
        )
        assert r.status_code == 400
        assert r.get_json()["error"] == "missing_operator"

    def test_mark_sem_reason_400(self, client, ops_pin):
        r = client.post(
            "/api/maintenance/mark",
            json={"hosts": ["X"], "operator": "op"},
            headers={"X-Ops-Pin": ops_pin},
        )
        assert r.status_code == 400
        assert r.get_json()["error"] == "missing_reason"

    def test_mark_hosts_string_vira_lista(self, client, ops_pin):
        """Body com 'hosts': 'X' (string) deve virar ['X']."""
        r = client.post(
            "/api/maintenance/mark",
            json={"hosts": "H1", "operator": "op", "reason": "r"},
            headers={"X-Ops-Pin": ops_pin},
        )
        assert r.status_code == 200
        assert r.get_json()["count"] == 1


# ═══════════════════════════════════════════════════════════════════
# 🧹 GRUPO 4 — POST /clear
# ═══════════════════════════════════════════════════════════════════


class TestClearEndpoint:
    """POST /api/maintenance/clear."""

    def test_clear_sucesso(self, client, ops_pin):
        # 1. Marca primeiro
        client.post(
            "/api/maintenance/mark",
            json={"hosts": ["H-CLEAR"], "operator": "op", "reason": "r"},
            headers={"X-Ops-Pin": ops_pin},
        )
        # 2. Limpa
        r = client.post(
            "/api/maintenance/clear",
            json={"hosts": ["H-CLEAR"], "operator": "op"},
            headers={"X-Ops-Pin": ops_pin},
        )
        assert r.status_code == 200
        body = r.get_json()
        assert body["count"] == 1
        assert "H-CLEAR" in body["cleared"]

    def test_clear_inexistente_vai_pra_not_found(self, client, ops_pin):
        r = client.post(
            "/api/maintenance/clear",
            json={"hosts": ["FANTASMA"], "operator": "op"},
            headers={"X-Ops-Pin": ops_pin},
        )
        assert r.status_code == 200
        body = r.get_json()
        assert body["count"] == 0
        assert "FANTASMA" in body["not_found"]


# ═══════════════════════════════════════════════════════════════════
# 🚨 GRUPO 5 — POST /release_all (endpoint perigoso)
# ═══════════════════════════════════════════════════════════════════


class TestReleaseAll:
    """Endpoint com 3 camadas de segurança."""

    def test_release_all_sem_confirm_400(self, client, ops_pin):
        r = client.post(
            "/api/maintenance/release_all",
            json={"operator": "op"},
            headers={"X-Ops-Pin": ops_pin},
        )
        assert r.status_code == 400
        assert r.get_json()["error"] == "confirm_required"

    def test_release_all_confirm_errado_400(self, client, ops_pin):
        r = client.post(
            "/api/maintenance/release_all?confirm=sim",
            json={"operator": "op"},
            headers={"X-Ops-Pin": ops_pin},
        )
        assert r.status_code == 400

    def test_release_all_sucesso(self, client, ops_pin):
        # Pré-marca 2 hosts
        client.post(
            "/api/maintenance/mark",
            json={"hosts": ["A", "B"], "operator": "op", "reason": "r"},
            headers={"X-Ops-Pin": ops_pin},
        )
        # Release all
        r = client.post(
            "/api/maintenance/release_all?confirm=YES_RELEASE_ALL",
            json={"operator": "admin", "note": "test"},
            headers={"X-Ops-Pin": ops_pin},
        )
        assert r.status_code == 200
        body = r.get_json()
        assert body["released"] == 2
        assert set(body["hosts"]) == {"A", "B"}

    def test_release_all_vazio_ok(self, client, ops_pin):
        """Sem hosts em manutenção: 200 OK, released=0."""
        r = client.post(
            "/api/maintenance/release_all?confirm=YES_RELEASE_ALL",
            json={"operator": "admin"},
            headers={"X-Ops-Pin": ops_pin},
        )
        assert r.status_code == 200
        body = r.get_json()
        assert body["released"] == 0


# ═══════════════════════════════════════════════════════════════════
# 🛡️ GRUPO 6 — Contrato JSON (Sprint 6f regression test)
# ═══════════════════════════════════════════════════════════════════


class TestContratoREST:
    """Garante que /api/maintenance retorna LIST (não dict)."""

    def test_get_maintenance_retorna_array(self, client, ops_pin):
        """🛡️ Sprint 6f: 'hosts' DEVE ser uma lista."""
        # Marca 2 hosts
        client.post(
            "/api/maintenance/mark",
            json={"hosts": ["X1", "X2"], "operator": "op", "reason": "r"},
            headers={"X-Ops-Pin": ops_pin},
        )

        r = client.get("/api/maintenance")
        body = r.get_json()
        assert isinstance(body["hosts"], list), "🚨 Sprint 6f: deve ser LIST!"
        assert len(body["hosts"]) == 2

        # Cada item tem o campo "host" injetado
        for item in body["hosts"]:
            assert "host" in item
            assert item["host"] in ("X1", "X2")
