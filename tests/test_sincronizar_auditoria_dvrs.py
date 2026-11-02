"""Testes do script de sincronização da planilha Dvrs.xlsx com dvr_unidades/cftv_canais."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import sincronizar_auditoria_dvrs as sync

_SCHEMA = """
CREATE TABLE dvr_unidades (
    id INTEGER PRIMARY KEY, dvr TEXT UNIQUE NOT NULL, unidade_id INTEGER, apelido TEXT,
    duplicado_de TEXT, canais INTEGER, acesso TEXT, modelo TEXT, serie TEXT
);
CREATE TABLE cftv_canais (
    id INTEGER PRIMARY KEY, ip_gravador TEXT, canal INTEGER, situacao TEXT, analogica INTEGER,
    ip_camera TEXT, equipamento TEXT, imagem TEXT, origem TEXT, conferido_em TEXT
);
"""

_LINHAS = [
    {"A": "Shopping_Novo", "B": "DVR", "C": "MHDX 3116", "F": "24/1/0/0", "G": "EKHG4700982I4", "J": "10.41.102.72"},
    {
        "A": "Shopping_praça_cancelas",
        "B": "NVR",
        "C": "iNVD 5132",
        "F": "32/3/16/4",
        "G": "KU9M2300494FG",
        "J": "10.41.100.174",
    },
    {"A": "k2", "B": "DVR", "C": "MHDX 3116", "F": "16/0/0/0", "G": "EKHH46017492E", "J": "?", "K": "?"},
]


def _banco(caminho: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(caminho)
    conn.executescript(_SCHEMA)
    conn.executemany(
        "INSERT INTO dvr_unidades (dvr, unidade_id, apelido, duplicado_de, canais, acesso, modelo, serie) "
        "VALUES (?, ?, ?, '', NULL, '', '', '')",
        [
            ("Shopping novo", None, "Shopping_Novo"),
            ("Shopping_Novo", 1, ""),  # fantasma criado com o nome da planilha
            ("Shopping_praça_cancelas", 1, ""),
            ("Shopping_Praca_Cancelas", None, ""),  # fantasma sem acento
            ("k2", None, ""),
        ],
    )
    conn.commit()
    return conn


def _registros(conn: sqlite3.Connection) -> dict[str, tuple]:
    return {r[0]: r[1:] for r in conn.execute("SELECT dvr, unidade_id, modelo, serie FROM dvr_unidades")}


def test_norm_ignora_acento_caixa_e_separadores() -> None:
    assert sync.norm("Shopping_praça cancelas") == sync.norm("SHOPPING-PRACA_CANCELAS")


def test_gerar_operacoes_ignora_gravador_desativado() -> None:
    dvrs, _ = sync.gerar_operacoes(_LINHAS)
    assert "k2" not in {d["nome"] for d in dvrs}


def test_aplicar_mantem_um_registro_por_gravador_e_remove_desativado(tmp_path: Path) -> None:
    conn = _banco(tmp_path / "app.db")
    dvrs, canais = sync.gerar_operacoes(_LINHAS)

    sync.aplicar_no_banco(conn, dvrs, canais)

    reg = _registros(conn)
    assert set(reg) == {"Shopping novo", "Shopping_praça_cancelas"}
    # Dados da planilha no registro mantido; unidade herdada do fantasma
    assert reg["Shopping novo"] == (1, "MHDX 3116", "EKHG4700982I4")
    assert reg["Shopping_praça_cancelas"] == (1, "iNVD 5132", "KU9M2300494FG")


def test_codigo_do_container_aplica_a_mesma_regra(tmp_path: Path) -> None:
    caminho = tmp_path / "app.db"
    _banco(caminho).close()
    dvrs, canais = sync.gerar_operacoes(_LINHAS)
    codigo = sync.codigo_container(dvrs, canais).replace("/app/data/app.db", str(caminho))

    res = subprocess.run([sys.executable, "-c", codigo], capture_output=True, text=True, check=False)

    assert res.returncode == 0, res.stderr
    assert "SUCESSO_PROD" in res.stdout
    with sqlite3.connect(caminho) as conn:
        assert set(_registros(conn)) == {"Shopping novo", "Shopping_praça_cancelas"}


@pytest.mark.parametrize("nome", ["k2", "K2"])
def test_desativado_e_reconhecido_sem_diferenciar_caixa(nome: str) -> None:
    dvrs, _ = sync.gerar_operacoes([{**_LINHAS[2], "A": nome}])
    assert dvrs == []
