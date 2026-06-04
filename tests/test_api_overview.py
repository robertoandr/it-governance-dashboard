"""Tests for REST API endpoints: /api/overview, /api/pillars, /health."""

from __future__ import annotations

import json


class TestHealth:
    def test_health_200(self, factory_client) -> None:
        resp = factory_client.get("/health")
        assert resp.status_code == 200

    def test_health_json(self, factory_client) -> None:
        resp = factory_client.get("/health")
        data = json.loads(resp.data)
        assert data["status"] == "healthy"
        assert "version" in data
        assert "environment" in data


class TestOverview:
    def test_overview_200(self, factory_client) -> None:
        resp = factory_client.get("/api/overview")
        assert resp.status_code == 200

    def test_overview_has_global_score(self, factory_client) -> None:
        data = json.loads(factory_client.get("/api/overview").data)
        assert "global_score" in data
        assert isinstance(data["global_score"], (int, float))

    def test_overview_has_five_pillars(self, factory_client) -> None:
        data = json.loads(factory_client.get("/api/overview").data)
        assert len(data["pillars"]) == 5

    def test_overview_status_valid(self, factory_client) -> None:
        data = json.loads(factory_client.get("/api/overview").data)
        assert data["status"] in {"OPERACIONAL", "DEGRADADO", "CRÍTICO"}

    def test_overview_pillars_have_components(self, factory_client) -> None:
        data = json.loads(factory_client.get("/api/overview").data)
        for p in data["pillars"]:
            assert len(p["components"]) >= 3, f"Pillar {p['id']} has too few components"

    def test_overview_score_range(self, factory_client) -> None:
        data = json.loads(factory_client.get("/api/overview").data)
        assert 0 <= data["global_score"] <= 100


class TestPillars:
    def test_pillars_list_200(self, factory_client) -> None:
        resp = factory_client.get("/api/pillars")
        assert resp.status_code == 200

    def test_pillars_list_count(self, factory_client) -> None:
        data = json.loads(factory_client.get("/api/pillars").data)
        assert len(data) == 5

    def test_pillar_detail_200(self, factory_client) -> None:
        resp = factory_client.get("/api/pillars/risk_management")
        assert resp.status_code == 200

    def test_pillar_detail_has_score(self, factory_client) -> None:
        data = json.loads(factory_client.get("/api/pillars/risk_management").data)
        assert "score" in data
        assert isinstance(data["score"], (int, float))

    def test_pillar_detail_has_components(self, factory_client) -> None:
        data = json.loads(factory_client.get("/api/pillars/risk_management").data)
        assert "components" in data
        assert len(data["components"]) >= 3

    def test_pillar_all_ids_valid(self, factory_client) -> None:
        ids = [
            "strategic_alignment",
            "value_delivery",
            "risk_management",
            "resource_management",
            "performance_measure",
        ]
        for pid in ids:
            resp = factory_client.get(f"/api/pillars/{pid}")
            assert resp.status_code == 200, f"Expected 200 for {pid}, got {resp.status_code}"

    def test_pillar_unknown_returns_404(self, factory_client) -> None:
        resp = factory_client.get("/api/pillars/inexistente")
        assert resp.status_code == 404


class TestHTMLViews:
    def test_overview_html_200(self, factory_client) -> None:
        resp = factory_client.get("/")
        assert resp.status_code == 200

    def test_pillars_html_200(self, factory_client) -> None:
        resp = factory_client.get("/pillars")
        assert resp.status_code == 200

    def test_pillar_detail_html_200(self, factory_client) -> None:
        resp = factory_client.get("/pillars/risk_management")
        assert resp.status_code == 200

    def test_pillar_detail_html_404(self, factory_client) -> None:
        resp = factory_client.get("/pillars/unknown_pillar")
        assert resp.status_code == 404
