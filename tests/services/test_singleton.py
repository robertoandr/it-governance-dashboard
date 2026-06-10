"""Testes unitários de itgov/services/singleton.py."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import itgov.services.singleton as _mod

# ── helpers ──────────────────────────────────────────────────────────────────


def _make_engine(url: str = "postgresql://user:pw@host/db") -> MagicMock:
    engine = MagicMock()
    engine.url = MagicMock()
    engine.url.__str__ = lambda _: url
    return engine


def _make_conn(scalar_result: bool) -> MagicMock:
    conn = MagicMock()
    conn.execute.return_value.scalar.return_value = scalar_result
    return conn


# ── SQLite bypass ─────────────────────────────────────────────────────────────


class TestSQLiteBypass:
    def test_sqlite_retorna_true_sem_consultar_bd(self) -> None:
        engine = _make_engine("sqlite:///itgov.db")
        engine.connect.return_value = MagicMock()

        with patch("itgov.services.singleton.get_engine", return_value=engine):
            resultado = _mod.try_acquire_poller_lock()

        assert resultado is True
        engine.connect.assert_not_called()

    def test_sqlite_memory_retorna_true(self) -> None:
        engine = _make_engine("sqlite:///:memory:")

        with patch("itgov.services.singleton.get_engine", return_value=engine):
            resultado = _mod.try_acquire_poller_lock()

        assert resultado is True


# ── PostgreSQL — lock disponível ──────────────────────────────────────────────


class TestPostgresLockAdquirido:
    def setup_method(self) -> None:
        # Reseta estado global entre testes
        _mod._lock_conn = None

    def test_retorna_true_quando_lock_disponivel(self) -> None:
        engine = _make_engine()
        conn = _make_conn(scalar_result=True)
        engine.connect.return_value = conn

        with patch("itgov.services.singleton.get_engine", return_value=engine):
            resultado = _mod.try_acquire_poller_lock()

        assert resultado is True

    def test_salva_conexao_global_para_manter_lock(self) -> None:
        engine = _make_engine()
        conn = _make_conn(scalar_result=True)
        engine.connect.return_value = conn

        with patch("itgov.services.singleton.get_engine", return_value=engine):
            _mod.try_acquire_poller_lock()

        assert _mod._lock_conn is conn

    def test_conexao_nao_fechada_quando_lock_adquirido(self) -> None:
        engine = _make_engine()
        conn = _make_conn(scalar_result=True)
        engine.connect.return_value = conn

        with patch("itgov.services.singleton.get_engine", return_value=engine):
            _mod.try_acquire_poller_lock()

        conn.close.assert_not_called()

    def test_usa_pg_try_advisory_lock_nao_bloqueante(self) -> None:
        engine = _make_engine()
        conn = _make_conn(scalar_result=True)
        engine.connect.return_value = conn

        with patch("itgov.services.singleton.get_engine", return_value=engine):
            _mod.try_acquire_poller_lock()

        sql_chamado = conn.execute.call_args[0][0].text
        assert "pg_try_advisory_lock" in sql_chamado
        assert "pg_advisory_lock" not in sql_chamado.replace("pg_try_advisory_lock", "")

    def test_passa_lock_key_correto(self) -> None:
        engine = _make_engine()
        conn = _make_conn(scalar_result=True)
        engine.connect.return_value = conn

        with patch("itgov.services.singleton.get_engine", return_value=engine):
            _mod.try_acquire_poller_lock()

        params = conn.execute.call_args[0][1]
        assert params["k"] == _mod.LOCK_KEY


# ── PostgreSQL — lock ocupado ─────────────────────────────────────────────────


class TestPostgresLockOcupado:
    def setup_method(self) -> None:
        _mod._lock_conn = None

    def test_retorna_false_quando_lock_ocupado(self) -> None:
        engine = _make_engine()
        conn = _make_conn(scalar_result=False)
        engine.connect.return_value = conn

        with patch("itgov.services.singleton.get_engine", return_value=engine):
            resultado = _mod.try_acquire_poller_lock()

        assert resultado is False

    def test_fecha_conexao_quando_nao_adquire(self) -> None:
        engine = _make_engine()
        conn = _make_conn(scalar_result=False)
        engine.connect.return_value = conn

        with patch("itgov.services.singleton.get_engine", return_value=engine):
            _mod.try_acquire_poller_lock()

        conn.close.assert_called_once()

    def test_nao_salva_conexao_global_quando_ocupado(self) -> None:
        engine = _make_engine()
        conn = _make_conn(scalar_result=False)
        engine.connect.return_value = conn

        with patch("itgov.services.singleton.get_engine", return_value=engine):
            _mod.try_acquire_poller_lock()

        assert _mod._lock_conn is None


# ── Exceção durante consulta ──────────────────────────────────────────────────


class TestPostgresErro:
    def setup_method(self) -> None:
        _mod._lock_conn = None

    def test_fecha_conexao_em_excecao(self) -> None:
        engine = _make_engine()
        conn = MagicMock()
        conn.execute.side_effect = RuntimeError("DB fora do ar")
        engine.connect.return_value = conn

        with (
            patch("itgov.services.singleton.get_engine", return_value=engine),
            pytest.raises(RuntimeError, match="DB fora do ar"),
        ):
            _mod.try_acquire_poller_lock()

        conn.close.assert_called_once()
