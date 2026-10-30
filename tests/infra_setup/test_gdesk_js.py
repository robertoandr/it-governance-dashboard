"""Testes do webhook infra-setup/gdesk/alerta.js (abre chamado no GDesk).

O JS roda no Node com HttpRequest/Zabbix simulados, embrulhado numa função
como o Zabbix faz com webhooks.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

_INFRA = Path(__file__).resolve().parent.parent.parent / "infra-setup"
_JS = _INFRA / "gdesk"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node não instalado")

_HARNESS = r"""
const fs = require('fs');
const inp = JSON.parse(fs.readFileSync(0, 'utf8'));
const enviados = [];
function HttpRequest() { this.headers = []; }
HttpRequest.prototype.addHeader = function (h) { this.headers.push(h); };
HttpRequest.prototype.post = function (url, body) {
    enviados.push({url: url, headers: this.headers, body: JSON.parse(body)});
    return inp.resp_body;
};
HttpRequest.prototype.getStatus = function () { return inp.resp_status; };
const Zabbix = {log: function () {}};
const fn = new Function('value', 'HttpRequest', 'Zabbix', inp.src);
let r = null, erro = null;
try { r = fn(inp.value, HttpRequest, Zabbix); } catch (e) { erro = String(e); }
process.stdout.write(JSON.stringify({r: r, erro: erro, enviados: enviados}));
"""


def _rodar(value: dict[str, Any], resp_status: int = 200, resp_body: str = '{"id": 777}') -> dict[str, Any]:
    entrada = {
        "src": (_JS / "alerta.js").read_text(encoding="utf-8"),
        "value": json.dumps(value),
        "resp_status": resp_status,
        "resp_body": resp_body,
    }
    saida = subprocess.run(
        ["node", "-e", _HARNESS], input=json.dumps(entrada), capture_output=True, text=True, check=True, timeout=30
    )
    return json.loads(saida.stdout)


def _alerta(**extra: str) -> dict[str, Any]:
    base = {
        "gdesk_url": "http://172.29.3.70:4000",
        "gdesk_apikey": "chave-teste",
        "queue_id": "2",
        "event_value": "1",
        "event_update_status": "0",
        "event_tags": "origem: infra, host: SRV-01",
        "severity": "4",
        "event_name": "ICMP Ping: Unavailable by ICMP ping",
        "event_severity": "High",
        "host": "SRV-01",
        "opdata": "Down (0)",
        "event_id": "555",
        "event_date": "2026.10.27",
        "event_time": "16:20:00",
        "age": "5m",
        "link": "http://zbx/tr_events.php?triggerid=1&eventid=555",
        "severity_not_classified": "baixa",
        "severity_information": "baixa",
        "severity_warning": "normal",
        "severity_average": "normal",
        "severity_high": "alta",
        "severity_disaster": "urgente",
    }
    base.update(extra)
    return base


def test_abre_chamado_em_problema_novo() -> None:
    saida = _rodar(_alerta(), resp_body='{"id": 777}')

    assert saida["erro"] is None
    assert len(saida["enviados"]) == 1
    env = saida["enviados"][0]
    assert env["url"] == "http://172.29.3.70:4000/api/public/tickets"
    assert "Authorization: Bearer chave-teste" in env["headers"]
    assert env["body"]["title"] == "High: ICMP Ping: Unavailable by ICMP ping"
    assert env["body"]["queue_id"] == 2
    assert env["body"]["priority"] == "alta"
    assert "Host: SRV-01" in env["body"]["description"]
    assert "http://zbx/tr_events.php?triggerid=1&eventid=555" in env["body"]["description"]
    # Tag gravada de volta no evento (dedup + rastreio).
    assert json.loads(saida["r"]) == {"tags": {"gdesk_ticket": "777"}}


@pytest.mark.parametrize(
    ("nseverity", "prioridade"),
    [("5", "urgente"), ("4", "alta"), ("3", "normal"), ("2", "normal"), ("1", "baixa"), ("0", "baixa")],
)
def test_severidade_vira_prioridade(nseverity: str, prioridade: str) -> None:
    saida = _rodar(_alerta(severity=nseverity))

    assert saida["erro"] is None
    assert saida["enviados"][0]["body"]["priority"] == prioridade


def test_recovery_nao_abre_chamado() -> None:
    saida = _rodar(_alerta(event_value="0"))

    assert saida["erro"] is None
    assert saida["enviados"] == []
    assert saida["r"] == "{}"


def test_atualizacao_ack_nao_abre_chamado() -> None:
    saida = _rodar(_alerta(event_update_status="1"))

    assert saida["erro"] is None
    assert saida["enviados"] == []
    assert saida["r"] == "{}"


def test_dedup_nao_reabre_quando_ja_tem_tag() -> None:
    saida = _rodar(_alerta(event_tags="origem: infra, gdesk_ticket: 42"))

    assert saida["erro"] is None
    assert saida["enviados"] == []
    assert saida["r"] == "{}"


def test_erro_http_propaga() -> None:
    saida = _rodar(_alerta(), resp_status=500, resp_body='{"error": "boom"}')

    assert saida["enviados"]  # tentou postar
    assert saida["erro"] is not None
    assert "HTTP 500" in saida["erro"]


def test_falta_url_ou_chave_falha() -> None:
    saida = _rodar(_alerta(gdesk_apikey=""))

    assert saida["erro"] is not None
    assert "gdesk_url/gdesk_apikey" in saida["erro"]


@pytest.mark.parametrize(
    ("corpo", "esperado"),
    [
        ('{"id": 1}', "1"),
        ('{"ticket_id": 2}', "2"),
        ('{"number": "T-3"}', "T-3"),
        ('{"data": {"id": 4}}', "4"),
        ('{"ok": true}', ""),
        ("texto-nao-json", ""),
    ],
)
def test_extrai_id_de_varios_formatos(corpo: str, esperado: str) -> None:
    saida = _rodar(_alerta(), resp_body=corpo)

    assert saida["erro"] is None
    assert json.loads(saida["r"])["tags"]["gdesk_ticket"] == esperado


def test_js_sem_sintaxe_es6() -> None:
    codigo = re.sub(r"//.*|/\*.*?\*/", "", (_JS / "alerta.js").read_text(encoding="utf-8"), flags=re.S)

    assert not re.search(r"\b(let|const|class)\s|=>|`|\.padStart\(|\.includes\(", codigo)
