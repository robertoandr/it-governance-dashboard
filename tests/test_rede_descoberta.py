"""Página /gov/rede (revisão de descobertos), cadastro como ativo e /gov/ativos-rede."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import itgov.db.session as db_session
from app.extensions import db
from app.models.unidade import Unidade
from itgov.api.v1 import rede_monitoring
from itgov.api.v1.rede_monitoring import SEM_UNIDADE, montar_descobertos, unidade_do_ip
from itgov.models.db.ativo import AtivoDB
from itgov.models.db.base import Base


def _h(ip: str, tipo: str = "outro", **kw: str) -> dict:
    base = {
        "ip": ip,
        "category": "Host Generico",
        "hostname": "",
        "vendor": "",
        "os_guess": "",
        "open_ports": 0,
        "has_agent": False,
        "has_snmp": False,
        "has_ssh": False,
        "mac": "",
        "tipo_sugerido": tipo,
        "motivo": "teste",
        "portas": "",
        "scan_time": "",
    }
    base.update(kw)
    return base


_HOSTS = [
    _h(
        "172.29.1.20",
        "impressora",
        hostname="impressora-rh",
        vendor="Brother",
        mac="00:80:77:11:22:33",
        portas="631,9100",
    ),
    _h("172.29.2.50", "vm", hostname="srv-app", mac="00:50:56:AA:BB:CC", motivo="MAC VMware"),
    _h("10.41.5.9", "camera", portas="554"),
    _h("192.168.99.1", "outro"),
]
_DADOS = {
    "enabled": True,
    "influx": {"has_data": True, "last_scan": "2026-09-25 10:00:00", "total": 4, "hosts": _HOSTS, "history": []},
    "drules": [],
    "active_drules": [],
    "scan_ranges": [],
}


@pytest.fixture
def ativos_db() -> Iterator[None]:
    """Banco de ativos em memória no lugar do engine da aplicação."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    anterior = db_session._engine
    db_session._engine = engine
    yield
    db_session._engine = anterior
    engine.dispose()


def _ativos() -> list[AtivoDB]:
    with Session(db_session.get_engine()) as s:
        return list(s.execute(select(AtivoDB)).scalars())


@pytest.fixture
def faixas(factory_app) -> Iterator[dict[str, int]]:
    """Sede Centro = 172.29.0.0/22, Shopping = 10.41.0.0/16 (revertido ao final)."""
    with factory_app.app_context():
        ids = {u.nome: u.id for u in Unidade.query.filter_by(parent_id=None)}
        db.session.get(Unidade, ids["Sede Centro"]).faixas_ip = "172.29.0.0/22"
        db.session.get(Unidade, ids["Shopping"]).faixas_ip = "10.41.0.0/16"
        db.session.commit()
    yield ids
    with factory_app.app_context():
        for nome in ("Sede Centro", "Shopping"):
            db.session.get(Unidade, ids[nome]).faixas_ip = ""
        db.session.commit()


