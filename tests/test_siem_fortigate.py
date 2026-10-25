"""Agente SIEM: normalização, leitura paginada, resumo e regras dos logs do FortiGate."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from itgov.services import siem_fortigate as sf

_FW = {"nome": "Sede", "url": "https://fw.test", "token": "t", "sha256": ""}
_BASE_NS = 1_792_962_000_000_000_000  # 2026-10-25 em nanossegundos


def _raw(i: int, **campos: Any) -> dict[str, Any]:
    return {
        "eventtime": _BASE_NS + i * 1_000_000_000,
        "logid": "0101039426",
        "type": "event",
        "subtype": "vpn",
        "level": "alert",
        "logdesc": "SSL VPN login fail",
        "remip": f"77.91.71.{i % 7}",
        "user": f"user{i % 40}",
        "action": "ssl-login-fail",
        "reason": "sslvpn_login_permission_denied",
        "msg": "SSL user failed to logged in",
    } | campos


def _ev(i: int, **campos: Any) -> sf.EventoSiem:
    ev = sf.normalizar("Sede", _raw(i, **campos))
    assert ev is not None
    return ev


# ── normalizar / relevante ───────────────────────────────────────────────────


def test_normalizar_converte_eventtime_em_ns_e_campos() -> None:
    ev = _ev(0)
    assert ev.quando.year == 2026 and ev.quando.tzinfo is not None
    assert (ev.subtipo, ev.nivel, ev.descricao) == ("vpn", "alert", "SSL VPN login fail")
    assert (ev.origem_ip, ev.usuario, ev.motivo) == ("77.91.71.0", "user0", "sslvpn_login_permission_denied")
    assert len(ev.chave) == 32


def test_normalizar_aceita_eventtime_em_segundos_e_recusa_sem_eventtime() -> None:
    ev = sf.normalizar("Sede", _raw(0, eventtime=1_792_962_000))
    assert ev is not None and ev.quando.year == 2026
    assert sf.normalizar("Sede", {"logdesc": "x"}) is None
    assert sf.normalizar("Sede", {"eventtime": "abc"}) is None


def test_chave_estavel_e_diferente_por_evento() -> None:
    assert _ev(1).chave == _ev(1).chave
    assert _ev(1).chave != _ev(2).chave


def test_normalizar_webfilter_junta_site_e_categoria() -> None:
    ev = _ev(0, type="utm", subtype="webfilter", hostname="mau.exemplo", catdesc="Malicious Websites", action="blocked")
    assert ev.mensagem == "mau.exemplo — Malicious Websites"


def test_textos_longos_sao_cortados() -> None:
    ev = _ev(0, user="x" * 500, msg="y" * 900)
    assert len(ev.usuario) == 120 and len(ev.mensagem) == 300


@pytest.mark.parametrize(
    ("campos", "esperado"),
    [
        ({"level": "alert"}, True),
        ({"level": "notice", "logdesc": "SDWAN status"}, False),
        ({"level": "notice", "logdesc": "Admin login successful"}, True),
        ({"level": "information", "logdesc": "Object attribute configured"}, True),
        ({"level": "notice", "logdesc": "SSL VPN tunnel up"}, True),
        ({"level": "notice", "type": "utm", "subtype": "webfilter", "logdesc": "URL", "action": "blocked"}, True),
        ({"level": "notice", "type": "utm", "subtype": "webfilter", "logdesc": "URL", "action": "passthrough"}, False),
    ],
)
def test_relevante(campos: dict[str, Any], esperado: bool) -> None:
    assert sf.relevante(_ev(0, **campos)) is esperado


# ── ler_novos ────────────────────────────────────────────────────────────────


def test_ler_novos_pagina_ate_alcancar_o_ultimo_guardado() -> None:
    # Memória devolve do mais novo para o mais antigo: 2500 eventos, i=2499 é o mais novo
    todos = [_raw(i) for i in range(2499, -1, -1)]
    paginas = [todos[0:1000], todos[1000:2000], todos[2000:2500]]
    desde = _raw(1200)["eventtime"]
    with patch.object(sf.fortigate_api, "_get", side_effect=paginas) as get:
        novos = sf.ler_novos(_FW, "event/system", desde)
    assert get.call_count == 2  # a 2ª página já passa do último guardado
    assert "start=1000" in get.call_args_list[1].args[1]
    assert len(novos) == 2499 - 1200
    assert min(e.eventtime for e in novos) > desde


def test_ler_novos_para_em_pagina_incompleta_e_vazia() -> None:
    with patch.object(sf.fortigate_api, "_get", side_effect=[[_raw(i) for i in range(5)]]) as get:
        assert len(sf.ler_novos(_FW, "ips", 0)) == 5
    assert get.call_count == 1
    with patch.object(sf.fortigate_api, "_get", return_value=[]):
        assert sf.ler_novos(_FW, "ips", 0) == []


def test_ler_novos_respeita_limite_de_paginas() -> None:
    pagina = [_raw(i) for i in range(1000)]
    with patch.object(sf.fortigate_api, "_get", return_value=pagina) as get:
        sf.ler_novos(_FW, "event/system", 0)
    assert get.call_count == sf.MAX_PAGINAS


# ── resumir / regras ─────────────────────────────────────────────────────────


def test_resumir_conta_falhas_de_vpn_por_ip_e_usuario() -> None:
    eventos = [_ev(i) for i in range(100)]
    r = sf.resumir(eventos)["Sede"]
    falhas = r["vpn_ssl_falhas"]
    assert falhas["total"] == 100
    assert falhas["ips_distintos"] == 7
    assert falhas["usuarios_distintos"] == 40
    assert falhas["motivos"] == [["sslvpn_login_permission_denied", 100]]


def test_regra_forca_bruta_distribuida() -> None:
    eventos = [_ev(i) for i in range(100)]
    achados = sf.achados_por_regra(sf.resumir(eventos), minutos=15)
    forca = next(a for a in achados if a.assinatura == "vpn-ssl-forca-bruta")
    assert forca.severidade == "alta" and forca.fortigate == "Sede"
    assert "400/h" in forca.titulo  # 100 em 15 min
    assert any("77.91.71." in e for e in forca.evidencias)


def test_regra_um_ip_insistente_sem_ser_distribuido() -> None:
    eventos = [_ev(i, remip="203.0.113.9", user="joao") for i in range(25)]
    achados = sf.achados_por_regra(sf.resumir(eventos), minutos=15)
    assert [a.assinatura for a in achados] == ["vpn-ssl-falhas-ip-203.0.113.9"]
    assert achados[0].severidade == "media"


def test_regra_admin_externo_e_critico_e_interno_nao() -> None:
    externo = _ev(1, level="information", logdesc="Admin login successful", remip="45.33.32.156", user="admin")
    interno = _ev(2, level="information", logdesc="Admin login successful", remip="172.29.3.10", user="admin")
    achados = sf.achados_por_regra(sf.resumir([externo, interno]), minutos=15)
    assert [a.assinatura for a in achados] == ["admin-login-externo"]
    assert achados[0].severidade == "critica"
    assert achados[0].evidencias == ["admin de 45.33.32.156"]


def test_regra_admin_falhando_ipsec_fortiguard_config_e_ameaca() -> None:
    eventos = (
        [_ev(i, logdesc="Admin login failed", user="admin", remip="10.0.0.5") for i in range(6)]
        + [_ev(100 + i, level="error", logdesc="IPsec phase 1 error", remip="189.32.31.166") for i in range(60)]
        + [
            _ev(
                200 + i, type="utm", subtype="webfilter", level="error", logdesc="ftgd_err", msg="A rating error occurs"
            )
            for i in range(25)
        ]
        + [_ev(300, level="information", logdesc="Object attribute configured", user="roberto", msg="edit policy 5")]
        + [_ev(301, type="utm", subtype="ips", level="alert", logdesc="Attack", attack="Exploit.X")]
    )
    sev = {a.assinatura: a.severidade for a in sf.achados_por_regra(sf.resumir(eventos), minutos=15)}
    assert sev == {
        "admin-login-falha": "alta",
        "ipsec-falhando-189.32.31.166": "baixa",
        "fortiguard-sem-resposta": "media",
        "config-alterada": "info",
        "ameaca-detectada": "alta",
    }


def test_regras_ordenam_do_mais_grave() -> None:
    eventos = [_ev(i) for i in range(100)] + [
        _ev(500, level="information", logdesc="Admin login successful", remip="45.33.32.156", user="admin")
    ]
    sevs = [a.severidade for a in sf.achados_por_regra(sf.resumir(eventos), minutos=15)]
    assert sevs == sorted(sevs, key=sf.SEVERIDADES.index)
    assert sevs[0] == "critica"


def test_sem_eventos_sem_achados() -> None:
    assert sf.resumir([]) == {}
    assert sf.achados_por_regra({}, minutos=15) == []
