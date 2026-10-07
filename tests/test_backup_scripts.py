"""Testes dos scripts de backup externo (sync_cloud.sh e backup_config.sh).

Rodam os scripts de verdade com `rclone` falso no PATH (registra os argumentos)
e `gpg` real, num diretório temporário — nada sai da máquina.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tarfile
from io import BytesIO
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _rclone_falso(bin_dir: Path, registro: Path, falha_em: str = "") -> None:
    """Cria um `rclone` que grava cada chamada em `registro` e falha no subcomando `falha_em`."""
    script = bin_dir / "rclone"
    script.write_text(f'#!/usr/bin/env bash\necho "$*" >> "{registro}"\n[ "$1" = "{falha_em}" ] && exit 1\nexit 0\n')
    script.chmod(0o755)


def _rodar_sync(tmp_path: Path, extra_env: dict[str, str] | None = None, falha_em: str = "") -> tuple[int, list[str]]:
    instancia = tmp_path / "inst"
    (instancia / "backups").mkdir(parents=True)
    (instancia / "backups" / "zabbix_2026-10-07_000000.sql.gz").write_bytes(b"x")
    (instancia / "backups" / "config_2026-10-07_000000.tar.gz.gpg").write_bytes(b"x")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    registro = tmp_path / "rclone.log"
    _rclone_falso(bin_dir, registro, falha_em)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "PROJECT_ROOT": str(instancia),
        "RCLONE_REMOTE": "remoto:Backups/teste",
        **(extra_env or {}),
    }
    proc = subprocess.run(["bash", str(_SCRIPTS / "sync_cloud.sh")], env=env, capture_output=True, text=True)
    chamadas = registro.read_text().splitlines() if registro.exists() else []
    return proc.returncode, chamadas


def test_sync_envia_dumps_e_pacote_criptografado_e_aplica_retencao_de_30_dias(tmp_path: Path) -> None:
    rc, chamadas = _rodar_sync(tmp_path)

    assert rc == 0
    assert chamadas[0].startswith("copy ")
    assert "--include *.gz --include *.gpg" in chamadas[0]
    assert chamadas[1].startswith("delete remoto:Backups/teste --min-age 30d")
    assert "--include *.gz --include *.gpg" in chamadas[1]


def test_sync_nao_apaga_nada_no_remoto_quando_o_upload_falha(tmp_path: Path) -> None:
    rc, chamadas = _rodar_sync(tmp_path, falha_em="copy")

    assert rc == 1
    assert not any(c.startswith("delete") for c in chamadas)


def test_sync_sem_retencao_quando_desligada(tmp_path: Path) -> None:
    rc, chamadas = _rodar_sync(tmp_path, {"REMOTE_RETENTION_DAYS": "0"})

    assert rc == 0
    assert [c.split()[0] for c in chamadas] == ["copy"]


def _env_config(tmp_path: Path, senha: str | None) -> dict[str, str]:
    alvo = tmp_path / "srv"
    alvo.mkdir()
    (alvo / ".env").write_text("SEGREDO=1\n")
    omni = tmp_path / "omni"
    omni.mkdir()
    (omni / "omniroute_20261007_0320.sqlite.gz").write_bytes(b"db")
    chave = tmp_path / "chave"
    if senha is not None:
        chave.write_text(senha)
        chave.chmod(0o600)
    gnupg = tmp_path / "gnupg"
    gnupg.mkdir(mode=0o700)
    return {
        **os.environ,
        "GNUPGHOME": str(gnupg),
        "BACKUP_PASSPHRASE_FILE": str(chave),
        "CONFIG_PATHS": f"{alvo / '.env'}\n{tmp_path / 'nao-existe'}",
        "OMNIROUTE_BACKUP_DIR": str(omni),
    }


def test_config_sem_senha_nao_gera_nada(tmp_path: Path) -> None:
    saida = tmp_path / "config.tar.gz.gpg"

    proc = subprocess.run(
        ["bash", str(_SCRIPTS / "backup_config.sh"), str(saida)],
        env=_env_config(tmp_path, senha=None),
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 2
    assert not saida.exists()
    assert "NÃO gerado" in proc.stdout


@pytest.mark.skipif(shutil.which("gpg") is None, reason="gpg não instalado")
def test_config_criptografa_e_abre_com_a_senha(tmp_path: Path) -> None:
    saida = tmp_path / "config.tar.gz.gpg"
    env = _env_config(tmp_path, senha="senha-de-teste")

    proc = subprocess.run(
        ["bash", str(_SCRIPTS / "backup_config.sh"), str(saida)], env=env, capture_output=True, text=True
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "pulando" in proc.stdout  # caminho inexistente é avisado, não derruba
    assert not saida.read_bytes().startswith(b"\x1f\x8b")  # não é um .tar.gz em claro
    claro = subprocess.run(
        [
            "gpg",
            "--batch",
            "--quiet",
            "--pinentry-mode",
            "loopback",
            "--passphrase",
            "senha-de-teste",
            "-d",
            str(saida),
        ],
        env=env,
        capture_output=True,
        check=True,
    ).stdout
    with tarfile.open(fileobj=BytesIO(claro), mode="r:gz") as tar:
        nomes = tar.getnames()
    assert any(n.endswith("srv/.env") for n in nomes)
    assert any(n.endswith("omniroute_20261007_0320.sqlite.gz") for n in nomes)
    assert "extra/crontab.txt" in nomes
