"""Render tests for dashboard.html — inventory widget (issue #88).

Verifies that the template renders the expected HTML structure for the
inventory panel: nav item, panel section, KPI element IDs, JS fetch call.

Uses the shared `client` fixture from conftest.py which boots the full
Flask app in testing mode.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def html(client) -> str:
    """Render the dashboard and return the full HTML body as a string."""
    resp = client.get("/")
    assert resp.status_code == 200, f"Dashboard returned {resp.status_code}"
    return resp.data.decode("utf-8")


class TestInventoryNavItem:
    def test_nav_item_exists(self, html):
        assert 'data-tab="inventory"' in html

    def test_nav_item_has_label(self, html):
        assert ">Inventário<" in html

    def test_nav_item_has_badge_placeholder(self, html):
        assert 'id="nav-inventory-badge"' in html


class TestInventoryPanel:
    def test_panel_section_exists(self, html):
        assert 'data-panel="inventory"' in html

    def test_panel_has_id(self, html):
        assert 'id="panel-inventory"' in html

    def test_panel_has_title(self, html):
        assert "Inventário de Ativos de TI" in html

    def test_skeleton_exists(self, html):
        assert 'id="inv-skeleton"' in html

    def test_error_boundary_exists(self, html):
        assert 'id="inv-error"' in html

    def test_content_container_exists(self, html):
        assert 'id="inv-content"' in html


class TestInventoryKPIElements:
    def test_total_element(self, html):
        assert 'id="inv-total"' in html

    def test_sem_owner_element(self, html):
        assert 'id="inv-sem-owner"' in html

    def test_sem_owner_badge(self, html):
        assert 'id="inv-sem-owner-badge"' in html

    def test_sem_contrato_element(self, html):
        assert 'id="inv-sem-contrato"' in html

    def test_criticidade_list(self, html):
        assert 'id="inv-criticidade-list"' in html

    def test_tipo_list(self, html):
        assert 'id="inv-tipo-list"' in html


class TestInventoryJavaScript:
    def test_fetches_stats_endpoint(self, html):
        assert "/api/v1/ativos/stats" in html

    def test_has_update_function(self, html):
        assert "updateInventory" in html

    def test_has_setinterval_refresh(self, html):
        assert "setInterval(updateInventory" in html

    def test_has_error_handler(self, html):
        assert "inv-error" in html

    def test_criticidade_colors_defined(self, html):
        assert "CRITICIDADE_COLOR" in html
