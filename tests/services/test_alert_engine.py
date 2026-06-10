"""Testes unitários do AlertEngine — anti-flap, supressão e recuperação."""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from itgov.models.db.alert import ActiveAlert, AlertLog
from itgov.services.alert_engine import AlertEngine, _formatar_alerta
from itgov.services.zabbix_hierarchy import HierarchyTree, HostNode

# ── Factories ─────────────────────────────────────────────────────────────────


def _dvr(hostid: str = "10", host: str = "DVR-1", ramo: str = "Centro") -> HostNode:
    return HostNode(hostid=hostid, host=host, name=host, ramo=ramo, role="dvr")


def _camera(
    hostid: str = "20",
    host: str = "cam-01",
    ramo: str = "Centro",
    dvr_parent: str = "DVR-1",
) -> HostNode:
    return HostNode(hostid=hostid, host=host, name=host, ramo=ramo, role="camera", dvr_parent=dvr_parent)


def _lojas(hostid: str = "30", host: str = "facial-01", ramo: str = "Norte") -> HostNode:
    return HostNode(hostid=hostid, host=host, name=host, ramo=ramo, role="lojas")


def _tree(dvr: HostNode | None = None, cameras: list[HostNode] | None = None) -> HierarchyTree:
    dvrs: dict = {}
    cams: dict = {}
    if dvr:
        dvrs[dvr.hostid] = dvr
        if cameras:
            dvr.cameras = [c.hostid for c in cameras]
    if cameras:
        cams = {c.hostid: c for c in cameras}
    return HierarchyTree(
        dvrs=dvrs,
        cameras=cams,
        standalone={},
        lojas={},
        all_hosts={**dvrs, **cams},
    )


# ── Session fake ──────────────────────────────────────────────────────────────


class _FakeSession:
    """Sessão SQLAlchemy em memória para testes (sem banco real)."""

    def __init__(self) -> None:
        self._added: list = []
        self._deleted: list = []
        self._store: dict = {}  # host_id → ActiveAlert
        self.committed = False

    def add(self, obj) -> None:
        self._added.append(obj)
        if isinstance(obj, ActiveAlert):
            self._store[obj.host_id] = obj

    def delete(self, obj) -> None:
        self._deleted.append(obj)
        self._store.pop(getattr(obj, "host_id", None), None)

    def get(self, model, pk):
        if model is ActiveAlert:
            return self._store.get(pk)
        return None

    def query(self, model):
        return _FakeQuery(self._added, model)

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        pass

    def commit(self) -> None:
        self.committed = True


class _FakeQuery:
    def __init__(self, items, model) -> None:
        self._items = [i for i in items if isinstance(i, model)]

    def filter(self, *args):
        return self

    def all(self) -> list:
        return self._items


def _session_factory_for(session: _FakeSession):
    @contextmanager
    def _inner():
        yield session

    return _inner


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _make_engine(
    dvr: HostNode | None = None,
    cameras: list[HostNode] | None = None,
    flap: int = 2,
) -> tuple[AlertEngine, _FakeSession]:
    tree = _tree(dvr, cameras or [])
    cache = MagicMock()
    cache.get.return_value = tree
    session = _FakeSession()
    engine = AlertEngine(
        hierarchy_cache=cache,
        session_factory=_session_factory_for(session),
        notifier=None,
        flap_threshold=flap,
    )
    return engine, session


# ── Testes: anti-flap ─────────────────────────────────────────────────────────


class TestAntiFlap:
    def test_nao_alerta_no_primeiro_poll_down(self) -> None:
        dvr = _dvr()
        engine, session = _make_engine(dvr=dvr, flap=2)
        engine.run_cycle({dvr.hostid: "0"})
        alertas = [o for o in session._added if isinstance(o, AlertLog)]
        assert alertas == [], "Não deve disparar no 1º poll"

    def test_alerta_no_segundo_poll_down(self) -> None:
        dvr = _dvr()
        engine, session = _make_engine(dvr=dvr, flap=2)
        engine.run_cycle({dvr.hostid: "0"})
        engine.run_cycle({dvr.hostid: "0"})
        alertas = [o for o in session._added if isinstance(o, AlertLog)]
        assert len(alertas) == 1
        assert alertas[0].tipo == "DVR_DOWN"

    def test_nao_duplica_alerta_no_terceiro_poll(self) -> None:
        dvr = _dvr()
        engine, session = _make_engine(dvr=dvr, flap=2)
        for _ in range(3):
            engine.run_cycle({dvr.hostid: "0"})
        alertas = [o for o in session._added if isinstance(o, AlertLog) and o.tipo == "DVR_DOWN"]
        assert len(alertas) == 1, "Alerta deve ser disparado apenas uma vez"

    def test_reset_contador_quando_up(self) -> None:
        dvr = _dvr()
        engine, _ = _make_engine(dvr=dvr, flap=2)
        engine.run_cycle({dvr.hostid: "0"})
        engine.run_cycle({dvr.hostid: "1"})  # sobe antes do threshold
        st = engine._states[dvr.hostid]
        assert st.down_count == 0
        assert not st.alerted

    def test_flap_threshold_1_alerta_no_primeiro_poll(self) -> None:
        dvr = _dvr()
        engine, session = _make_engine(dvr=dvr, flap=1)
        engine.run_cycle({dvr.hostid: "0"})
        alertas = [o for o in session._added if isinstance(o, AlertLog)]
        assert len(alertas) == 1


# ── Testes: supressão de câmeras-filhas ───────────────────────────────────────