@pytest.fixture
def rede(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("ZABBIX_URL", "https://zabbix.test")
    with patch.object(rede_monitoring, "get_cached_rede_summary", return_value=_DADOS):
        yield


@pytest.fixture
def operador_client(factory_app) -> Iterator:
    from app.models.user import User

    with factory_app.app_context():
        user = User.query.filter_by(email="pytest-rede-operador@test.local").first()
        if user is None:
            user = User(name="Operador Rede", email="pytest-rede-operador@test.local", role="operador")
            user.set_password("pytest-only-not-real")
            db.session.add(user)
            db.session.commit()
        uid = user.id
    with factory_app.test_client() as c:
        with c.session_transaction() as sess:
            sess["_user_id"] = str(uid)
            sess["_fresh"] = True
        yield c


# ── Funções puras ─────────────────────────────────────────────────────────────


def test_unidade_do_ip_prefere_faixa_mais_especifica() -> None:
    faixas = [(1, "10.41.0.0/16"), (2, "10.41.5.0/24"), (3, "172.29.0.0/22")]
    assert unidade_do_ip("10.41.5.9", faixas) == 2
    assert unidade_do_ip("10.41.6.1", faixas) == 1
    assert unidade_do_ip("192.168.0.1", faixas) is None
    assert unidade_do_ip("nao-e-ip", faixas) is None
    assert unidade_do_ip("10.41.5.9", [(9, "lixo")]) is None


def test_montar_descobertos_cruza_unidade_e_ativo() -> None:
    faixas = [(1, "172.29.0.0/22"), (2, "10.41.0.0/16")]
    ativos = {"172.29.2.50": {"id": "x", "nome": "srv-app", "tipo": "vm"}}
    r = montar_descobertos(_HOSTS, faixas, ativos, {1: "Sede Centro", 2: "Shopping"})

    assert (r["total"], r["novos"], r["cadastrados"]) == (4, 3, 1)
    # Novos primeiro; cadastrado por último
    assert r["hosts"][-1]["ip"] == "172.29.2.50" and r["hosts"][-1]["ativo"]["nome"] == "srv-app"
    por_ip = {h["ip"]: h for h in r["hosts"]}
    assert por_ip["172.29.1.20"]["unidade"] == "Sede Centro"
    assert por_ip["10.41.5.9"]["unidade"] == "Shopping"
    assert por_ip["192.168.99.1"]["unidade_id"] is None


@pytest.mark.parametrize(
    ("filtro_unidade", "filtro_status", "ips"),
    [
        ({1}, "", {"172.29.1.20", "172.29.2.50"}),
        (SEM_UNIDADE, "", {"192.168.99.1"}),
        (None, "novos", {"172.29.1.20", "10.41.5.9", "192.168.99.1"}),
        (None, "cadastrados", {"172.29.2.50"}),
        ({1}, "novos", {"172.29.1.20"}),
    ],
)
def test_montar_descobertos_filtros(filtro_unidade, filtro_status: str, ips: set[str]) -> None:
    r = montar_descobertos(
        _HOSTS,
        [(1, "172.29.0.0/22"), (2, "10.41.0.0/16")],
        {"172.29.2.50": {"id": "x", "nome": "srv-app", "tipo": "vm"}},
        {1: "Sede Centro", 2: "Shopping"},
        filtro_unidade=filtro_unidade,
        filtro_status=filtro_status,
    )
    assert {h["ip"] for h in r["hosts"]} == ips


# ── /gov/rede ─────────────────────────────────────────────────────────────────


def test_rede_page_mostra_fila_de_revisao(authed_client, rede, ativos_db, faixas) -> None:
    html = authed_client.get("/gov/rede").get_data(as_text=True)
    for trecho in (
        "Ativos descobertos",
        "4 novos",
        "impressora-rh",
        "Impressora",
        "MAC VMware",
        "Sede Centro",
        "Shopping",
    ):
        assert trecho in html, trecho
    assert "rede/cadastrar?ip=172.29.1.20" in html
    assert "Nenhuma unidade tem faixa de IP" not in html


def test_rede_page_avisa_quando_nao_ha_faixas(authed_client, rede, ativos_db) -> None:
    html = authed_client.get("/gov/rede").get_data(as_text=True)
    assert "Nenhuma unidade tem faixa de IP" in html


def test_rede_page_filtra_por_unidade(authed_client, rede, ativos_db, faixas) -> None:
    html = authed_client.get(f"/gov/rede?unidade={faixas['Shopping']}").get_data(as_text=True)
    assert "10.41.5.9" in html
    assert "impressora-rh" not in html


def test_rede_page_operador_nao_ve_botao_cadastrar(operador_client, rede, ativos_db) -> None:
    html = operador_client.get("/gov/rede").get_data(as_text=True)
    assert "impressora-rh" in html
    assert "rede/cadastrar" not in html


# ── Cadastro ──────────────────────────────────────────────────────────────────


def test_cadastrar_get_pre_preenche_tipo_e_unidade(authed_client, rede, ativos_db, faixas) -> None:
    html = authed_client.get("/gov/rede/cadastrar?ip=172.29.1.20").get_data(as_text=True)
    assert 'value="impressora-rh"' in html
    assert '<option value="impressora" selected' in html
    assert f'<option value="{faixas["Sede Centro"]}" selected' in html


def test_cadastrar_nome_padrao_sem_hostname(authed_client, rede, ativos_db) -> None:
    html = authed_client.get("/gov/rede/cadastrar?ip=10.41.5.9").get_data(as_text=True)
    assert 'value="camera-10.41.5.9"' in html


def test_cadastrar_post_cria_ativo_com_metadata(authed_client, rede, ativos_db, faixas) -> None:
    resp = authed_client.post(
        "/gov/rede/cadastrar",
        data={
            "ip": "172.29.1.20",
            "nome": "Impressora RH",
            "tipo": "impressora",
            "unidade_id": str(faixas["Sede Centro"]),
            "criticidade": "baixa",
            "ambiente": "prod",
            "descricao": "2º andar",
        },
    )
    assert resp.status_code == 302
    (ativo,) = _ativos()
    assert (ativo.nome, ativo.tipo, ativo.criticidade, ativo.owner) == (
        "Impressora RH",
        "impressora",
        "baixa",
        "pytest-admin@test.local",
    )
    meta = ativo.metadata_
    assert meta["ip"] == "172.29.1.20" and meta["mac"] == "00:80:77:11:22:33"
    assert meta["unidade_id"] == faixas["Sede Centro"] and meta["unidade"] == "Sede Centro"
    assert meta["origem"] == "descoberta_rede" and meta["descricao"] == "2º andar"

    # Agora aparece como cadastrado na revisão, e um novo cadastro do mesmo IP é recusado
    html = authed_client.get("/gov/rede").get_data(as_text=True)
    assert "✓ Impressora RH" in html
    resp = authed_client.get("/gov/rede/cadastrar?ip=172.29.1.20")
    assert resp.status_code == 302 and "ativos-rede" in resp.headers["Location"]


def test_cadastrar_tipo_invalido_mostra_erro(authed_client, rede, ativos_db) -> None:
    resp = authed_client.post(
        "/gov/rede/cadastrar",
        data={"ip": "192.168.99.1", "nome": "coisa", "tipo": "torradeira", "criticidade": "media", "ambiente": "prod"},
    )
    assert resp.status_code == 200
    assert "Dados inválidos" in resp.get_data(as_text=True)
    assert _ativos() == []


def test_cadastrar_nome_duplicado_mostra_erro(authed_client, rede, ativos_db) -> None:
    dados = {"nome": "mesmo-nome", "tipo": "outro", "criticidade": "media", "ambiente": "prod"}
    authed_client.post("/gov/rede/cadastrar", data={**dados, "ip": "192.168.99.1"})
    resp = authed_client.post("/gov/rede/cadastrar", data={**dados, "ip": "10.41.5.9"})
    assert "Já existe um ativo mesmo-nome" in resp.get_data(as_text=True)
    assert len(_ativos()) == 1


def test_recadastrar_ativo_removido_reativa(authed_client, rede, ativos_db) -> None:
    dados = {"ip": "192.168.99.1", "nome": "gw-obra", "tipo": "outro", "criticidade": "media", "ambiente": "prod"}
    authed_client.post("/gov/rede/cadastrar", data=dados)
    (ativo,) = _ativos()
    authed_client.post(f"/gov/ativos-rede/{ativo.id}/remover")
    assert _ativos()[0].deleted_at is not None

    resp = authed_client.post("/gov/rede/cadastrar", data=dados)
    assert resp.status_code == 302
    (reativado,) = _ativos()
    assert reativado.id == ativo.id and reativado.deleted_at is None


def test_cadastrar_ip_desconhecido_404(authed_client, rede, ativos_db) -> None:
    assert authed_client.get("/gov/rede/cadastrar?ip=1.2.3.4").status_code == 404
    assert authed_client.get("/gov/rede/cadastrar").status_code == 404


def test_operador_nao_cadastra(operador_client, rede, ativos_db) -> None:
    assert operador_client.get("/gov/rede/cadastrar?ip=172.29.1.20").status_code == 403


# ── /gov/ativos-rede ──────────────────────────────────────────────────────────


def _cadastrar(client, ip: str, nome: str, tipo: str, unidade_id: int | None) -> None:
    client.post(
        "/gov/rede/cadastrar",
        data={
            "ip": ip,
            "nome": nome,
            "tipo": tipo,
            "unidade_id": str(unidade_id or ""),
            "criticidade": "media",
            "ambiente": "prod",
        },
    )


def test_ativos_rede_separa_por_unidade_e_filtra(authed_client, rede, ativos_db, faixas) -> None:
    _cadastrar(authed_client, "172.29.1.20", "Impressora RH", "impressora", faixas["Sede Centro"])
    _cadastrar(authed_client, "10.41.5.9", "Cam loja", "camera", faixas["Shopping"])
    _cadastrar(authed_client, "192.168.99.1", "Gateway obra", "outro", None)

    html = authed_client.get("/gov/ativos-rede").get_data(as_text=True)
    assert "3 ativos no inventário" in html
    # Seções por unidade, "Sem unidade" por último
    assert html.index("Sede Centro <span") < html.index("Shopping <span") < html.index("Sem unidade <span")

    html = authed_client.get(f"/gov/ativos-rede?unidade={faixas['Shopping']}").get_data(as_text=True)
    assert "Cam loja" in html and "Impressora RH" not in html

    html = authed_client.get("/gov/ativos-rede?tipo=impressora").get_data(as_text=True)
    assert "Impressora RH" in html and "Cam loja" not in html

    html = authed_client.get("/gov/ativos-rede?unidade=sem").get_data(as_text=True)
    assert "Gateway obra" in html and "Cam loja" not in html


def test_remover_ativo_inexistente_404(authed_client, ativos_db) -> None:
    assert authed_client.post("/gov/ativos-rede/nao-uuid/remover").status_code == 404
    assert authed_client.post("/gov/ativos-rede/00000000-0000-0000-0000-000000000000/remover").status_code == 404


def test_operador_ve_ativos_mas_nao_remove(operador_client, ativos_db) -> None:
    assert operador_client.get("/gov/ativos-rede").status_code == 200
    assert operador_client.post("/gov/ativos-rede/00000000-0000-0000-0000-000000000000/remover").status_code == 403
