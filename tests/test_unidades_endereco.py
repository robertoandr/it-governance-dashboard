"""Unidades: endereço, CEP, cidade, UF e logo (V2.0)."""

from __future__ import annotations

import io
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, inspect, text

from app.extensions import db
from app.models.unidade import (
    LOGO_MAX_BYTES,
    Unidade,
    garantir_colunas,
    normalizar_cep,
    normalizar_cnpj,
    normalizar_uf,
    tipo_logo,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 32
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


@pytest.fixture
def operador_client(factory_app) -> Iterator:
    from app.models.user import User

    with factory_app.app_context():
        user = User.query.filter_by(email="pytest-operador@test.local").first()
        if user is None:
            user = User(name="Pytest Operador", email="pytest-operador@test.local", role="operador")
            user.set_password("pytest-only-not-real")
            db.session.add(user)
            db.session.commit()
        uid = user.id
    with factory_app.test_client() as c:
        with c.session_transaction() as sess:
            sess["_user_id"] = str(uid)
            sess["_fresh"] = True
        yield c


def _nome(base: str) -> str:
    # data/app.db persiste entre execuções locais: nomes únicos evitam colisão.
    return f"{base} {uuid.uuid4().hex[:8]}"


def _criar(client, nome: str, **campos: object):
    return client.post(
        "/gov/unidades/nova",
        data={"nome": nome, **campos},
        content_type="multipart/form-data",
    )


# ── Validações ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("bruto", "esperado"),
    [("89201-100", "89201-100"), ("89201100", "89201-100"), (" 89.201-100 ", "89201-100"), ("", ""), ("  ", "")],
)
def test_normalizar_cep(bruto: str, esperado: str) -> None:
    assert normalizar_cep(bruto) == esperado


@pytest.mark.parametrize("bruto", ["8920110", "892011000", "89201-10A"])
def test_normalizar_cep_invalido(bruto: str) -> None:
    with pytest.raises(ValueError, match="CEP inválido"):
        normalizar_cep(bruto)


def test_normalizar_uf() -> None:
    assert normalizar_uf(" sc ") == "SC"
    assert normalizar_uf("") == ""
    with pytest.raises(ValueError, match="UF inválida"):
        normalizar_uf("XX")


@pytest.mark.parametrize(("dados", "mime"), [(PNG, "image/png"), (JPEG, "image/jpeg"), (WEBP, "image/webp")])
def test_tipo_logo_aceita_raster(dados: bytes, mime: str) -> None:
    assert tipo_logo(dados) == mime


@pytest.mark.parametrize(
    ("dados", "erro"),
    [
        (SVG, "PNG, JPEG ou WebP"),
        (b"GIF89a" + b"\x00" * 10, "PNG, JPEG ou WebP"),
        (b"", "vazio"),
        (PNG + b"\x00" * LOGO_MAX_BYTES, "512 KB"),
    ],
)
def test_tipo_logo_recusa(dados: bytes, erro: str) -> None:
    with pytest.raises(ValueError, match=erro):
        tipo_logo(dados)


def test_local_cidade_uf() -> None:
    assert Unidade(nome="x", cidade="Joinville", uf="SC").local == "Joinville/SC"
    assert Unidade(nome="x", cidade="Joinville", uf="").local == "Joinville"
    assert Unidade(nome="x", cidade="", uf="SC").local == "SC"


# ── Migração ──────────────────────────────────────────────────────────────────


def test_garantir_colunas_adiciona_faltantes_e_e_idempotente(tmp_path) -> None:
    # Tabela como estava em produção antes destas colunas (banco isolado:
    # mexer no schema do banco compartilhado afetaria os outros testes).
    engine = create_engine(f"sqlite:///{tmp_path / 'antigo.db'}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE unidades (id INTEGER PRIMARY KEY, nome VARCHAR(120) NOT NULL, "
                "parent_id INTEGER, faixas_ip TEXT NOT NULL DEFAULT '', ativo BOOLEAN NOT NULL DEFAULT 1, "
                "created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)"
            )
        )
        conn.execute(text("INSERT INTO unidades (nome, created_at, updated_at) VALUES ('Shopping', 0, 0)"))

    garantir_colunas(engine)
    garantir_colunas(engine)  # segunda chamada não pode falhar

    novas = {"cnpj", "endereco", "cep", "cidade", "uf", "logo", "logo_mime"}
    assert novas <= {c["name"] for c in inspect(engine).get_columns("unidades")}
    with engine.connect() as conn:
        # Linhas antigas recebem string vazia, não NULL.
        assert conn.execute(text("SELECT endereco, cep, cidade, uf, logo_mime FROM unidades")).one() == (
            "",
            "",
            "",
            "",
            None,
        )
    engine.dispose()


# ── Formulário e listagem ─────────────────────────────────────────────────────


def test_criar_com_endereco_e_logo(authed_client, factory_app) -> None:
    nome = _nome("Loja Centro")
    resp = _criar(
        authed_client,
        nome,
        endereco="Rua XV de Novembro, 100",
        cep="89201100",
        cidade="Joinville",
        uf="sc",
        logo=(io.BytesIO(PNG), "logo.png"),
    )
    assert resp.status_code == 302
    with factory_app.app_context():
        u = Unidade.query.filter_by(nome=nome).one()
        assert (u.endereco, u.cep, u.cidade, u.uf) == ("Rua XV de Novembro, 100", "89201-100", "Joinville", "SC")
        assert u.logo == PNG and u.logo_mime == "image/png"
        uid = u.id

    logo = authed_client.get(f"/gov/unidades/{uid}/logo")
    assert logo.status_code == 200
    assert logo.data == PNG
    assert logo.mimetype == "image/png"
    assert logo.headers["X-Content-Type-Options"] == "nosniff"

    lista = authed_client.get("/gov/unidades").get_data(as_text=True)
    assert "Rua XV de Novembro, 100" in lista
    assert "Joinville/SC · 89201-100" in lista
    assert f"/gov/unidades/{uid}/logo?v=" in lista


