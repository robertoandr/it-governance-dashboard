"""Tests for app/services/influx_client.py — retry, permanência de erros, parsing.

# Por que o teste original não funcionaria
# =========================================
#
# O teste proposto tinha três problemas:
#
# 1. ALVO DE PATCH ERRADO
#    ----------------------
#    Original:
#        mocker.patch("influxdb_client.WriteApi.write", side_effect=[...])
#
#    Por que falha: usamos InfluxDBClientAsync, cujo write API é WriteApiAsync,
#    não WriteApi (síncrono). Mesmo que fosse a classe certa, patchear na
#    definição da classe não afeta instâncias já criadas — o Python resolve o
#    método no objeto, não na classe, para AsyncMock.
#
#    Regra de ouro de mock: patchear WHERE IT IS USED, não where it is defined.
#    O caminho de chamada real em nosso código é:
#
#        write_point()
#          └─ _retry("write_point", _write)
#               └─ _write()                       ← closure definida em write_point
#                    └─ async with _make_client() as client:
#                         └─ await client.write_api().write(...)
#
#    O único ponto de injeção testável sem modificar o código de produção é:
#        "app.services.influx_client._make_client"
#
# 2. NOME DE CLASSE ERRADO
#    ----------------------
#    Original:  client = InfluxClient(...)
#    Correto:   service = InfluxService()   (sem argumentos no construtor)
#
# 3. INSTÂNCIAS vs. CLASSES NA side_effect
#    ----------------------------------------
#    Original:  side_effect=[ConnectionError, ConnectionError, None]
#    Correto:   side_effect=[ConnectionError("msg"), ConnectionError("msg"), None]
#
#    Quando o item é uma CLASSE (não instância), unittest.mock tenta chamá-la
#    como callable e pode se comportar de forma inesperada. Passando instâncias
#    (ou usando a lista como está — Python 3.8+ mock levanta exceções-instância
#    se o item for Exception subclass), o comportamento é determinístico.
#    Para clareza, sempre passe instâncias.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.influx_client import (
    InfluxService,
    SlaPoint,
    _is_permanent,
    _retry,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _point(uptime: float = 99.5) -> SlaPoint:
    """Build a minimal SlaPoint for tests.

    Args:
        uptime: uptime_pct field value (default 99.5).

    Returns:
        SlaPoint: Ready-to-write value object.
    """
    return SlaPoint(
        contrato_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        fornecedor_id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
        criticidade="alta",
        uptime_pct=uptime,
        incidentes=0,
        tempo_indisponivel_min=0,
    )


def _make_client_cm(write_side_effect=None, query_return=None):
    """Build a fully wired async-context-manager mock for _make_client().

    The returned mock supports:
        async with _make_client() as client:
            await client.write_api().write(...)
            await client.query_api().query(...)

    Args:
        write_side_effect: side_effect list for write_api().write AsyncMock.
        query_return: return_value for query_api().query AsyncMock.

    Returns:
        tuple[MagicMock, AsyncMock, AsyncMock]: (cm, write_mock, query_mock)
    """
    write_mock = AsyncMock(side_effect=write_side_effect)
    query_mock = AsyncMock(return_value=query_return or [])

    write_api = MagicMock()
    write_api.write = write_mock

    query_api = MagicMock()
    query_api.query = query_mock

    client = MagicMock()
    client.write_api.return_value = write_api
    client.query_api.return_value = query_api

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)

    return cm, write_mock, query_mock


# ---------------------------------------------------------------------------
# _is_permanent — unit (pure function, no I/O)
# ---------------------------------------------------------------------------

class TestIsPermanent:
    @pytest.mark.parametrize(
        "message,expected",
        [
            ("400 Bad Request", True),
            ("401 Unauthorized", True),
            ("403 Forbidden", True),
            ("404 Not Found", True),
            ("422 Unprocessable Entity", True),
            # Transient — must NOT be permanent
            ("500 Internal Server Error", False),
            ("503 Service Unavailable", False),
            ("Connection refused", False),
            ("timeout", False),
            ("network error", False),
        ],
    )
    def test_classification(self, message: str, expected: bool) -> None:
        assert _is_permanent(Exception(message)) is expected

    def test_case_insensitive(self) -> None:
        assert _is_permanent(Exception("401 UNAUTHORIZED")) is True

    def test_empty_message_is_transient(self) -> None:
        assert _is_permanent(Exception()) is False


# ---------------------------------------------------------------------------
# _retry — isolated unit tests (no InfluxDB, no flask context)
# ---------------------------------------------------------------------------

class TestRetryFunction:
    async def test_succeeds_on_first_try(self, mocker: pytest.fixture) -> None:
        """Successful first call — no sleep, returns value."""
        sleep_mock = mocker.patch("asyncio.sleep", new_callable=AsyncMock)
        coro = AsyncMock(return_value=42)

        result = await _retry("label", coro)

        assert result == 42
        coro.assert_called_once()
        sleep_mock.assert_not_called()

    async def test_retries_twice_then_succeeds(self, mocker: pytest.fixture) -> None:
        """Two transient failures → eventual success on attempt 3."""
        sleep_mock = mocker.patch("asyncio.sleep", new_callable=AsyncMock)
        coro = AsyncMock(
            side_effect=[ConnectionError("e1"), ConnectionError("e2"), "ok"]
        )

        result = await _retry("label", coro)

        assert result == "ok"
        assert coro.call_count == 3
        assert sleep_mock.call_count == 2

    async def test_exponential_backoff_delays(self, mocker: pytest.fixture) -> None:
        """Sleep durations follow 0.5 → 1.0 → 2.0 pattern (_BASE_DELAY_S * 2^attempt)."""
        sleep_mock = mocker.patch("asyncio.sleep", new_callable=AsyncMock)
        coro = AsyncMock(
            side_effect=[ConnectionError(), ConnectionError(), "ok"]
        )

        await _retry("label", coro)

        delays = [call.args[0] for call in sleep_mock.call_args_list]
        assert delays == pytest.approx([0.5, 1.0])

    async def test_raises_after_max_retries_exhausted(self, mocker: pytest.fixture) -> None:
        """All 3 attempts fail → original exception re-raised."""
        mocker.patch("asyncio.sleep", new_callable=AsyncMock)
        coro = AsyncMock(side_effect=ConnectionError("persistent"))

        with pytest.raises(ConnectionError, match="persistent"):
            await _retry("label", coro)

        assert coro.call_count == 3

    async def test_permanent_error_raises_immediately(self, mocker: pytest.fixture) -> None:
        """401 Unauthorized is permanent — no retry, no sleep."""
        sleep_mock = mocker.patch("asyncio.sleep", new_callable=AsyncMock)
        coro = AsyncMock(side_effect=Exception("401 Unauthorized"))

        with pytest.raises(Exception, match="401"):
            await _retry("label", coro)

        coro.assert_called_once()
        sleep_mock.assert_not_called()


# ---------------------------------------------------------------------------
# InfluxService.write_point — this is the CORRECTED version of the user's test
# ---------------------------------------------------------------------------

class TestWritePoint:
    async def test_write_retries_on_transient_error(self, mocker: pytest.fixture) -> None:
        """Garante que retry exponencial dispara em 5xx e desiste após N tentativas.

        Versão corrigida do teste original. Diferenças:
          • Patch target: "app.services.influx_client._make_client" (ponto de USO)
            em vez de "influxdb_client.WriteApi.write" (definição errada)
          • Classe correta: InfluxService() sem argumentos
          • side_effect com instâncias de exceção, não classes
          • asyncio.sleep mockado para o teste não durar 1.5s
        """
        cm, write_mock, _ = _make_client_cm(
            write_side_effect=[
                ConnectionError("transient 1"),
                ConnectionError("transient 2"),
                None,  # sucesso na 3ª tentativa
            ]
        )
        mocker.patch("app.services.influx_client._make_client", return_value=cm)
        mocker.patch("asyncio.sleep", new_callable=AsyncMock)

        await InfluxService().write_point(_point())

        assert write_mock.call_count == 3

    async def test_write_succeeds_on_first_try(self, mocker: pytest.fixture) -> None:
        cm, write_mock, _ = _make_client_cm(write_side_effect=[None])
        mocker.patch("app.services.influx_client._make_client", return_value=cm)

        await InfluxService().write_point(_point())

        write_mock.assert_called_once()

    async def test_write_gives_up_after_max_retries(self, mocker: pytest.fixture) -> None:
        """Todas as 3 tentativas falham → ConnectionError propagado."""
        cm, write_mock, _ = _make_client_cm(
            write_side_effect=ConnectionError("always fails")
        )
        mocker.patch("app.services.influx_client._make_client", return_value=cm)
        mocker.patch("asyncio.sleep", new_callable=AsyncMock)

        with pytest.raises(ConnectionError, match="always fails"):
            await InfluxService().write_point(_point())

        assert write_mock.call_count == 3

    async def test_write_does_not_retry_on_401(self, mocker: pytest.fixture) -> None:
        """Erro permanente (401) não dispara retry — fail-fast."""
        cm, write_mock, _ = _make_client_cm(
            write_side_effect=Exception("401 Unauthorized: invalid token")
        )
        mocker.patch("app.services.influx_client._make_client", return_value=cm)
        sleep_mock = mocker.patch("asyncio.sleep", new_callable=AsyncMock)

        with pytest.raises(Exception, match="401"):
            await InfluxService().write_point(_point())

        write_mock.assert_called_once()   # tentou apenas 1 vez
        sleep_mock.assert_not_called()    # não dormiu

    async def test_write_passes_correct_bucket_and_org(self, mocker: pytest.fixture) -> None:
        """Os parâmetros bucket e org chegam do Settings, não hardcoded."""
        cm, write_mock, _ = _make_client_cm(write_side_effect=[None])
        mocker.patch("app.services.influx_client._make_client", return_value=cm)

        await InfluxService().write_point(_point())

        _, kwargs = write_mock.call_args
        assert kwargs["bucket"] == "metrics"   # do Settings (test env injeta)
        assert "org" in kwargs

    async def test_write_tags_and_fields_are_set(self, mocker: pytest.fixture) -> None:
        """O Point construído contém os valores do SlaPoint (smoke test)."""
        captured: list = []

        async def capture_write(**kwargs):
            captured.append(kwargs.get("record"))

        cm, _, _ = _make_client_cm()
        cm.__aenter__.return_value.write_api.return_value.write = capture_write  # type: ignore[assignment]
        mocker.patch("app.services.influx_client._make_client", return_value=cm)

        pt = _point(uptime=87.3)
        await InfluxService().write_point(pt)

        assert len(captured) == 1
        influx_point = captured[0]
        # The Point serialises to line protocol — check via internal _fields
        assert influx_point._fields["uptime_pct"] == pytest.approx(87.3)
        assert influx_point._tags["criticidade"] == "alta"
        assert influx_point._tags["contrato_id"] == str(pt.contrato_id)


# ---------------------------------------------------------------------------
# InfluxService.query_sla_series
# ---------------------------------------------------------------------------

class TestQuerySlaSeries:
    def _make_record(self, ts: datetime, value: float) -> MagicMock:
        rec = MagicMock()
        rec.get_time.return_value = ts
        rec.get_value.return_value = value
        return rec

    def _make_table(self, records: list) -> MagicMock:
        t = MagicMock()
        t.records = records
        return t

    async def test_returns_parsed_records(self, mocker: pytest.fixture) -> None:
        """Timestamps e uptime_pct extraídos corretamente das tabelas Flux."""
        ts1 = datetime(2026, 5, 1, 10, tzinfo=UTC)
        ts2 = datetime(2026, 5, 1, 11, tzinfo=UTC)
        tables = [
            self._make_table([
                self._make_record(ts1, 99.1),
                self._make_record(ts2, 98.7),
            ])
        ]
        cm, _, query_mock = _make_client_cm(query_return=tables)
        mocker.patch("app.services.influx_client._make_client", return_value=cm)

        results = await InfluxService().query_sla_series(
            uuid.uuid4(), range_str="7d", window="1h"
        )

        assert len(results) == 2
        assert results[0] == {"timestamp": ts1, "uptime_pct": 99.1}
        assert results[1] == {"timestamp": ts2, "uptime_pct": 98.7}

    async def test_returns_empty_list_when_no_data(self, mocker: pytest.fixture) -> None:
        cm, _, _ = _make_client_cm(query_return=[])
        mocker.patch("app.services.influx_client._make_client", return_value=cm)

        results = await InfluxService().query_sla_series(uuid.uuid4())

        assert results == []

    async def test_flux_query_interpolates_contrato_id(self, mocker: pytest.fixture) -> None:
        """Garante que o UUID do contrato aparece no Flux gerado."""
        cm, _, query_mock = _make_client_cm(query_return=[])
        mocker.patch("app.services.influx_client._make_client", return_value=cm)

        cid = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        await InfluxService().query_sla_series(cid, range_str="30d", window="6h")

        flux_arg: str = query_mock.call_args.args[0]
        assert str(cid) in flux_arg
        assert "30d" in flux_arg
        assert "6h" in flux_arg

    async def test_retries_on_query_failure(self, mocker: pytest.fixture) -> None:
        """Erros transitivos no query também acionam retry."""
        call_count = 0

        async def flaky_query(flux, *, org):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("query timeout")
            return []

        cm = MagicMock()
        mock_client = MagicMock()
        mock_client.query_api.return_value.query = flaky_query
        cm.__aenter__ = AsyncMock(return_value=mock_client)
        cm.__aexit__ = AsyncMock(return_value=False)

        mocker.patch("app.services.influx_client._make_client", return_value=cm)
        mocker.patch("asyncio.sleep", new_callable=AsyncMock)

        results = await InfluxService().query_sla_series(uuid.uuid4())

        assert results == []
        assert call_count == 3


# ---------------------------------------------------------------------------
# InfluxService.query_avg_per_contrato
# ---------------------------------------------------------------------------

class TestQueryAvgPerContrato:
    async def test_returns_structured_dicts(self, mocker: pytest.fixture) -> None:
        cid = str(uuid.uuid4())
        fid = str(uuid.uuid4())

        rec = MagicMock()
        rec.values = {"contrato_id": cid, "fornecedor_id": fid, "criticidade": "critica"}
        rec.get_value.return_value = 95.3

        table = MagicMock()
        table.records = [rec]

        cm, _, _ = _make_client_cm(query_return=[table])
        mocker.patch("app.services.influx_client._make_client", return_value=cm)

        results = await InfluxService().query_avg_per_contrato("30d")

        assert results == [
            {
                "contrato_id": cid,
                "fornecedor_id": fid,
                "criticidade": "critica",
                "uptime_pct": 95.3,
            }
        ]

    async def test_flux_template_interpolates_range(self, mocker: pytest.fixture) -> None:
        cm, _, query_mock = _make_client_cm(query_return=[])
        mocker.patch("app.services.influx_client._make_client", return_value=cm)

        await InfluxService().query_avg_per_contrato("90d")

        flux_arg: str = query_mock.call_args.args[0]
        assert "90d" in flux_arg
        assert "sla_uptime" in flux_arg
        assert "uptime_pct" in flux_arg


# ---------------------------------------------------------------------------
# SlaPoint value object
# ---------------------------------------------------------------------------

class TestSlaPoint:
    def test_timestamp_defaults_to_utc_now(self) -> None:
        before = datetime.now(UTC)
        pt = _point()
        after = datetime.now(UTC)
        assert before <= pt.timestamp <= after

    def test_explicit_timestamp_is_preserved(self) -> None:
        ts = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        pt = SlaPoint(
            contrato_id=uuid.uuid4(),
            fornecedor_id=uuid.uuid4(),
            criticidade="media",
            uptime_pct=100.0,
            incidentes=0,
            tempo_indisponivel_min=0,
            timestamp=ts,
        )
        assert pt.timestamp == ts
