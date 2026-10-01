"""Detalhe do pilar: componentes sem coletor só aparecem "fora do cálculo" quando de fato ficaram fora."""

from __future__ import annotations

from flask import render_template

from app.models.governance import PillarID
from app.services.score_calculator import ScoreCalculator


def _render(factory_app, components: list[dict]) -> str:
    pillar = ScoreCalculator().calculate_pillar(PillarID.VALUE_DELIVERY, components)
    with factory_app.test_request_context("/gov/pillars/value_delivery"):
        return render_template("dashboards/pillar_detail.html", pillar=pillar, governance={})


def test_misto_marca_semente_como_fora_do_calculo(factory_app) -> None:
    html = _render(
        factory_app,
        [
            {"id": "real", "label": "Real", "value": 40.0, "source": "zabbix"},
            {"id": "seed", "label": "Semente", "value": 90.0, "source": "coming_soon"},
        ],
    )
    assert "sem coleta" in html
    assert "fora do cálculo" in html


def test_so_sementes_nao_diz_fora_do_calculo(factory_app) -> None:
    html = _render(
        factory_app,
        [
            {"id": "a", "label": "A", "value": 60.0, "source": "coming_soon"},
            {"id": "b", "label": "B", "value": 80.0, "source": "coming_soon"},
        ],
    )
    assert "fora do cálculo" not in html
    assert "sem coleta" not in html
