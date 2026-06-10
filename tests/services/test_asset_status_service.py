"""Testes do AssetStatusService e ZabbixAssetClient._classificar.

Cobertura:
  Classificação:
    1. Grupo mapeado → asset_type correto
    2. Grupo não mapeado → 'other'
    3. Host sem grupos → 'other'

  Derivação de status (icmpping):
    4. icmpping="1" → up
    5. icmpping="0" → down
    6. hostid ausente do mapa → unknown (nunca assume up)

  sync() — primeiro poll:
    7. Insere todos os hosts na tabela asset_status
    8. Não grava nenhuma transição (sem estado anterior)

  sync() — segundo poll, transição detectada:
    9.  Status mudou → transição gravada em asset_status_history
    10. duration_seconds calculado quando last_change está preenchido
    11. duration_seconds=None quando last_change é None (host inserido no 1º poll)

  sync() — segundo poll, sem transição:
    12. updated_at atualizado
    13. Nenhuma transição gravada

  Leitura — list_assets():
    14. Sem filtros → retorna todos
    15. Filtro asset_type → apenas hosts do tipo informado
    16. Filtro status → apenas hosts com o status informado

  Leitura — summary():
    17. total, por_tipo e por_status corretos

  Leitura — history():
    18. Retorna apenas transições dentro da janela de dias
    19. Filtro hostid → apenas transições do host informado
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from itgov.clients.zabbix_asset_client import HostInfo, ZabbixAssetClient
from itgov.models.db.asset_status import AssetStatusDB, AssetStatusHistoryDB
from itgov.models.db.base import Base
from itgov.services.asset_status_service import AssetStatusService

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def session():
    """SQLite in-memory isolado por teste."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as sess:
        yield sess
    engine.dispose()


@pytest.fixture
def svc(session: Session) -> AssetStatusService:
    return AssetStatusService(session)


def _host(hostid: str = "1001", asset_type: str = "camera") -> HostInfo:
    return HostInfo(hostid=hostid, host=f"host-{hostid}", name=f"Nome {hostid}", asset_type=asset_type)


def _icmp(hostid: str = "1001", value: str = "1") -> dict[str, str]:
    return {hostid: value}


# ── Classificação via ZabbixAssetClient ───────────────────────────────────────


class TestClassificacao:
    def test_grupo_camera_mapeado(self) -> None:
        client = ZabbixAssetClient(url="http://zbx", token="tok")
        assert client._classificar(["Cameras"]) == "camera"

    def test_grupo_server_mapeado(self) -> None:
        client = ZabbixAssetClient(url="http://zbx", token="tok")
        assert client._classificar(["Linux servers"]) == "server"

    def test_grupo_nao_mapeado_retorna_other(self) -> None:
        client = ZabbixAssetClient(url="http://zbx", token="tok")
        assert client._classificar(["Grupo Desconhecido"]) == "other"

    def test_host_sem_grupos_retorna_other(self) -> None:
        client = ZabbixAssetClient(url="http://zbx", token="tok")
        assert client._classificar([]) == "other"

    def test_group_map_customizado(self) -> None:
        client = ZabbixAssetClient(
            url="http://zbx",
            token="tok",
            group_map={"impressora": ["Printers"], "server": ["Servers"]},
        )
        assert client._classificar(["Printers"]) == "impressora"
        assert client._classificar(["Servers"]) == "server"
        assert client._classificar(["Cameras"]) == "other"


# ── Derivação de status via icmpping ─────────────────────────────────────────


class TestDerivacaoStatus:
    def test_icmpping_1_retorna_up(self, svc: AssetStatusService, session: Session) -> None:
        svc.sync([_host()], {"1001": "1"})
        reg = session.get(AssetStatusDB, "1001")
        assert reg is not None
        assert reg.status == "up"

    def test_icmpping_0_retorna_down(self, svc: AssetStatusService, session: Session) -> None:
        svc.sync([_host()], {"1001": "0"})
        reg = session.get(AssetStatusDB, "1001")
        assert reg.status == "down"

    def test_hostid_ausente_retorna_unknown(self, svc: AssetStatusService, session: Session) -> None:
        svc.sync([_host()], {})  # sem icmpping
        reg = session.get(AssetStatusDB, "1001")
        assert reg.status == "unknown"


# ── sync() — primeiro poll ────────────────────────────────────────────────────


class TestPrimeiroPoll:
    def test_insere_todos_os_hosts(self, svc: AssetStatusService, session: Session) -> None:
        hosts = [_host("1001"), _host("1002"), _host("1003", "server")]
        svc.sync(hosts, {"1001": "1", "1002": "0"})
        for hostid in ("1001", "1002", "1003"):
            assert session.get(AssetStatusDB, hostid) is not None

    def test_zero_transicoes_no_primeiro_poll(self, svc: AssetStatusService, session: Session) -> None:
        transicoes = svc.sync([_host("1001"), _host("1002")], {"1001": "1", "1002": "0"})
        assert transicoes == 0
        assert session.query(AssetStatusHistoryDB).count() == 0

    def test_last_change_nulo_no_primeiro_poll(self, svc: AssetStatusService, session: Session) -> None:
        svc.sync([_host()], {"1001": "1"})
        reg = session.get(AssetStatusDB, "1001")
        assert reg.last_change is None


# ── sync() — segundo poll com transição ───────────────────────────────────────


