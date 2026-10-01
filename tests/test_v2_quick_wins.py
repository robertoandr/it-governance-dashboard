"""Ajustes globais da V2.0: nome, versão única, tema escuro e última atualização."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from app import config as app_config
from app.views.dashboards import _last_data_update

_ROOT = Path(__file__).resolve().parent.parent


def test_versao_vem_do_pyproject() -> None:
    esperado = tomllib.loads((_ROOT / "pyproject.toml").read_text())["project"]["version"]
    assert app_config._project_version() == esperado


def test_versao_sem_pyproject_cai_para_zero(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(app_config, "_PYPROJECT", tmp_path / "nao-existe.toml")
    assert app_config._project_version() == "0.0.0"


def test_nome_padrao() -> None:
    assert app_config.AppConfig.model_fields["name"].default == "Governança de TI 360"


def test_ultima_atualizacao_usa_coleta_mais_recente_em_horario_local() -> None:
    pilares = [
        {"id": "a", "last_collected": "2026-09-30T19:52:00Z"},
        {"id": "b", "last_collected": "2026-09-30T20:10:00+00:00"},
        {"id": "c", "last_collected": None},
    ]
    assert _last_data_update(pilares) == "30/09/2026 17:10"


def test_ultima_atualizacao_sem_dados_ou_invalida() -> None:
    assert _last_data_update([{"id": "a", "last_collected": None}]) is None
    assert _last_data_update([{"id": "a", "last_collected": "ontem"}]) is None


def test_tailwind_configurado_depois_do_script_no_login(factory_app) -> None:
    html = factory_app.test_client().get("/gov/login").get_data(as_text=True)
    assert "tailwind = {" not in html
    assert html.index("vendor/tailwind.js") < html.index("tailwind.config = { darkMode: 'class' }")


def test_home_mostra_ultima_atualizacao_e_nome(authed_client) -> None:
    html = authed_client.get("/gov/").get_data(as_text=True)
    assert "Última atualização em" in html
    assert "Calculado em" not in html
    assert "Governança de TI 360" in html
    assert "tailwind.config = { darkMode: 'class' }" in html
