"""CFTV: status de credencial (derivação das tags) e padronização de senha."""

from __future__ import annotations

import os
import time
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


def test_ip_invalido_nao_faz_request(monkeypatch: pytest.MonkeyPatch, com_creds: None) -> None:
    ultima = _captura(monkeypatch)
    r = padronizar_senha(host="c", ip="10.0.0.9; rm -rf /", vendor="intelbras", cred_atual="centro")
    assert r["status"] == "erro"
    assert ultima() == ()  # não montou URL nem chamou a câmera


def test_sem_padrao(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CFTV_CAM_PADRAO_USER", raising=False)
    monkeypatch.delenv("CFTV_CAM_PADRAO_PASS", raising=False)
    assert padronizar_senha(host="c", ip="10.0.0.9", vendor="intelbras", cred_atual="centro")["status"] == "sem_padrao"


# ── a rota exige login (proteção da ação sensível) ─────────────────────────────
def test_rota_exige_login(factory_app) -> None:
    with factory_app.test_client() as c:
        resp = c.post("/gov/cftv/padronizar-senha", data={"host": "c", "ip": "10.0.0.9"})
    assert resp.status_code in (301, 302, 401, 403)


# ── reteste da senha padrão depois do reset físico/console ─────────────────────
@pytest.fixture
def marcas(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    """Captura as marcações no Zabbix em vez de chamar a API."""
    feitas: dict[str, list[str]] = {"padrao": [], "recusa": []}
    monkeypatch.setattr(cftv_credencial, "_marcar_padrao_no_zabbix", lambda host: feitas["padrao"].append(host))
    monkeypatch.setattr(cftv_credencial, "_marcar_recusa_no_zabbix", lambda host: feitas["recusa"].append(host))
    return feitas


def test_retestar_ok_libera(monkeypatch: pytest.MonkeyPatch, com_creds: None, marcas: dict[str, list[str]]) -> None:
    chamadas: list[tuple] = []
    monkeypatch.setattr(cftv_credencial, "testar_credencial", lambda *a, **k: chamadas.append(a))
    r = cftv_credencial.retestar_padrao(host="cam1", ip="10.0.0.9", vendor="Intelbras")
    assert r["status"] == "ok"
    assert chamadas == [("10.0.0.9", "admin", "nova-padrao")]  # uma tentativa, só com a padrão
    assert marcas == {"padrao": ["cam1"], "recusa": []}


def test_retestar_recusada_renova_marca(
    monkeypatch: pytest.MonkeyPatch, com_creds: None, marcas: dict[str, list[str]]
) -> None:
    def recusa(*_a: object, **_k: object) -> None:
        raise AcessoNegadoError("10.0.0.9")

    monkeypatch.setattr(cftv_credencial, "testar_credencial", recusa)
    r = cftv_credencial.retestar_padrao(host="cam1", ip="10.0.0.9", vendor="Intelbras")
    assert r["status"] == "recusada"
    assert marcas == {"padrao": [], "recusa": ["cam1"]}


def test_retestar_gravador_sem_vendor_usa_cgi(
    monkeypatch: pytest.MonkeyPatch, com_creds: None, marcas: dict[str, list[str]]
) -> None:
    monkeypatch.setattr(cftv_credencial, "testar_credencial", lambda *a, **k: None)
    assert cftv_credencial.retestar_padrao(host="dvr1", ip="10.0.0.1", vendor="")["status"] == "ok"


@pytest.mark.parametrize(
    ("ip", "vendor", "esperado"),
    [("10.0.0.9; rm", "Intelbras", "erro"), ("10.0.0.9", "Hikvision", "nao_suportado")],
)
def test_retestar_barra_sem_tentar(
    monkeypatch: pytest.MonkeyPatch, com_creds: None, marcas: dict[str, list[str]], ip: str, vendor: str, esperado: str
) -> None:
    monkeypatch.setattr(cftv_credencial, "testar_credencial", lambda *a, **k: pytest.fail("não devia tentar"))
    assert cftv_credencial.retestar_padrao(host="x", ip=ip, vendor=vendor)["status"] == esperado


def test_retestar_sem_padrao(monkeypatch: pytest.MonkeyPatch, marcas: dict[str, list[str]]) -> None:
    monkeypatch.delenv("CFTV_CAM_PADRAO_USER", raising=False)
    monkeypatch.delenv("CFTV_CAM_PADRAO_PASS", raising=False)
    assert cftv_credencial.retestar_padrao(host="x", ip="10.0.0.9", vendor="Intelbras")["status"] == "sem_padrao"


def test_rota_retestar_exige_login(factory_app) -> None:
    with factory_app.test_client() as c:
        resp = c.post("/gov/cftv/retestar-senha", data={"host": "c", "ip": "10.0.0.9"})
    assert resp.status_code in (301, 302, 401, 403)


def test_visao_lista_recusadas_uma_a_uma(monkeypatch: pytest.MonkeyPatch) -> None:
    from itgov.api.v1 import cftv_monitoring

    monkeypatch.setattr(cftv_monitoring, "get_cached_historico_quedas", lambda: {})

    def dev(host: str, gravador: str, cred_status: str) -> dict:
        return {
            "host": host, "name": host, "ip": "10.0.0.1", "subcat": "camera", "andar": "?",
            "gravador": gravador, "is_gravador": False, "canal": "", "loja": "", "vendor": "Intelbras",
            "model": "", "status": "up", "problems": 0, "cred_status": cred_status,
        }  # fmt: skip

    dados = {"enabled": True, "devices": [dev("cam-b", "NVR-1", "recusada"), dev("cam-ok", "NVR-1", "ok"),
                                          dev("cam-a", "NVR-1", "recusada")]}  # fmt: skip
    v = cftv_monitoring.montar_visao(dados, {}, {}, {})
    assert v["cred_recusada_total"] == 2
    assert [d["host"] for d in v["cred_recusada_list"]] == ["cam-a", "cam-b"]


# ── equipamento liberado sai da lista sem esperar o cache de 2 min ─────────────
def test_marcar_padrao_tira_recusa_e_invalida_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    from itgov.api.v1 import cftv_monitoring

    chamadas: list[tuple[str, dict]] = []

    def zbx(metodo: str, params: dict) -> list[dict]:
        chamadas.append((metodo, params))
        if metodo == "host.get":
            return [{"hostid": "42", "tags": [{"tag": "cftv_cred_recusada", "value": "padrao:2026-10-28"}]}]
        return []

    invalidou: list[bool] = []
    monkeypatch.setattr(cftv_monitoring, "_zbx", zbx)
    monkeypatch.setattr(cftv_monitoring, "invalidar_cache_cftv", lambda: invalidou.append(True))
    cftv_credencial._marcar_padrao_no_zabbix("cam-adm-hauer-ch10")

    assert chamadas[-1] == ("host.update", {"hostid": "42", "tags": [{"tag": "cftv_cred", "value": "padrao"}]})
    assert invalidou == [True]


def test_invalidacao_vale_para_outros_workers(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """A marca em disco derruba o cache mesmo do worker que não atendeu o POST."""
    from itgov.api.v1 import cftv_monitoring

    buscas: list[int] = []
    monkeypatch.setattr(cftv_monitoring, "_MARCA_INVALIDADO", tmp_path / "marca")
    monkeypatch.setattr(cftv_monitoring, "_buscar_cftv", lambda: buscas.append(1) or {"devices": []})
    monkeypatch.setattr(cftv_monitoring, "_cache_data", None)

    cftv_monitoring.get_cached_cftv_summary()
    cftv_monitoring.get_cached_cftv_summary()
    assert len(buscas) == 1  # segunda leitura veio do cache

    # outro worker invalidou: só a marca muda, a memória deste worker continua cheia
    marca = tmp_path / "marca"
    marca.touch()
    os.utime(marca, (time.time() + 1, time.time() + 1))  # mtime do kernel é "coarse"; fixa depois da carga
    assert cftv_monitoring._cache_data is not None
    cftv_monitoring.get_cached_cftv_summary()
    assert len(buscas) == 2