class TestSegundoPollTransicao:
    def test_transicao_gravada_quando_status_muda(self, svc: AssetStatusService, session: Session) -> None:
        svc.sync([_host()], {"1001": "1"})  # up
        transicoes = svc.sync([_host()], {"1001": "0"})  # down
        assert transicoes == 1
        h = session.query(AssetStatusHistoryDB).one()
        assert h.from_status == "up"
        assert h.to_status == "down"
        assert h.hostid == "1001"

    def test_duration_seconds_none_quando_sem_last_change(self, svc: AssetStatusService, session: Session) -> None:
        svc.sync([_host()], {"1001": "1"})  # primeiro poll → last_change=None
        svc.sync([_host()], {"1001": "0"})  # status muda, mas last_change era None
        h = session.query(AssetStatusHistoryDB).one()
        assert h.duration_seconds is None

    def test_duration_seconds_calculado_quando_ha_last_change(self, svc: AssetStatusService, session: Session) -> None:
        svc.sync([_host()], {"1001": "1"})  # insere com last_change=None
        svc.sync([_host()], {"1001": "0"})  # transição: last_change=None → duration=None

        # Simula last_change definido para próxima transição
        reg = session.get(AssetStatusDB, "1001")
        reg.last_change = datetime.now(UTC) - timedelta(seconds=120)
        session.commit()

        svc2 = AssetStatusService(session)
        svc2.sync([_host()], {"1001": "1"})  # volta para up
        historico = session.query(AssetStatusHistoryDB).order_by(AssetStatusHistoryDB.changed_at.desc()).first()
        assert historico.duration_seconds is not None
        assert historico.duration_seconds >= 100  # ao menos ~100s (tolerância de relógio)

    def test_last_change_atualizado_apos_transicao(self, svc: AssetStatusService, session: Session) -> None:
        antes = datetime.now()  # naive para comparar com SQLite (sem tz)
        svc.sync([_host()], {"1001": "1"})
        svc.sync([_host()], {"1001": "0"})
        reg = session.get(AssetStatusDB, "1001")
        assert reg.last_change is not None
        # SQLite armazena sem tzinfo — compara sem timezone
        lc = reg.last_change.replace(tzinfo=None) if reg.last_change.tzinfo else reg.last_change
        assert lc >= antes


# ── sync() — segundo poll sem transição ──────────────────────────────────────


class TestSegundoPollSemTransicao:
    def test_nenhuma_transicao_gravada(self, svc: AssetStatusService, session: Session) -> None:
        svc.sync([_host()], {"1001": "1"})
        transicoes = svc.sync([_host()], {"1001": "1"})
        assert transicoes == 0
        assert session.query(AssetStatusHistoryDB).count() == 0

    def test_updated_at_atualizado(self, svc: AssetStatusService, session: Session) -> None:
        svc.sync([_host()], {"1001": "1"})
        reg1 = session.get(AssetStatusDB, "1001")
        t1 = reg1.updated_at
        svc.sync([_host()], {"1001": "1"})
        session.expire(reg1)
        reg1 = session.get(AssetStatusDB, "1001")
        assert reg1.updated_at >= t1


# ── list_assets() ─────────────────────────────────────────────────────────────


class TestListAssets:
    def test_sem_filtro_retorna_todos(self, svc: AssetStatusService, session: Session) -> None:
        svc.sync([_host("1001", "camera"), _host("1002", "server")], {"1001": "1", "1002": "0"})
        result = svc.list_assets()
        assert len(result) == 2

    def test_filtro_asset_type(self, svc: AssetStatusService, session: Session) -> None:
        svc.sync([_host("1001", "camera"), _host("1002", "server")], {})
        cameras = svc.list_assets(asset_type="camera")
        assert len(cameras) == 1
        assert cameras[0].asset_type == "camera"

    def test_filtro_status(self, svc: AssetStatusService, session: Session) -> None:
        svc.sync([_host("1001"), _host("1002")], {"1001": "1", "1002": "0"})
        downs = svc.list_assets(status="down")
        assert len(downs) == 1
        assert downs[0].hostid == "1002"


# ── summary() ─────────────────────────────────────────────────────────────────


class TestSummary:
    def test_total_e_por_tipo(self, svc: AssetStatusService, session: Session) -> None:
        svc.sync(
            [
                _host("1001", "camera"),
                _host("1002", "camera"),
                _host("1003", "server"),
            ],
            {"1001": "1", "1002": "0"},
        )
        s = svc.summary()
        assert s.total == 3
        assert s.por_tipo["camera"] == 2
        assert s.por_tipo["server"] == 1

    def test_por_status(self, svc: AssetStatusService, session: Session) -> None:
        svc.sync([_host("1001"), _host("1002"), _host("1003")], {"1001": "1", "1002": "0"})
        s = svc.summary()
        assert s.por_status["up"] == 1
        assert s.por_status["down"] == 1
        assert s.por_status["unknown"] == 1

    def test_banco_vazio(self, svc: AssetStatusService) -> None:
        s = svc.summary()
        assert s.total == 0
        assert s.por_tipo == {}
        assert s.por_status == {}


# ── history() ─────────────────────────────────────────────────────────────────


class TestHistory:
    def test_retorna_transicoes_na_janela(self, svc: AssetStatusService, session: Session) -> None:
        svc.sync([_host()], {"1001": "1"})
        svc.sync([_host()], {"1001": "0"})
        result = svc.history(days=1)
        assert len(result) == 1
        assert result[0].from_status == "up"
        assert result[0].to_status == "down"

    def test_filtro_hostid(self, svc: AssetStatusService, session: Session) -> None:
        svc.sync([_host("1001"), _host("1002")], {"1001": "1", "1002": "1"})
        svc.sync([_host("1001"), _host("1002")], {"1001": "0", "1002": "0"})
        result = svc.history(days=1, hostid="1002")
        assert len(result) == 1
        assert result[0].hostid == "1002"

    def test_banco_sem_transicoes_retorna_lista_vazia(self, svc: AssetStatusService) -> None:
        assert svc.history(days=7) == []
