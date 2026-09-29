"""Fixtures do teste de ponta a ponta do módulo Tarefas (Playwright).

Sobe o app de verdade (``flask run`` numa porta livre) contra o
``data/app.db`` do checkout, cria dois usuários ``e2e-tarefas-*`` e apaga,
antes e depois, só o que eles criaram — como os testes de ``tests/tarefas``.

Fica fora de ``tests/`` (``testpaths`` do pyproject) para não rodar no job
de testes unitários, que não tem navegador. Rodar com::

    pytest e2e -o addopts="" -p no:cacheprovider
"""

from __future__ import annotations

import os
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

from e2e.usuarios import Usuario

RAIZ = Path(__file__).resolve().parent.parent
PREFIXO_EMAIL = "e2e-tarefas-"
# Saída do app durante o teste (ignorada pelo git; o CI publica como artefato).
LOG_APP = RAIZ / "e2e" / "app-e2e.log"

# Valores falsos só para o app subir: o módulo Tarefas depende apenas do
# app.db. No CI os mesmos nomes vêm do ``env`` do job.
_AMBIENTE_FALSO = {
    "INFLUX_URL": "http://localhost:8086",
    "INFLUX_TOKEN": "e2e-fake-token-not-real",
    "INFLUX_ORG": "e2e-org",
    "INFLUX_BUCKET": "e2e-bucket",
    "INFLUX__ENABLED": "false",
    "ZABBIX_URL": "http://localhost:8080/api_jsonrpc.php",
    "ZABBIX_USER": "e2e-user",
    "ZABBIX_PASSWORD": "e2e-fake-password-not-real",
    "ZABBIX_FRONT_URL": "http://localhost:8080",
    "ZENDESK_SUBDOMAIN": "e2e",
    "ZENDESK_EMAIL": "e2e@example.com",
    "ZENDESK_API_TOKEN": "e2e-fake-token-not-real",
    "SECRET_KEY": "e2e-secret-key-not-real",
    "OPS_PIN": "0000",
    "FLASK_ENV": "development",
}
for _nome, _valor in _AMBIENTE_FALSO.items():
    os.environ.setdefault(_nome, _valor)


def _porta_livre() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _esperar_no_ar(url: str, processo: subprocess.Popen[bytes], limite_s: float = 30.0) -> None:
    fim = time.monotonic() + limite_s
    while time.monotonic() < fim:
        if processo.poll() is not None:
            raise RuntimeError(f"O app terminou ao subir (código {processo.returncode}).")
        try:
            with urllib.request.urlopen(url, timeout=2) as resposta:  # nosec B310 — URL local montada aqui
                if resposta.status == 200:
                    return
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            pass
        time.sleep(0.3)
    raise RuntimeError(f"O app não respondeu em {limite_s:.0f} s: {url}")


@pytest.fixture(scope="session")
def app_flask():  # type: ignore[no-untyped-def]  # Flask importado aqui: depende do ambiente acima
    """Instância do app só para preparar dados no ``data/app.db``."""
    from app import create_app
    from app.config import get_settings

    return create_app(get_settings())


@pytest.fixture(scope="session")
def usuarios(app_flask) -> Iterator[dict[str, Usuario]]:  # type: ignore[no-untyped-def]
    """Usuários ``admin`` e ``visualizador`` com senha aleatória por execução."""
    from app.extensions import db
    from app.models.user import User
    from tests.tarefas.limpeza import limpar_dados_de

    criados: dict[str, Usuario] = {}
    with app_flask.app_context():
        limpar_dados_de(PREFIXO_EMAIL)
        for perfil in ("admin", "visualizador"):
            email = f"{PREFIXO_EMAIL}{perfil}@test.local"
            senha = secrets.token_urlsafe(16)
            user = User.query.filter_by(email=email).first()
            if user is None:
                user = User(name=f"E2E {perfil}", email=email, role=perfil)
                db.session.add(user)
            user.is_active = True
            user.set_password(senha)
            criados[perfil] = Usuario(email=email, senha=senha)
        db.session.commit()
    yield criados
    with app_flask.app_context():
        db.session.rollback()
        limpar_dados_de(PREFIXO_EMAIL)


@pytest.fixture(scope="session")
def url_base(usuarios: dict[str, Usuario]) -> Iterator[str]:
    """Sobe o app com ``flask run`` e devolve a URL base (sem barra no fim)."""
    porta = _porta_livre()
    with LOG_APP.open("wb") as log:
        processo = subprocess.Popen(  # noqa: S603 — comando fixo, sem entrada externa
            [sys.executable, "-m", "flask", "--app", "wsgi:application", "run", "--port", str(porta), "--no-reload"],
            cwd=RAIZ,
            env=os.environ.copy(),
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        url = f"http://127.0.0.1:{porta}"
        try:
            _esperar_no_ar(f"{url}/gov/login", processo)
            yield url
        finally:
            processo.terminate()
            try:
                processo.wait(timeout=10)
            except subprocess.TimeoutExpired:
                processo.kill()