class TestSupressaoCamerasDVRDown:
    def test_camera_suprimida_quando_dvr_down(self) -> None:
        dvr = _dvr()
        cam = _camera(dvr_parent="DVR-1")
        engine, session = _make_engine(dvr=dvr, cameras=[cam], flap=2)
        # DVR DOWN por 2 polls
        engine.run_cycle({dvr.hostid: "0", cam.hostid: "0"})
        engine.run_cycle({dvr.hostid: "0", cam.hostid: "0"})
        alertas = [o for o in session._added if isinstance(o, AlertLog)]
        tipos = {a.tipo for a in alertas}
        assert "DVR_DOWN" in tipos, "DVR deve ter alerta"
        assert "CAMERA_DOWN" not in tipos, "Câmera filha deve ser suprimida"

    def test_camera_alerta_quando_dvr_up(self) -> None:
        dvr = _dvr()
        cam = _camera(dvr_parent="DVR-1")
        engine, session = _make_engine(dvr=dvr, cameras=[cam], flap=2)
        # DVR UP, câmera DOWN por 2 polls
        engine.run_cycle({dvr.hostid: "1", cam.hostid: "0"})
        engine.run_cycle({dvr.hostid: "1", cam.hostid: "0"})
        alertas = [o for o in session._added if isinstance(o, AlertLog)]
        tipos = {a.tipo for a in alertas}
        assert "CAMERA_DOWN" in tipos
        assert "DVR_DOWN" not in tipos


# ── Testes: recuperação ───────────────────────────────────────────────────────


class TestRecuperacao:
    def test_recovery_apos_down_confirmado(self) -> None:
        dvr = _dvr()
        engine, session = _make_engine(dvr=dvr, flap=2)
        engine.run_cycle({dvr.hostid: "0"})
        engine.run_cycle({dvr.hostid: "0"})  # alerta disparado
        engine.run_cycle({dvr.hostid: "1"})  # recuperação

        recovery = [o for o in session._added if isinstance(o, AlertLog) and o.tipo == "RECOVERY"]
        assert len(recovery) == 1

    def test_recovery_nao_dispara_sem_alerta_previo(self) -> None:
        dvr = _dvr()
        engine, session = _make_engine(dvr=dvr, flap=2)
        engine.run_cycle({dvr.hostid: "1"})  # nunca ficou down
        recovery = [o for o in session._added if isinstance(o, AlertLog) and o.tipo == "RECOVERY"]
        assert recovery == []

    def test_estado_limpo_apos_recovery(self) -> None:
        dvr = _dvr()
        engine, _ = _make_engine(dvr=dvr, flap=2)
        engine.run_cycle({dvr.hostid: "0"})
        engine.run_cycle({dvr.hostid: "0"})
        engine.run_cycle({dvr.hostid: "1"})
        st = engine._states[dvr.hostid]
        assert not st.confirmed_down
        assert st.down_count == 0


# ── Testes: formatação de mensagens ───────────────────────────────────────────


class TestFormatacaoAlertas:
    def test_dvr_down_formato(self) -> None:
        dvr = _dvr(ramo="Centro")
        tipo, sev, msg = _formatar_alerta(dvr, n_filhos=3)
        assert tipo == "DVR_DOWN"
        assert sev == "critical"
        assert "CAUSA-RAIZ" not in msg  # mensagem não usa o label internamente
        assert "Centro" in msg
        assert "3" in msg

    def test_camera_down_formato(self) -> None:
        cam = _camera(ramo="Norte")
        tipo, sev, msg = _formatar_alerta(cam, n_filhos=0)
        assert tipo == "CAMERA_DOWN"
        assert sev == "warning"
        assert "Norte" in msg

    def test_ativo_lojas_formato(self) -> None:
        ativo = _lojas(ramo="Sul")
        tipo, sev, msg = _formatar_alerta(ativo, n_filhos=0)
        assert tipo == "ATIVO_DOWN"
        assert sev == "warning"
        assert "Sul" in msg


# ── Testes: notificador Teams ─────────────────────────────────────────────────


class TestTeamsNotifier:
    def test_notificacao_dvr_down_chamada(self) -> None:
        dvr = _dvr()
        cache = MagicMock()
        cache.get.return_value = _tree(dvr=dvr)
        session = _FakeSession()
        notifier = MagicMock()
        engine = AlertEngine(
            hierarchy_cache=cache,
            session_factory=_session_factory_for(session),
            notifier=notifier,
            flap_threshold=1,
        )
        with patch("itgov.services.alert_engine.teams_notify") as mock_notify:
            engine.run_cycle({dvr.hostid: "0"})
        mock_notify.assert_called_once()
        payload = mock_notify.call_args[0][0]
        assert payload["tipo"] == "DVR_DOWN"
        assert payload["severidade"] == "critico"

    def test_recovery_chama_notify_recovery(self) -> None:
        dvr = _dvr()
        cache = MagicMock()
        cache.get.return_value = _tree(dvr=dvr)
        session = _FakeSession()
        notifier = MagicMock()
        engine = AlertEngine(
            hierarchy_cache=cache,
            session_factory=_session_factory_for(session),
            notifier=notifier,
            flap_threshold=1,
        )
        with patch("itgov.services.alert_engine.teams_notify"):
            engine.run_cycle({dvr.hostid: "0"})  # dispara
        with patch("itgov.services.alert_engine.teams_notify") as mock_notify:
            engine.run_cycle({dvr.hostid: "1"})  # recovery
        mock_notify.assert_called_once()
        payload = mock_notify.call_args[0][0]
        assert payload["tipo"] == "RECOVERY"
        assert payload["severidade"] == "recovery"
