"""/gov/triggers — abas Em aberto/Resolvidos e marcação de resolvido."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from unittest.mock import patch

import pytest
import requests
from flask import Flask
from flask.testing import FlaskClient

from itgov.api.v1 import zabbix_triggers as zt

_PREFIXO = "990"  # eventids só destes testes — limpos no teardown
_get_cached_resolved = zt.get_cached_resolved  # o autouse abaixo troca o do módulo


def _problema(eventid: str, *, manual_close: bool = False, acknowledged: bool = False, severity: int = 4) -> dict:
    return {
        "eventid": eventid,
        "triggerid": f"t{eventid}",
        "name": f"Problema {eventid}",
        "host": "srv-teste",
        "severity": severity,
        "severity_label": "Alto",
        "severity_color": "red",
        "acknowledged": acknowledged,
        "manual_close": manual_close,
        "since": "01/10 10:00",
        "since_iso": "2026-10-01T10:00:00-03:00",
        "zabbix_url": f"https://zbx/tr_events.php?triggerid=t{eventid}&eventid={eventid}",
    }


def _dados(problems: list[dict]) -> dict:
    return {
        "enabled": True,
        "total": len(problems),
        "problems": problems,
        "counts": {},
        "updated_at": "01/10 10:00",
    }


def _resolvidos(items: list[dict] | None = None, **extra: object) -> dict:
    return {"items": items or [], "truncated": False, "days": 7, "error": None, **extra}


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch: pytest.MonkeyPatch, factory_app: Flask) -> Iterator[None]:
    monkeypatch.setenv("ZABBIX_URL", "https://zbx.local")
    with patch.object(zt, "get_cached_resolved", return_value=_resolvidos()):
        yield
    from app.extensions import db
    from app.models.trigger_resolucao import TriggerResolucao

    with factory_app.app_context():
        TriggerResolucao.query.filter(TriggerResolucao.eventid.like(f"{_PREFIXO}%")).delete(synchronize_session=False)
        db.session.commit()


@pytest.fixture
def client_as(factory_app: Flask) -> Callable[[str], FlaskClient]:
    """Return a factory that yields a test client logged in with the given role."""
    from app.extensions import db
    from app.models.user import User

    def _make(role: str) -> FlaskClient:
        email = f"pytest-triggers-{role}@test.local"
        with factory_app.app_context():
            user = User.query.filter_by(email=email).first()
            if user is None:
                user = User(name=f"Pytest {role}", email=email, role=role)
                user.set_password("pytest-only-not-real")
                db.session.add(user)
                db.session.commit()
            user_id = user.id
        client = factory_app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user_id)
            sess["_fresh"] = True
        return client

    return _make


def _marcas(factory_app: Flask) -> dict:
    from app.models.trigger_resolucao import TriggerResolucao

    with factory_app.app_context():
        return {
            m.eventid: m for m in TriggerResolucao.query.filter(TriggerResolucao.eventid.like(f"{_PREFIXO}%")).all()
        }


class TestPagina:
    def test_separa_abertos_e_resolvidos(self, client_as: Callable[[str], FlaskClient]) -> None:
        resolvido_zbx = {**_problema("9903"), "resolved_at": "01/10 11:00", "resolved_iso": "x"}
        dados = _dados([_problema("9901"), _problema("9902")])
        client = client_as("operador")
        with (
            patch.object(zt, "get_cached_triggers", return_value=dados),
            patch.object(zt, "get_cached_resolved", return_value=_resolvidos([resolvido_zbx])),
            patch.object(zt, "resolve_problem", return_value=True),
        ):
            assert client.post("/gov/triggers/9902/resolve", json={"nota": "trocado o disco"}).status_code == 200
            html = client.get("/gov/triggers").get_data(as_text=True)

        assert 'x-data="triggersPage"' in html
        aberto, resolvidos = html.split("═══ Resolvidos ═══")
        assert "Problema 9901" in aberto
        assert "Problema 9902" not in aberto
        assert "Problema 9902" in resolvidos
        assert "Zabbix ainda ativo" in resolvidos
        assert "por Pytest operador" in resolvidos
        assert "Problema 9903" in resolvidos
        assert "Normalizado" in resolvidos

    def test_sem_x_data_inline(self, client_as: Callable[[str], FlaskClient]) -> None:
        # O build CSP do Alpine não avalia objeto literal em x-data.
        with patch.object(zt, "get_cached_triggers", return_value=_dados([_problema("9901")])):
            html = client_as("gestor").get("/gov/triggers").get_data(as_text=True)
        assert 'x-data="{' not in html

    @pytest.mark.parametrize(
        ("resolved", "aviso"),
        [
            (_resolvidos(error="timeout"), "Não foi possível buscar o histórico de resolvidos"),
            (_resolvidos(truncated=True), "a lista abaixo pode estar incompleta"),
        ],
    )
    def test_falha_nos_resolvidos_nao_esconde_abertos(
        self, client_as: Callable[[str], FlaskClient], resolved: dict, aviso: str
    ) -> None:
        with (
            patch.object(zt, "get_cached_triggers", return_value=_dados([_problema("9901")])),
            patch.object(zt, "get_cached_resolved", return_value=resolved),
        ):
            html = client_as("gestor").get("/gov/triggers").get_data(as_text=True)
        assert "Problema 9901" in html
        assert aviso in html

    def test_zabbix_indisponivel(self, client_as: Callable[[str], FlaskClient]) -> None:
        dados = {"enabled": False, "reason": "zabbix_not_configured", "problems": [], "counts": {}}
        with (
            patch.object(zt, "get_cached_triggers", return_value=dados),
            patch.object(zt, "get_cached_resolved") as resolved,
        ):
            resp = client_as("admin").get("/gov/triggers")
        resolved.assert_not_called()
        assert resp.status_code == 200
        assert "Zabbix não disponível" in resp.get_data(as_text=True)

    def test_visualizador_sem_acesso(self, client_as: Callable[[str], FlaskClient]) -> None:
        assert client_as("visualizador").get("/gov/triggers").status_code == 403


class TestResolver:
    def test_sem_manual_close_faz_ack_e_grava_marca(
        self, client_as: Callable[[str], FlaskClient], factory_app: Flask
    ) -> None:
        with (
            patch.object(zt, "get_cached_triggers", return_value=_dados([_problema("9901")])),
            patch.object(zt, "resolve_problem", return_value=True) as resolve,
        ):
            resp = client_as("operador").post("/gov/triggers/9901/resolve", json={"nota": "ok"})

        assert resp.get_json() == {"ok": True, "fechado_no_zabbix": False}
        kwargs = resolve.call_args.kwargs
        assert kwargs["close"] is False
        assert kwargs["acknowledged"] is False
        assert kwargs["message"] == "Resolvido via Governança de TI 360 por Pytest operador: ok"
        marca = _marcas(factory_app)["9901"]
        assert marca.fechado_no_zabbix is False
        assert marca.nota == "ok"
        assert marca.host == "srv-teste"

    def test_com_manual_close_fecha_no_zabbix(
        self, client_as: Callable[[str], FlaskClient], factory_app: Flask
    ) -> None:
        dados = _dados([_problema("9901", manual_close=True, acknowledged=True)])
        with (
            patch.object(zt, "get_cached_triggers", return_value=dados),
            patch.object(zt, "resolve_problem", return_value=True) as resolve,
        ):
            resp = client_as("admin").post("/gov/triggers/9901/resolve")

        assert resp.get_json()["fechado_no_zabbix"] is True
        assert resolve.call_args.kwargs["close"] is True
        assert resolve.call_args.kwargs["acknowledged"] is True
        assert resolve.call_args.kwargs["message"] == "Resolvido via Governança de TI 360 por Pytest admin"
        assert _marcas(factory_app)["9901"].fechado_no_zabbix is True

    def test_problema_inexistente_da_404(self, client_as: Callable[[str], FlaskClient]) -> None:
        with (
            patch.object(zt, "get_cached_triggers", return_value=_dados([])),
            patch.object(zt, "resolve_problem") as resolve,
        ):
            resp = client_as("operador").post("/gov/triggers/9909/resolve")
        assert resp.status_code == 404
        resolve.assert_not_called()

    def test_rele_zabbix_antes_de_resolver(self, client_as: Callable[[str], FlaskClient]) -> None:
        # ack feito no Zabbix há segundos: o cache ainda diria acknowledged=False
        # e o ack repetido faria o Zabbix recusar a operação.
        ordem: list[str] = []
        with (
            patch.object(zt, "invalidate_cache", side_effect=lambda: ordem.append("invalidate")),
            patch.object(
                zt,
                "get_cached_triggers",
                side_effect=lambda: ordem.append("get") or _dados([_problema("9901", acknowledged=True)]),
            ),
            patch.object(zt, "resolve_problem", return_value=True) as resolve,
        ):
            client_as("operador").post("/gov/triggers/9901/resolve")
        assert ordem == ["invalidate", "get"]
        assert resolve.call_args.kwargs["acknowledged"] is True

    def test_zabbix_recusa_nao_grava_marca(self, client_as: Callable[[str], FlaskClient], factory_app: Flask) -> None:
        with (
            patch.object(zt, "get_cached_triggers", return_value=_dados([_problema("9901")])),
            patch.object(zt, "resolve_problem", return_value=False),
        ):
            resp = client_as("operador").post("/gov/triggers/9901/resolve")
        assert resp.status_code == 502
        assert "9901" not in _marcas(factory_app)

    def test_ja_marcado_e_idempotente(self, client_as: Callable[[str], FlaskClient]) -> None:
        client = client_as("operador")
        with (
            patch.object(zt, "get_cached_triggers", return_value=_dados([_problema("9901")])),
            patch.object(zt, "resolve_problem", return_value=True) as resolve,
        ):
            client.post("/gov/triggers/9901/resolve")
            assert client.post("/gov/triggers/9901/resolve").get_json() == {"ok": True}
        assert resolve.call_count == 1

    def test_visualizador_nao_resolve(self, client_as: Callable[[str], FlaskClient]) -> None:
        with patch.object(zt, "resolve_problem") as resolve:
            assert client_as("visualizador").post("/gov/triggers/9901/resolve").status_code == 403
        resolve.assert_not_called()


class TestReabrir:
    def _resolver(self, client: FlaskClient, problema: dict) -> None:
        with (
            patch.object(zt, "get_cached_triggers", return_value=_dados([problema])),
            patch.object(zt, "resolve_problem", return_value=True),
        ):
            client.post(f"/gov/triggers/{problema['eventid']}/resolve")

    def test_reabrir_remove_marca(self, client_as: Callable[[str], FlaskClient], factory_app: Flask) -> None:
        client = client_as("gestor")
        self._resolver(client, _problema("9901"))
        resp = client.post("/gov/triggers/9901/reopen")
        assert resp.get_json() == {"ok": True}
        assert "9901" not in _marcas(factory_app)

    def test_fechado_no_zabbix_nao_reabre(self, client_as: Callable[[str], FlaskClient], factory_app: Flask) -> None:
        client = client_as("gestor")
        self._resolver(client, _problema("9901", manual_close=True))
        assert client.post("/gov/triggers/9901/reopen").status_code == 409
        assert "9901" in _marcas(factory_app)

    def test_sem_marca_da_404(self, client_as: Callable[[str], FlaskClient]) -> None:
        assert client_as("gestor").post("/gov/triggers/9909/reopen").status_code == 404


class TestApiZabbix:
    @pytest.fixture(autouse=True)
    def _cache(self, monkeypatch: pytest.MonkeyPatch) -> None:
        zt._cache_dados = None
        zt._cache_ts = 0.0
        zt._resolved_cache.update(dados=None, ts=0.0)
        monkeypatch.setenv("ZABBIX_TOKEN", "tok")
        monkeypatch.setattr(zt.time, "time", lambda: 1759310000.0)  # 01/10/2025 09:13 UTC

    @pytest.mark.parametrize(
        ("close", "acknowledged", "action"),
        [(False, False, 6), (False, True, 4), (True, False, 7), (True, True, 5)],
    )
    def test_resolve_problem_monta_action(self, close: bool, acknowledged: bool, action: int) -> None:
        zt._cache_dados = {"enabled": True}
        with patch.object(zt, "_zbx", return_value={}) as zbx:
            assert zt.resolve_problem("10", close=close, acknowledged=acknowledged, message="m") is True
        assert zbx.call_args.args == ("event.acknowledge", {"eventids": ["10"], "action": action, "message": "m"})
        assert zt._cache_dados is None

    def test_resolve_problem_falha(self) -> None:
        zt._cache_dados = {"enabled": True}
        with patch.object(zt, "_zbx", side_effect=RuntimeError("sem permissão")):
            assert zt.resolve_problem("10", close=True, acknowledged=False, message="m") is False
        assert zt._cache_dados == {"enabled": True}

    def test_resolve_problem_erro_de_rede(self) -> None:
        with patch.object(zt, "_zbx", side_effect=requests.ConnectionError("recusado")):
            assert zt.resolve_problem("10", close=False, acknowledged=False, message="m") is False

    def test_fetch_problems_traz_manual_close(self) -> None:
        problems = [{"eventid": "1", "objectid": "t1", "name": "x", "severity": "3", "clock": "1759200000"}]
        triggers = [{"triggerid": "t1", "manual_close": "1", "hosts": [{"name": "h"}]}]
        with patch.object(zt, "_zbx", side_effect=[problems, triggers]) as zbx:
            result = zt._fetch_problems()
        assert result[0]["manual_close"] is True
        assert "manual_close" in zbx.call_args_list[1].args[1]["output"]

    def test_fetch_resolved_so_normalizados_com_hora_de_recuperacao(self) -> None:
        events = [
            {
                "eventid": "5",
                "objectid": "t5",
                "name": "Link caiu",
                "severity": "4",
                "clock": "1759300000",
                "r_eventid": "6",
                "hosts": [{"name": "fw"}],
            },
            {
                "eventid": "7",
                "objectid": "t7",
                "name": "Ainda ativo",
                "severity": "2",
                "clock": "1759300100",
                "r_eventid": "0",
            },
        ]
        recovery = [{"eventid": "6", "clock": "1759303600"}]
        with patch.object(zt, "_zbx", side_effect=[events, recovery]) as zbx:
            result, truncated = zt._fetch_resolved()

        assert truncated is False
        assert [r["eventid"] for r in result] == ["5"]
        item = result[0]
        assert item["host"] == "fw"
        assert item["since"] == "01/10 03:26"  # 1759300000 = 01/10/2025 06:26 UTC → Brasília
        assert item["resolved_at"] == "01/10 04:26"
        assert item["zabbix_url"].endswith("triggerid=t5&eventid=5")
        assert zbx.call_args_list[1].args[1]["eventids"] == ["6"]
        assert zbx.call_args_list[0].args[1]["time_from"] == 1759310000 - 30 * 86400

    def test_fetch_resolved_janela_pela_normalizacao(self) -> None:
        dia = 86400
        agora = 1759310000
        events = [
            # aberto há 20 dias, normalizou ontem → entra
            {
                "eventid": "1",
                "objectid": "t1",
                "name": "antigo",
                "severity": "3",
                "clock": str(agora - 20 * dia),
                "r_eventid": "2",
            },
            # aberto e normalizado há 10 dias → fora da janela de 7
            {
                "eventid": "3",
                "objectid": "t3",
                "name": "velho",
                "severity": "3",
                "clock": str(agora - 10 * dia),
                "r_eventid": "4",
            },
            # aberto há 2 dias, normalizou há 1h → entra, primeiro da lista
            {
                "eventid": "5",
                "objectid": "t5",
                "name": "novo",
                "severity": "3",
                "clock": str(agora - 2 * dia),
                "r_eventid": "6",
            },
        ]
        recovery = [
            {"eventid": "2", "clock": str(agora - dia)},
            {"eventid": "4", "clock": str(agora - 9 * dia)},
            {"eventid": "6", "clock": str(agora - 3600)},
        ]
        with patch.object(zt, "_zbx", side_effect=[events, recovery]):
            result, _ = zt._fetch_resolved()
        assert [r["eventid"] for r in result] == ["5", "1"]
        assert "_r_clock" not in result[0]

    def test_fetch_resolved_avisa_truncamento(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(zt, "_RESOLVED_LIMIT", 2)
        events = [
            {"eventid": "1", "objectid": "t1", "name": "a", "severity": "3", "clock": "1759300000", "r_eventid": "0"},
            {"eventid": "2", "objectid": "t2", "name": "b", "severity": "3", "clock": "1759300000", "r_eventid": "0"},
        ]
        with patch.object(zt, "_zbx", return_value=events) as zbx:
            assert zt._fetch_resolved() == ([], True)
        assert zbx.call_count == 1

    def test_fetch_resolved_vazio_nao_busca_recuperacao(self) -> None:
        with patch.object(zt, "_zbx", return_value=[]) as zbx:
            assert zt._fetch_resolved() == ([], False)
        assert zbx.call_count == 1

    def test_get_cached_resolved_usa_cache_e_invalidate_limpa(self) -> None:
        with patch.object(zt, "_fetch_resolved", return_value=([{"eventid": "1"}], False)) as fetch:
            primeiro = _get_cached_resolved()
            assert _get_cached_resolved() is primeiro
            zt.invalidate_cache()
            _get_cached_resolved()
        assert primeiro == {"items": [{"eventid": "1"}], "truncated": False, "days": 7, "error": None}
        assert fetch.call_count == 2

    def test_get_cached_resolved_falha_nao_quebra(self) -> None:
        with patch.object(zt, "_fetch_resolved", side_effect=requests.Timeout("lento")):
            result = _get_cached_resolved()
        assert result["items"] == []
        assert "lento" in result["error"]
        assert zt._resolved_cache["dados"] is None  # erro não fica em cache

    def test_get_cached_resolved_sem_configuracao(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ZABBIX_TOKEN", "")
        with patch.object(zt, "_fetch_resolved") as fetch:
            assert _get_cached_resolved()["error"] == "zabbix_not_configured"
        fetch.assert_not_called()