def test_editar_sem_arquivo_mantem_logo_e_remover_apaga(authed_client, factory_app) -> None:
    nome = _nome("Loja Logo")
    _criar(authed_client, nome, logo=(io.BytesIO(JPEG), "logo.jpg"))
    with factory_app.app_context():
        uid = Unidade.query.filter_by(nome=nome).one().id

    authed_client.post(
        f"/gov/unidades/{uid}/editar",
        data={"nome": nome, "cidade": "Curitiba", "uf": "PR"},
        content_type="multipart/form-data",
    )
    with factory_app.app_context():
        u = db.session.get(Unidade, uid)
        assert u.logo == JPEG and u.cidade == "Curitiba"

    form = authed_client.get(f"/gov/unidades/{uid}/editar").get_data(as_text=True)
    assert 'name="remover_logo"' in form
    assert 'enctype="multipart/form-data"' in form

    authed_client.post(
        f"/gov/unidades/{uid}/editar",
        data={"nome": nome, "remover_logo": "1"},
        content_type="multipart/form-data",
    )
    with factory_app.app_context():
        u = db.session.get(Unidade, uid)
        assert u.logo is None and u.logo_mime is None
    assert authed_client.get(f"/gov/unidades/{uid}/logo").status_code == 404


@pytest.mark.parametrize(
    ("campos", "erro"),
    [
        ({"cep": "123"}, "CEP inválido"),
        ({"uf": "XX"}, "UF inválida"),
        ({"logo": (io.BytesIO(SVG), "logo.svg")}, "PNG, JPEG ou WebP"),
    ],
)
def test_criar_com_dado_invalido_nao_salva(authed_client, factory_app, campos: dict, erro: str) -> None:
    nome = _nome("Loja Invalida")
    resp = _criar(authed_client, nome, cidade="Joinville", **campos)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert erro in html
    assert 'value="Joinville"' in html  # o que foi digitado continua no formulário
    with factory_app.app_context():
        assert Unidade.query.filter_by(nome=nome).first() is None


def test_logo_inexistente_404(authed_client, factory_app) -> None:
    with factory_app.app_context():
        uid = Unidade.query.filter_by(nome="Obras").one().id
    assert authed_client.get(f"/gov/unidades/{uid}/logo").status_code == 404
    assert authed_client.get("/gov/unidades/999999/logo").status_code == 404


def _unidade_com_logo(factory_app, nome: str) -> int:
    # Direto no banco: dois test clients abertos juntos compartilham o usuário logado.
    with factory_app.app_context():
        u = Unidade(nome=nome, logo=PNG, logo_mime="image/png")
        db.session.add(u)
        db.session.commit()
        return u.id


def test_operador_ve_logo_mas_nao_edita(operador_client, factory_app) -> None:
    nome = _nome("Loja Operador")
    uid = _unidade_com_logo(factory_app, nome)
    assert operador_client.get(f"/gov/unidades/{uid}/logo").status_code == 200
    assert operador_client.get(f"/gov/unidades/{uid}/editar").status_code == 403


def test_logo_exige_login(factory_client, factory_app) -> None:
    nome = _nome("Loja Anonimo")
    uid = _unidade_com_logo(factory_app, nome)
    assert factory_client.get(f"/gov/unidades/{uid}/logo").status_code == 302


# ── CNPJ ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("bruto", "esperado"),
    [("11222333000181", "11.222.333/0001-81"), (" 11.222.333/0001-81 ", "11.222.333/0001-81"), ("", "")],
)
def test_normalizar_cnpj(bruto: str, esperado: str) -> None:
    assert normalizar_cnpj(bruto) == esperado


@pytest.mark.parametrize("bruto", ["11222333000182", "1122233300018", "00000000000000", "abc"])
def test_normalizar_cnpj_invalido(bruto: str) -> None:
    with pytest.raises(ValueError, match="CNPJ inválido"):
        normalizar_cnpj(bruto)


def test_cnpj_salvo_e_listado_sem_coluna_de_gravadores(authed_client, factory_app) -> None:
    nome = _nome("Loja CNPJ")
    assert _criar(authed_client, nome, cnpj="11222333000181").status_code == 302
    with factory_app.app_context():
        assert Unidade.query.filter_by(nome=nome).one().cnpj == "11.222.333/0001-81"
    lista = authed_client.get("/gov/unidades").get_data(as_text=True)
    assert "CNPJ 11.222.333/0001-81" in lista
    assert "Gravadores CFTV" not in lista


def test_cnpj_invalido_nao_salva(authed_client, factory_app) -> None:
    nome = _nome("Loja CNPJ ruim")
    html = _criar(authed_client, nome, cnpj="11222333000182").get_data(as_text=True)
    assert "CNPJ inválido" in html
    with factory_app.app_context():
        assert Unidade.query.filter_by(nome=nome).first() is None
