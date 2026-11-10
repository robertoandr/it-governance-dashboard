"""Prévia dos modelos de redesign com dados ao vivo (/gov/previa)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from app.services import previa_modelos as pm

_AGORA = datetime(2026, 11, 10, 18, 0, tzinfo=UTC)
_T = int(_AGORA.timestamp())


def _prob(nome: str, sev: int, horas: float, host: str, ack: bool = False, clock: bool = True) -> dict:
    return {"name": nome, "severity": sev, "acknowledged": ack, "hosts": [host],
            "clock": _T - int(horas * 3600) if clock else 0}  # fmt: skip


_BRUTOS = [
    _prob("ICMP Ping: Unavailable by ICMP ping", 4, 0.5, "cam-1"),
    _prob("ICMP Ping: Unavailable by ICMP ping", 4, 200, "cam-2"),
    _prob("Gravador sem SNMP", 3, 30, "dvr-1"),
    _prob("Pouco disco", 2, 2, "srv-1", ack=True),
    _prob("Sem horário", 5, 0, "srv-2", clock=False),
]


@pytest.mark.parametrize(("sev", "esperado"), [(5, "alta"), (4, "alta"), (3, "media"), (2, "atencao"), (0, "atencao")])
def test_gravidade(sev: int, esperado: str) -> None:
    assert pm.gravidade(sev) == esperado


@pytest.mark.parametrize(
    ("segundos", "texto"),
    [(30, "1 min"), (95, "1 min"), (7200, "2 h"), (129600, "1 d 12 h"), (86400, "1 d"), (8 * 86400 + 3600, "8 d")],
)
def test_idade_texto(segundos: int, texto: str) -> None:
    assert pm.idade_texto(segundos) == texto


def test_montar_problemas_ordena_e_separa_por_idade() -> None:
    problemas, faixas = pm.montar_problemas(_BRUTOS, _AGORA)
    # Alta primeiro e, dentro dela, os mais novos; sem horário vai para o começo (idade 0)
    assert [p.host for p in problemas] == ["srv-2", "cam-1", "cam-2", "dvr-1", "srv-1"]
    assert problemas[1].idade == "30 min" and problemas[0].idade == "—"
    por_faixa = {f.rotulo: (f.alta, f.media, f.atencao) for f in faixas}
    # O problema sem horário não entra em nenhuma faixa
    assert por_faixa == {"< 1 h": (1, 0, 0), "1–6 h": (0, 0, 1), "6–24 h": (0, 0, 0), "1–3 d": (0, 1, 0),
                         "3–7 d": (0, 0, 0), "> 7 d": (1, 0, 0)}  # fmt: skip


def test_montar_pilares_calcula_a_perda() -> None:
    pilares = pm.montar_pilares(
        [
            {"label": "Riscos", "score": 41.68, "weight": 0.25},
            {"label": "Valor", "score": 75.23, "weight": 0.2},
            {"label": "Alinhamento", "score": 55.6, "weight": 0.15},
        ]
    )
    assert [(p.nome, p.perda, p.critico) for p in pilares] == [
        ("Riscos", 14.6, True),
        ("Alinhamento", 6.7, True),
        ("Valor", 5.0, False),
    ]


def test_montar_mapa_marca_a_pior_gravidade() -> None:
    problemas, _ = pm.montar_problemas(_BRUTOS, _AGORA)
    hosts = [
        {"name": "cam-1", "hostgroups": [{"name": "CFTV/Cameras"}]},
        {"name": "cam-9", "hostgroups": [{"name": "CFTV/Cameras"}]},
        {"name": "dvr-1", "hostgroups": [{"name": "CFTV/DVRs"}]},
        {"name": "srv-1", "hostgroups": [{"name": "Linux servers"}]},
    ]
    mapa = {g.tipo: g for g in pm.montar_mapa(hosts, problemas)}
    assert [h.estado for h in mapa["Câmeras"].hosts] == ["alta", ""]
    assert mapa["Câmeras"].com_problema == 1
    assert mapa["Gravadores (DVR/NVR)"].hosts[0].estado == "media"
    assert mapa["Servidores"].hosts[0].estado == "atencao"


def test_agrupar_incidentes_junta_o_mesmo_problema() -> None:
    problemas, _ = pm.montar_problemas(_BRUTOS, _AGORA)
    incidentes = pm.agrupar_incidentes(problemas)
    icmp = next(i for i in incidentes if i.nome.startswith("ICMP"))
    assert sorted(icmp.hosts) == ["cam-1", "cam-2"]
    assert (icmp.mais_novo, icmp.mais_antigo) == ("30 min", "8 d")
    assert incidentes[0].gravidade == "alta" and incidentes[-1].gravidade == "atencao"


def test_servicos_e_estado_geral() -> None:
    previa = pm.Previa(
        atualizado="10/11 15:00",
        hosts_total=400,
        hosts_fora=60,
        cameras_total=10,
        cameras_on=10,
        chamados_abertos=50,
        chamados_fora=49,
        fontes=[
            pm.Fonte(nome="Zabbix", estado="online", latencia_ms=300),
            pm.Fonte(nome="Zendesk", estado="offline", detalhe="HTTP 401"),
            pm.Fonte(nome="GitHub", estado="nao_configurada"),
        ],
    )
    previa.servicos = pm.montar_servicos(previa)
    estados = {s.nome: s.estado for s in previa.servicos}
    assert estados == {
        "Infraestrutura monitorada": "degradado",
        "CFTV · câmeras": "ok",
        "Service desk (SLA)": "fora",
        "Integração · Zabbix": "ok",
        "Integração · Zendesk": "fora",
    }
    assert previa.estado_geral == "fora"
    assert next(s for s in previa.servicos if s.nome == "Integração · Zendesk").resumo == "HTTP 401"


def test_fonte_que_falha_fica_indisponivel() -> None:
    previa = pm.Previa(atualizado="")

    def falha() -> dict:
        raise RuntimeError("fora")

    assert pm._ler("zabbix", previa, falha, {}) == {}
    assert previa.indisponivel == ["zabbix"]


# ── Páginas ──────────────────────────────────────────────────────────────────


@pytest.fixture
def previa() -> pm.Previa:
    problemas, faixas = pm.montar_problemas(_BRUTOS, _AGORA)
    p = pm.Previa(
        atualizado="10/11 15:00",
        score=56.8,
        status="Crítico",
        pilares=pm.montar_pilares([{"label": "Gestão de Riscos", "score": 41.68, "weight": 0.25}]),
        hosts_total=401,
        hosts_fora=62,
        disponibilidade=84.5,
        problemas=problemas,
        idades=faixas,
        mapa=pm.montar_mapa([{"name": "cam-1", "hostgroups": [{"name": "CFTV/Cameras"}]}], problemas),
        cameras_on=255,
        cameras_total=338,
        gravadores_on=17,
        gravadores_total=22,
        chamados_abertos=53,
        chamados_fora=52,
        prioridades=[pm.Prioridade(nome="Normal", abertos=40, fora=40)],
        antigos=[pm.Chamado(id=3560, assunto="Criação de grupo", prioridade="Normal", idade="341d", estourado=True)],
        fontes=[pm.Fonte(nome="Zabbix", estado="online", latencia_ms=300)],
        indisponivel=["mapa de hosts"],
    )
    for x in problemas:
        p.gravidades[x.gravidade] += 1
    p.servicos = pm.montar_servicos(p)
    return p


@pytest.mark.parametrize(
    ("modelo", "trechos"),
    [
        ("a", ["Mapa de hosts", "Câmeras", "ICMP Ping: Unavailable by ICMP ping", "62", "/401"]),
        ("b", ["Caixa de entrada", "Gravidade alta", "#3560", "Criação de grupo", "Ctrl K"]),
        ("c", ["Incidentes ativos", "× 2 hosts", "Service desk (SLA)", "Gestão de Riscos", "−14,6"]),
    ],
)
def test_pagina_do_modelo(authed_client, previa: pm.Previa, modelo: str, trechos: list[str]) -> None:
    with patch.object(pm, "obter", return_value=previa):
        resp = authed_client.get(f"/gov/previa/{modelo}")
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    for trecho in [*trechos, "Sem dados agora de: mapa de hosts", "css/previa.css"]:
        assert trecho in html, trecho


def test_indice_lista_os_modelos(authed_client) -> None:
    html = authed_client.get("/gov/previa").get_data(as_text=True)
    assert "Console de observabilidade" in html and "Caixa de triagem" in html and "Página de status" in html


def test_modelo_desconhecido_da_404(authed_client) -> None:
    assert authed_client.get("/gov/previa/z").status_code == 404


def test_previa_exige_login(factory_client) -> None:
    assert factory_client.get("/gov/previa/a").status_code in (302, 401)


def test_previa_so_para_admin(factory_app) -> None:
    from app.extensions import db
    from app.models.user import User

    with factory_app.app_context():
        user = User.query.filter_by(email="pytest-visual@test.local").first()
        if user is None:
            user = User(name="Pytest Visual", email="pytest-visual@test.local", role="visualizador")
            user.set_password("pytest-only-not-real")
            db.session.add(user)
            db.session.commit()
        uid = user.id
    with factory_app.test_client() as c:
        with c.session_transaction() as sess:
            sess["_user_id"] = str(uid)
            sess["_fresh"] = True
        assert c.get("/gov/previa/a").status_code in (302, 403)


def test_fontes_locais_existem() -> None:
    from pathlib import Path

    pasta = Path(__file__).resolve().parents[1] / "app" / "static" / "fonts" / "fira"
    for nome in ("fira-sans-400.woff2", "fira-sans-500.woff2", "fira-sans-600.woff2", "fira-code-var.woff2", "OFL.txt"):
        assert (pasta / nome).is_file(), nome
    assert (pasta / "fira-sans-400.woff2").read_bytes()[:4] == b"wOF2"
