"""Testes do tema Gadens Institucional (cores por status, logo e tema no login)."""

from __future__ import annotations

import pytest

from app.ui.tema import COR_ATENCAO, COR_CRITICO, COR_OK, COR_SEM_DADO, cor_pilar, cor_score


@pytest.mark.parametrize(
    ("score", "cor"),
    [(None, COR_SEM_DADO), (100, COR_OK), (85.1, COR_OK), (85, COR_ATENCAO), (60, COR_ATENCAO), (59.9, COR_CRITICO)],
)
def test_cor_score_segue_85_e_60(score: float | None, cor: str) -> None:
    assert cor_score(score) == cor


def test_cor_pilar_aceita_dict_e_objeto_e_ignora_coming_soon() -> None:
    class Pilar:
        score = 90.0
        data_source = "live"

    assert cor_pilar({"score": 50.3, "data_source": "live"}) == COR_CRITICO
    assert cor_pilar(Pilar()) == COR_OK
    assert cor_pilar({"score": 0, "data_source": "coming_soon"}) == COR_SEM_DADO
    assert cor_pilar({"data_source": "live"}) == COR_SEM_DADO


def test_login_tem_logo_gadens_claro_e_escuro_e_o_tema(factory_client) -> None:
    html = factory_client.get("/gov/login").get_data(as_text=True)

    assert "img/gadens-logo.png" in html
    assert "img/gadens-logo-escuro.png" in html
    assert "fonts/app/fonts.css" in html
    assert "gadens:" in html  # paleta do tema na config do Tailwind
    assert "toggleDark" in html  # modo claro/escuro continua no login


def test_paginas_internas_usam_o_tema_e_o_simbolo(authed_client) -> None:
    html = authed_client.get("/gov/pillars").get_data(as_text=True)

    assert "img/gadens-simbolo-escuro.png" in html
    assert "fonts/app/fonts.css" in html
    assert "Visão Geral" in html and "Visao Geral" not in html
    assert "#3B82F6" not in html  # cor fixa antiga dos pilares
