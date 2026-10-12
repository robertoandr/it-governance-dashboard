"""Tests for infra-setup/whatsapp/*.js (texto do alerta, janela de horário e resumo da manhã).

O JS roda no Node com HttpRequest/Zabbix simulados, embrulhado numa função
como o Zabbix faz com webhooks e itens Script.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

_INFRA = Path(__file__).resolve().parent.parent.parent / "infra-setup"
_JS = _INFRA / "whatsapp"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node não instalado")

_HARNESS = r"""
const fs = require('fs');
const inp = JSON.parse(fs.readFileSync(0, 'utf8'));
const enviados = [], chamadas = [];
class HttpRequest {
    constructor() { this.headers = []; }
    addHeader(h) { this.headers.push(h); }
    post(url, body) {
        const b = JSON.parse(body);
        if (url.includes('api_jsonrpc')) {
            chamadas.push(b);
            return JSON.stringify({result: inp.api[b.method].shift()});
        }
        enviados.push({url, body: b});
        return '{}';
    }
    getStatus() { return 200; }
}
const Zabbix = {log() {}};
const fn = new Function('value', 'HttpRequest', 'Zabbix', inp.src + '\n' + (inp.extra || ''));
let r = null, erro = null;
try { r = fn(inp.value, HttpRequest, Zabbix); } catch (e) { erro = String(e); }
process.stdout.write(JSON.stringify({r, erro, enviados, chamadas}));
"""

_GRUPO = "123-456@g.us"


def _ms(iso_utc: str) -> int:
    return int(datetime.fromisoformat(iso_utc).replace(tzinfo=UTC).timestamp() * 1000)


def _rodar(
    arquivo: str, value: dict[str, Any] | None = None, extra: str = "", api: dict[str, list[Any]] | None = None
) -> dict[str, Any]:
    src = "\n".join((_JS / nome).read_text(encoding="utf-8") for nome in ("comum.js", arquivo))
    entrada = {"src": src, "extra": extra, "value": json.dumps(value) if value else None, "api": api or {}}
    saida = subprocess.run(
        ["node", "-e", _HARNESS], input=json.dumps(entrada), capture_output=True, text=True, check=True, timeout=30
    )
    return json.loads(saida.stdout)


def _alerta(**extra: str) -> dict[str, Any]:
    base = {
        "evo_url": "http://evo:8080",
        "evo_apikey": "k",
        "instance": "alertas-zabbix",
        "to": _GRUPO,
        "severity": "2",
        "event_name": "ICMP Ping: High ICMP ping response time",
        "host": "Câmera Shopping 1 - canal 32",
        "event_value": "1",
        "update_status": "0",
        "update_user": "{USER.FULLNAME}",
        "update_action": "{EVENT.UPDATE.ACTION}",
        "update_message": "{EVENT.UPDATE.MESSAGE}",
        "opdata": "Value: 322ms",
        "start_time": "19:42:06",
        "recovery_time": "{EVENT.RECOVERY.TIME}",
        "duration": "5m 4s",
        "link": "http://zbx/tr_events.php?triggerid=1&eventid=2",
        "janela_inicio": "08:00",
        "janela_fim": "19:00",
        "fuso_horas": "-3",
        "agora_ms": str(_ms("2026-10-07T15:00:00")),  # 12:00 em Brasília
    }
    base.update(extra)
    return base


def _texto(saida: dict[str, Any]) -> str:
    assert saida["erro"] is None, saida["erro"]
    assert len(saida["enviados"]) == 1
    return saida["enviados"][0]["body"]["text"]


# ── Alerta individual ─────────────────────────────────────────────────────


def test_rede_lenta_em_portugues_com_tempo_de_resposta() -> None:
    saida = _rodar("alerta.js", _alerta())

    assert saida["r"] == "OK"
    assert saida["enviados"][0]["body"]["number"] == _GRUPO
    assert saida["enviados"][0]["url"] == "http://evo:8080/message/sendText/alertas-zabbix"
    assert _texto(saida).splitlines() == [
        "🟡 *Câmera Shopping 1 - canal 32* está com a rede lenta",
        "Desde 19:42 · prioridade atenção",
        "Tempo de resposta: 322 ms",
        "Chamado aberto no Zendesk.",
        "Detalhes: http://zbx/tr_events.php?triggerid=1&eventid=2",
    ]


def test_fora_do_ar_sem_valor_tecnico() -> None:
    texto = _texto(
        _rodar("alerta.js", _alerta(severity="4", event_name="ICMP Ping: Unavailable by ICMP ping", opdata="Down (0)"))
    )

    assert texto.startswith("🔴 *Câmera Shopping 1 - canal 32* está fora do ar (não responde)")
    assert "prioridade alta" in texto
    assert "Down" not in texto
    assert "Value" not in texto


def test_resolucao_diz_quanto_tempo_durou() -> None:
    texto = _texto(
        _rodar(
            "alerta.js",
            _alerta(event_value="0", start_time="22:26:06", recovery_time="22:33:07", duration="7m 1s"),
        )
    )

    assert (
        texto == "✅ *Câmera Shopping 1 - canal 32* voltou ao normal\nEstava com rede lenta das 22:26 às 22:33 (7 min)."
    )


def test_trigger_desconhecida_mantem_nome_original() -> None:
    texto = _texto(_rodar("alerta.js", _alerta(event_name="Temperatura do rack acima de 30C", opdata="")))

    assert "teve um alerta: Temperatura do rack acima de 30C" in texto


@pytest.mark.parametrize(
    ("hora_utc", "envia"),
    [
        ("2026-10-07T11:00:00", True),  # 08:00 — abre a janela
        ("2026-10-07T21:59:00", True),  # 18:59
        ("2026-10-07T22:00:00", False),  # 19:00 — fecha a janela
        ("2026-10-08T01:31:00", False),  # 22:31
        ("2026-10-08T10:59:00", False),  # 07:59
    ],
)
def test_janela_de_horario(hora_utc: str, envia: bool) -> None:
    saida = _rodar("alerta.js", _alerta(agora_ms=str(_ms(hora_utc))))

    assert saida["erro"] is None
    assert bool(saida["enviados"]) is envia
    if not envia:
        assert "resumo da manhã" in saida["r"]


def test_destino_vazio_falha() -> None:
    saida = _rodar("alerta.js", _alerta(to="{ALERT.SENDTO}"))

    assert saida["erro"] is not None
    assert "destino vazio" in saida["erro"]


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("2h 25m 1s", "2h 25min"),
        ("5m 1s", "5 min"),
        ("40s", "menos de 1 min"),
        ("1d 3h 2m", "1 dia 3h"),
        ("3d 0h 5m", "3 dias"),
        ("", ""),
    ],
)
def test_duracao_legivel(entrada: str, esperado: str) -> None:
    assert _rodar("alerta.js", extra=f"return duracao({json.dumps(entrada)});")["r"] == esperado


# ── Resumo da manhã ───────────────────────────────────────────────────────


def _resumo(agora_utc: str) -> dict[str, Any]:
    return {
        "api_url": "http://zbx/api_jsonrpc.php",
        "api_token": "tok",
        "acao_id": "12",
        "mediatype_id": "105",
        "evo_url": "http://evo:8080",
        "evo_apikey": "k",
        "instance": "alertas-zabbix",
        "to": _GRUPO,
        "janela_inicio": "08:00",
        "janela_fim": "19:00",
        "fuso_horas": "-3",
        "agora_ms": str(_ms(agora_utc)),
    }


def _seg(iso_utc: str) -> str:
    return str(_ms(iso_utc) // 1000)


_HOST_32 = [{"name": "Câmera Shopping 1 - canal 32"}]
_HOST_29 = [{"name": "Câmera Shopping 1 - canal 29"}]


def test_resumo_agrupa_e_separa_ativos_de_resolvidos() -> None:
    api = {
        "alert.get": [
            [
                {"eventid": "10", "p_eventid": "0"},
                {"eventid": "11", "p_eventid": "10"},  # resolução do 10
                {"eventid": "20", "p_eventid": "0"},
                {"eventid": "21", "p_eventid": "20"},
                {"eventid": "30", "p_eventid": "0"},
            ]
        ],
        "event.get": [
            [
                {
                    "eventid": "10",
                    "name": "ICMP Ping: Unavailable by ICMP ping",
                    "clock": _seg("2026-10-08T01:35:00"),
                    "severity": "4",
                    "r_eventid": "11",
                    "hosts": _HOST_32,
                },
                {
                    "eventid": "20",
                    "name": "ICMP Ping: Unavailable by ICMP ping",
                    "clock": _seg("2026-10-08T03:00:00"),
                    "severity": "4",
                    "r_eventid": "21",
                    "hosts": _HOST_32,
                },
                {
                    "eventid": "30",
                    "name": "ICMP Ping: Unavailable by ICMP ping",
                    "clock": _seg("2026-10-08T01:51:00"),
                    "severity": "4",
                    "r_eventid": "0",
                    "hosts": _HOST_29,
                },
            ],
            [
                {"eventid": "11", "clock": _seg("2026-10-08T01:40:00")},
                {"eventid": "21", "clock": _seg("2026-10-08T03:10:00")},
            ],
        ],
    }

    saida = _rodar("resumo.js", _resumo("2026-10-08T11:00:00"), api=api)

    filtro = saida["chamadas"][0]["params"]
    assert saida["chamadas"][0]["method"] == "alert.get"
    assert filtro["actionids"] == ["12"]
    assert filtro["mediatypeids"] == ["105"]
    assert filtro["time_from"] == _ms("2026-10-07T22:00:00") // 1000  # 19:00 de ontem
    assert filtro["time_till"] == _ms("2026-10-08T11:00:00") // 1000 - 1  # até 07:59:59
    assert saida["chamadas"][1]["params"]["eventids"] == ["10", "20", "30"]
    assert _texto(saida).splitlines() == [
        "☀️ *Bom dia! Resumo dos alertas da noite* (19:00 às 08:00)",
        "",
        "🔴 *Ainda com problema* (1)",
        "• Câmera Shopping 1 - canal 29 — fora do ar (desde 22:51)",
        "",
        "✅ *Já voltaram ao normal* (1)",
        "• Câmera Shopping 1 - canal 32 — fora do ar 2 vezes (última: 00:00 às 00:10)",
        "",
        "Os chamados no Zendesk foram abertos normalmente durante a noite.",
    ]
    assert saida["r"] == "Resumo enviado: 3 alerta(s) em 2 linha(s)"


def test_resumo_sem_alertas_avisa_noite_tranquila() -> None:
    saida = _rodar("resumo.js", _resumo("2026-10-08T11:00:00"), api={"alert.get": [[]]})

    assert len(saida["chamadas"]) == 1
    assert _texto(saida) == "☀️ *Bom dia!* Nenhum alerta durante a noite (19:00 às 08:00). ✅"


def test_resumo_antes_das_8_usa_a_noite_anterior() -> None:
    saida = _rodar("resumo.js", _resumo("2026-10-08T09:00:00"), api={"alert.get": [[]]})  # 06:00

    filtro = saida["chamadas"][0]["params"]
    assert filtro["time_from"] == _ms("2026-10-06T22:00:00") // 1000
    assert filtro["time_till"] == _ms("2026-10-07T11:00:00") // 1000 - 1


def test_resumo_corta_listas_longas() -> None:
    eventos = [
        {
            "eventid": str(i),
            "name": "ICMP Ping: Unavailable by ICMP ping",
            "clock": _seg("2026-10-08T01:00:00"),
            "severity": "4",
            "r_eventid": "0",
            "hosts": [{"name": f"Câmera {i}"}],
        }
        for i in range(1, 31)
    ]
    api = {"alert.get": [[{"eventid": e["eventid"], "p_eventid": "0"} for e in eventos]], "event.get": [eventos]}

    texto = _texto(_rodar("resumo.js", _resumo("2026-10-08T11:00:00"), api=api))

    assert "🔴 *Ainda com problema* (30)" in texto
    assert texto.count("\n• ") == 25
    assert "… e mais 5 (veja no Zabbix)" in texto


# ── Fim de semana e feriados: só resumos às 08, 14 e 18h ──────────────────


@pytest.mark.parametrize(
    ("ano", "esperado"), [(2025, "2025-04-20"), (2026, "2026-04-05"), (2027, "2027-03-28"), (2038, "2038-04-25")]
)
def test_pascoa(ano: int, esperado: str) -> None:
    ms = _rodar("alerta.js", extra=f"return pascoa({ano});")["r"]
    assert datetime.fromtimestamp(ms / 1000, UTC).date().isoformat() == esperado


@pytest.mark.parametrize(
    ("data", "folga"),
    [
        ("2026-10-10", True),  # sábado
        ("2026-10-11", True),  # domingo
        ("2026-10-12", True),  # Nossa Senhora Aparecida (segunda)
        ("2026-04-03", True),  # Sexta-feira Santa
        ("2026-06-04", True),  # Corpus Christi (Curitiba)
        ("2026-09-08", True),  # Nossa Senhora da Luz dos Pinhais (Curitiba)
        ("2026-11-20", True),  # Consciência Negra
        ("2026-02-17", False),  # Carnaval é ponto facultativo...
        ("2026-10-13", False),  # terça comum
    ],
)
def test_dias_de_folga(data: str, folga: bool) -> None:
    ano, mes, dia = (int(x) for x in data.split("-"))
    js = f"return ehFolga({{ano: {ano}, mes: {mes - 1}, dia: {dia}}}, '');"
    assert _rodar("alerta.js", extra=js)["r"] is folga


def test_feriado_extra_pela_macro() -> None:
    js = "return [ehFolga({ano: 2026, mes: 1, dia: 17}, '16/02, 17/02'), ehFolga({ano: 2026, mes: 1, dia: 18}, '2026-02-18')];"
    assert _rodar("alerta.js", extra=js)["r"] == [True, True]


@pytest.mark.parametrize(
    "hora_utc",
    ["2026-10-10T15:00:00", "2026-10-12T11:00:00", "2026-10-11T21:00:00"],  # sáb 12h, feriado 08h, dom 18h
)
def test_alerta_nao_vai_na_hora_em_folga(hora_utc: str) -> None:
    saida = _rodar("alerta.js", _alerta(agora_ms=str(_ms(hora_utc)), resumos_folga="08:00,14:00,18:00"))

    assert saida["erro"] is None and not saida["enviados"]
    assert saida["r"] == "Fim de semana/feriado: vai no resumo das 08:00, 14:00, 18:00"


def _folga(agora_utc: str) -> dict[str, Any]:
    return {
        **_resumo(agora_utc),
        "resumos_folga": "08:00,14:00,18:00",
        "feriados_extras": "{$WHATSAPP.FERIADOS.EXTRAS}",
    }


@pytest.mark.parametrize(
    ("agora_utc", "de_utc", "ate_utc", "texto"),
    [
        # sábado 08:00: a noite de sexta
        ("2026-10-10T11:00:00", "2026-10-09T22:00:00", "2026-10-10T11:00:00",
         "☀️ *Bom dia!* Nenhum alerta durante a noite (19:00 às 08:00). ✅"),
        # sábado 14:00 e 18:00
        ("2026-10-10T17:00:00", "2026-10-10T11:00:00", "2026-10-10T17:00:00",
         "🕑 *Boa tarde!* Nenhum alerta durante a manhã (08:00 às 14:00). ✅"),
        ("2026-10-10T21:00:00", "2026-10-10T17:00:00", "2026-10-10T21:00:00",
         "🌆 *Boa noite!* Nenhum alerta durante a tarde (14:00 às 18:00). ✅"),
        # segunda feriado 08:00 (desde domingo 18:00) e terça útil 08:00 (desde o feriado 18:00)
        ("2026-10-12T11:00:00", "2026-10-11T21:00:00", "2026-10-12T11:00:00",
         "☀️ *Bom dia!* Nenhum alerta durante a noite (18:00 às 08:00). ✅"),
        ("2026-10-13T11:00:00", "2026-10-12T21:00:00", "2026-10-13T11:00:00",
         "☀️ *Bom dia!* Nenhum alerta durante a noite (18:00 às 08:00). ✅"),
        # execução atrasada alguns minutos ainda é o mesmo resumo
        ("2026-10-10T17:04:00", "2026-10-10T11:00:00", "2026-10-10T17:00:00",
         "🕑 *Boa tarde!* Nenhum alerta durante a manhã (08:00 às 14:00). ✅"),
    ],
)  # fmt: skip
def test_resumos_de_folga(agora_utc: str, de_utc: str, ate_utc: str, texto: str) -> None:
    saida = _rodar("resumo.js", _folga(agora_utc), api={"alert.get": [[]]})

    filtro = saida["chamadas"][0]["params"]
    assert filtro["time_from"] == _ms(de_utc) // 1000
    assert filtro["time_till"] == _ms(ate_utc) // 1000 - 1
    assert _texto(saida) == texto


@pytest.mark.parametrize("agora_utc", ["2026-10-07T17:00:00", "2026-10-07T21:10:00"])  # quarta 14:00 e 18:10
def test_dia_util_nao_manda_resumo_das_14_e_18(agora_utc: str) -> None:
    saida = _rodar("resumo.js", _folga(agora_utc))

    assert saida["erro"] is None and not saida["enviados"] and not saida["chamadas"]
    assert saida["r"] == "Dia útil: sem resumo agora (os alertas vão na hora)"


def test_resumo_de_folga_com_alertas_avisa_os_horarios() -> None:
    api = {
        "alert.get": [[{"eventid": "10", "p_eventid": "0"}]],
        "event.get": [
            [
                {
                    "eventid": "10",
                    "name": "ICMP Ping: Unavailable by ICMP ping",
                    "clock": _seg("2026-10-10T13:00:00"),
                    "severity": "4",
                    "r_eventid": "0",
                    "hosts": _HOST_29,
                }
            ]
        ],
    }

    linhas = _texto(_rodar("resumo.js", _folga("2026-10-10T17:00:00"), api=api)).splitlines()

    assert linhas[:2] == [
        "🕑 *Boa tarde! Resumo dos alertas da manhã* (08:00 às 14:00)",
        "📅 Fim de semana/feriado: alertas só às 08:00, 14:00 e 18:00.",
    ]
    assert "• Câmera Shopping 1 - canal 29 — fora do ar (desde 10:00)" in linhas
    assert linhas[-1] == "Os chamados no Zendesk foram abertos normalmente durante a manhã."


# ── Compatibilidade com o Duktape (ES5) e scripts Python ──────────────────


@pytest.mark.parametrize("arquivo", ["comum.js", "alerta.js", "resumo.js"])
def test_js_sem_sintaxe_es6(arquivo: str) -> None:
    codigo = re.sub(r"//.*|/\*.*?\*/", "", (_JS / arquivo).read_text(encoding="utf-8"), flags=re.S)

    assert not re.search(r"\b(let|const|class)\s|=>|`|\.padStart\(|\.includes\(", codigo)


def _modulo(nome: str, arquivo: str) -> Any:
    spec = importlib.util.spec_from_file_location(nome, _INFRA / arquivo)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_script_10_agenda_no_inicio_da_janela_e_usa_token_secreto() -> None:
    resumo = _modulo("whatsapp_resumo_script", "10_create_whatsapp_resumo.py")

    assert resumo.agendamento("08:00") == "0;h8m0"
    assert resumo.agendamento("07:30") == "0;h7m30"
    assert resumo.agendamento("08:00", "08:00,14:00,18:00") == "0;h8m0;h14m0;h18m0"
    params = {p["name"]: p["value"] for p in resumo.parametros_item("105")}
    assert params["api_token"] == "{$WHATSAPP.RESUMO.TOKEN}"
    assert params["mediatype_id"] == "105"
    assert (params["janela_inicio"], params["janela_fim"]) == ("08:00", "19:00")
    assert params["resumos_folga"] == "08:00,14:00,18:00"
    assert params["feriados_extras"] == "{$WHATSAPP.FERIADOS.EXTRAS}"
    assert "function principal" in resumo.ITEM_SCRIPT
    assert "function mensagemResumo" in resumo.ITEM_SCRIPT


def test_webhook_09_usa_alerta_js() -> None:
    wa = _modulo("whatsapp_alert_script_js", "09_create_whatsapp_alert.py")

    assert "function mensagemAlerta" in wa.WEBHOOK_SCRIPT
    assert "function traduzir" in wa.WEBHOOK_SCRIPT
