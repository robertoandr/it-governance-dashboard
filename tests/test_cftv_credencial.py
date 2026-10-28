"""CFTV: status de credencial (derivação das tags) e padronização de senha."""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest

from app.services import cftv_credencial
from app.services.cftv_credencial import padronizar_senha
from itgov.api.v1.cftv_monitoring import _cred_info
from itgov.services.intelbras_api import AcessoNegadoError


# ── derivação do cred_status a partir das tags do coletor ──────────────────────
@pytest.mark.parametrize(
    ("tags", "esperado"),
    [
        ({}, "sem_cred"),
        ({"cftv_cred": "centro"}, "pendente"),
        ({"cftv_cred": "centro", "model": "VIP-1230"}, "ok"),
        ({"cftv_cred": "centro", "cftv_cred_recusada": "centro:2026-10-20"}, "recusada"),
        ({"cftv_cred": "centro", "model": "X", "cftv_cred_recusada": "centro:2026-10-20"}, "recusada"),
    ],
)
def test_cred_status(tags: dict[str, str], esperado: str) -> None:
    assert _cred_info(tags)["cred_status"] == esperado


def test_cred_recusada_parseada() -> None:
    info = _cred_info({"cftv_cred": "centro", "cftv_cred_recusada": "centro:2026-10-20"})
    assert info["cred_recusada"] == {"cred": "centro", "data": "2026-10-20"}
    assert info["cred"] == "centro"


# ── padronização de senha ──────────────────────────────────────────────────────
@pytest.fixture
def com_creds(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Define padrão e centro; zera as demais para o teste não depender do ambiente."""
    monkeypatch.setenv("CFTV_CAM_PADRAO_USER", "admin")
    monkeypatch.setenv("CFTV_CAM_PADRAO_PASS", "nova-padrao")
    monkeypatch.setenv("CFTV_CAM_CENTRO_USER", "admin")
    monkeypatch.setenv("CFTV_CAM_CENTRO_PASS", "senha-centro")
    for var in ("CFTV_CAM_USER", "CFTV_CAM_TRIUNFO_PASS", "CFTV_CAM_TRIUNFO_PASS2"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(cftv_credencial, "_marcar_padrao_no_zabbix", lambda host: None)
    yield


def _captura(monkeypatch: pytest.MonkeyPatch) -> Callable[[], tuple]:
    chamadas: list[tuple] = []
    monkeypatch.setattr(cftv_credencial, "trocar_senha", lambda *a, **k: chamadas.append(a))
    return lambda: chamadas[-1] if chamadas else ()


def test_padroniza_com_sucesso(monkeypatch: pytest.MonkeyPatch, com_creds: None) -> None:
    ultima = _captura(monkeypatch)
    r = padronizar_senha(host="cam1", ip="10.0.0.9", vendor="Intelbras", cred_atual="centro")
    assert r["status"] == "ok"
    # loga com a credencial centro e grava a senha padrão, mantendo o usuário
    assert ultima() == ("10.0.0.9", "admin", "senha-centro", "nova-padrao")


def test_credencial_atual_recusada(monkeypatch: pytest.MonkeyPatch, com_creds: None) -> None:
    def recusa(*_a: object, **_k: object) -> None:
        raise AcessoNegadoError("10.0.0.9")

    monkeypatch.setattr(cftv_credencial, "trocar_senha", recusa)
    assert padronizar_senha(host="c", ip="10.0.0.9", vendor="intelbras", cred_atual="centro")["status"] == "recusada"


def test_vendor_nao_suportado_nao_chama_camera(monkeypatch: pytest.MonkeyPatch, com_creds: None) -> None:
    ultima = _captura(monkeypatch)
    r = padronizar_senha(host="c", ip="10.0.0.9", vendor="hikvision", cred_atual="centro")
    assert r["status"] == "nao_suportado"
    assert ultima() == ()  # nunca tocou na câmera


def test_ja_padrao(com_creds: None) -> None:
    assert padronizar_senha(host="c", ip="10.0.0.9", vendor="intelbras", cred_atual="padrao")["status"] == "ja_padrao"


def test_sem_credencial_atual(com_creds: None) -> None:
    # 'triunfo' não configurado no ambiente do teste
    assert padronizar_senha(host="c", ip="10.0.0.9", vendor="intelbras", cred_atual="triunfo")["status"] == "sem_cred"


def test_sem_padrao(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CFTV_CAM_PADRAO_USER", raising=False)
    monkeypatch.delenv("CFTV_CAM_PADRAO_PASS", raising=False)
    assert padronizar_senha(host="c", ip="10.0.0.9", vendor="intelbras", cred_atual="centro")["status"] == "sem_padrao"


# ── a rota exige login (proteção da ação sensível) ─────────────────────────────
def test_rota_exige_login(factory_app) -> None:
    with factory_app.test_client() as c:
        resp = c.post("/gov/cftv/padronizar-senha", data={"host": "c", "ip": "10.0.0.9"})
    assert resp.status_code in (301, 302, 401, 403)
