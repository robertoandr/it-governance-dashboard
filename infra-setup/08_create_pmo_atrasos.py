#!/usr/bin/env python3
"""
Trigger no Zabbix para projetos atrasados na lista PMO do ClickUp ("TI | Projetos").

Pedido do PDF V2.0 para /gov/pmo: "total por usuário abertos, concluídos e
atrasados; se atrasado abrir trigger". Os totais aparecem na página; o atraso
vira problema no Zabbix (e portanto em /gov/triggers), um por pessoa, que
fecha sozinho quando a pessoa não tiver mais projeto atrasado.

Cria/atualiza (idempotente):
  - Grupo "Governança — PMO" e host "ClickUp-PMO" (visível "ClickUp — PMO", sem interface)
  - Item Script "pmo.clickup.resumo" (10 min): lê a lista no ClickUp e devolve
    abertas/concluídas/atrasadas por responsável — mesmas regras da página
    (status pelo tipo; "cancelado" conta como encerrado; atraso = vencimento
    antes de hoje no fuso de Brasília)
  - Descoberta por responsável + itens dependentes + trigger
    "PMO: <pessoa> tem N projeto(s) atrasado(s)" (Atenção, tag origem=pmo)
  - Trigger "PMO: ClickUp sem resposta" (sem dados por 40 min)
  - Exclui a tag origem=pmo da ação 12 (chamado Zendesk): atraso de projeto
    não é incidente de infraestrutura

Usa as macros globais {$CLICKUP_TOKEN} (secreta) e {$CLICKUP_LIST_ID}, criadas
por 05_create_clickup_alert.py.

Uso:
    python3 infra-setup/08_create_pmo_atrasos.py

Pré-requisitos: ZABBIX_URL/ZABBIX_TOKEN no .env (raiz do projeto).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import urllib3

_env_file = Path(__file__).resolve().parent.parent / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

ZABBIX_URL = os.environ.get("ZABBIX_URL", "http://172.29.2.11:8080/api_jsonrpc.php")
if "host.docker.internal" in ZABBIX_URL:
    ZABBIX_URL = "http://172.29.2.11:8080/api_jsonrpc.php"
if not ZABBIX_URL.endswith("/api_jsonrpc.php"):
    ZABBIX_URL = ZABBIX_URL.rstrip("/") + "/api_jsonrpc.php"
ZABBIX_TOKEN = os.environ.get("ZABBIX_TOKEN", "")

os.environ.pop("ZABBIX_USER", None)
os.environ.pop("ZABBIX_PASSWORD", None)

urllib3.disable_warnings()

try:
    from zabbix_utils import ZabbixAPI
except ImportError:
    sys.exit("Instale: pip install zabbix-utils --break-system-packages")

GRUPO = "Governança — PMO"
# Nome técnico só aceita letras, números, espaço, ponto, hífen e sublinhado;
# o travessão fica no nome visível.
HOST = "ClickUp-PMO"
HOST_VISIVEL = "ClickUp — PMO"
CHAVE_RESUMO = "pmo.clickup.resumo"
CHAVE_LLD = "pmo.responsaveis"
ACAO_ZENDESK = "Governança — Chamado Zendesk"

ITEM_SCRIPT = 21
ITEM_DEPENDENTE = 18
TEXTO, NUMERO = "4", "3"
PRE_JSONPATH = "12"
PRIO_ATENCAO = "2"

# Duktape (ES5) no Zabbix Server. Atraso: vencimento antes do início de hoje em
# Brasília (UTC-3, sem horário de verão) — mesmo critério de clickup_tarefas.py.
SCRIPT = r"""
var p = JSON.parse(value);
var agora = Date.now(), tres = 3 * 3600000;
var hoje = Math.floor((agora - tres) / 86400000) * 86400000 + tres;
var pessoas = {}, semAtrasadas = 0, total = 0;
for (var pagina = 0; pagina < 30; pagina++) {
    var req = new HttpRequest();
    req.addHeader('Authorization: ' + p.token);
    var corpo = req.get('https://api.clickup.com/api/v2/list/' + p.list_id +
        '/task?include_closed=true&subtasks=true&page=' + pagina);
    if (req.getStatus() !== 200) throw 'ClickUp HTTP ' + req.getStatus();
    var d = JSON.parse(corpo), tarefas = d.tasks || [];
    for (var i = 0; i < tarefas.length; i++) {
        var t = tarefas[i], st = t.status || {}, tipo = st.type || '';
        var nomeStatus = String(st.status || '').toLowerCase();
        var fechada = tipo === 'closed' || tipo === 'done' || nomeStatus.indexOf('cancel') === 0;
        var atrasada = !fechada && t.due_date && parseInt(t.due_date, 10) < hoje;
        var resp = t.assignees || [];
        total++;
        if (!resp.length && atrasada) semAtrasadas++;
        for (var j = 0; j < resp.length; j++) {
            var a = resp[j], id = String(a.id);
            if (!pessoas[id]) pessoas[id] = {id: id, nome: a.username || a.email || id, abertas: 0, concluidas: 0, atrasadas: 0};
            if (fechada) pessoas[id].concluidas++; else pessoas[id].abertas++;
            if (atrasada) pessoas[id].atrasadas++;
        }
    }
    if (d.last_page || !tarefas.length) break;
}
var lista = [];
for (var k in pessoas) lista.push(pessoas[k]);
return JSON.stringify({total: total, sem_responsavel_atrasadas: semAtrasadas, pessoas: lista});
"""


def banner(text: str) -> None:
    print(f"\n{'=' * 60}\n  {text}\n{'=' * 60}")


def ok(msg: str) -> None:
    print(f"  [OK] {msg}")


def info(msg: str) -> None:
    print(f"  [--] {msg}")


def _upsert(api: ZabbixAPI, obj: str, idfield: str, existentes: list[dict], dados: dict) -> str:
    """Cria ou atualiza um objeto da API e devolve o id."""
    ns = getattr(api, obj)
    if existentes:
        oid = existentes[0][idfield]
        ns.update(**{idfield: oid}, **dados)
        info(f"{obj} atualizado: {dados.get('name') or dados.get('description') or oid}")
        return oid
    oid = ns.create(**dados)[f"{idfield}s"][0]
    ok(f"{obj} criado: {dados.get('name') or dados.get('description') or oid}")
    return oid


def main() -> None:
    banner("PMO — projetos atrasados viram trigger")
    api = ZabbixAPI(url=ZABBIX_URL, token=ZABBIX_TOKEN, skip_version_check=True)
    print(f"  Zabbix {api.api_version()} conectado")

    macros = {m["macro"] for m in api.usermacro.get(globalmacro=True, output=["macro"])}
    faltam = {"{$CLICKUP_TOKEN}", "{$CLICKUP_LIST_ID}"} - macros
    if faltam:
        sys.exit(f"  [ERRO] Macros globais ausentes: {', '.join(sorted(faltam))} — rode 05_create_clickup_alert.py")

    banner("1. Grupo e host")
    grupos = api.hostgroup.get(output=["groupid"], filter={"name": GRUPO})
    groupid = grupos[0]["groupid"] if grupos else api.hostgroup.create(name=GRUPO)["groupids"][0]
    hosts = api.host.get(output=["hostid"], filter={"host": HOST})
    hostid = _upsert(
        api,
        "host",
        "hostid",
        hosts,
        {
            "host": HOST,
            "name": HOST_VISIVEL,
            "groups": [{"groupid": groupid}],
            "tags": [{"tag": "origem", "value": "pmo"}],
            "description": "Lista PMO do ClickUp (TI | Projetos). Criado por infra-setup/08_create_pmo_atrasos.py.",
        },
    )

    banner("2. Item de coleta (Script, 10 min)")
    resumo = {
        "name": "PMO: resumo do ClickUp",
        "key_": CHAVE_RESUMO,
        "type": ITEM_SCRIPT,
        "value_type": TEXTO,
        "delay": "10m",
        "history": "7d",
        "timeout": "60s",
        "params": SCRIPT,
        "parameters": [
            {"name": "token", "value": "{$CLICKUP_TOKEN}"},
            {"name": "list_id", "value": "{$CLICKUP_LIST_ID}"},
        ],
        "tags": [{"tag": "origem", "value": "pmo"}],
    }
    itens = api.item.get(output=["itemid"], hostids=hostid, filter={"key_": CHAVE_RESUMO})
    if itens:
        masterid = _upsert(api, "item", "itemid", itens, resumo)
    else:
        masterid = _upsert(api, "item", "itemid", [], {**resumo, "hostid": hostid})

    banner("3. Descoberta por responsável")
    regra = {
        "name": "PMO: responsáveis",
        "key_": CHAVE_LLD,
        "type": ITEM_DEPENDENTE,
        "master_itemid": masterid,
        "lifetime_type": "0",
        "lifetime": "7d",
        "lld_macro_paths": [
            {"lld_macro": "{#ID}", "path": "$.id"},
            {"lld_macro": "{#NOME}", "path": "$.nome"},
        ],
        "preprocessing": [
            {"type": PRE_JSONPATH, "params": "$.pessoas", "error_handler": "0", "error_handler_params": ""}
        ],
    }
    regras = api.discoveryrule.get(output=["itemid"], hostids=hostid, filter={"key_": CHAVE_LLD})
    if regras:
        ruleid = _upsert(api, "discoveryrule", "itemid", regras, regra)
    else:
        ruleid = _upsert(api, "discoveryrule", "itemid", [], {**regra, "hostid": hostid})

    for campo, rotulo in (("abertas", "abertos"), ("concluidas", "concluídos"), ("atrasadas", "atrasados")):
        chave = f"pmo.{campo}[{{#ID}}]"
        proto = {
            "name": f"PMO: {{#NOME}} — projetos {rotulo}",
            "key_": chave,
            "type": ITEM_DEPENDENTE,
            "master_itemid": masterid,
            "value_type": NUMERO,
            "history": "90d",
            "trends": "365d",
            "preprocessing": [
                {
                    "type": PRE_JSONPATH,
                    "params": f"$.pessoas[?(@.id=='{{#ID}}')].{campo}.first()",
                    "error_handler": "0",
                    "error_handler_params": "",
                }
            ],
            "tags": [{"tag": "origem", "value": "pmo"}],
        }
        existentes = api.itemprototype.get(output=["itemid"], discoveryids=ruleid, filter={"key_": chave})
        if existentes:
            _upsert(api, "itemprototype", "itemid", existentes, proto)
        else:
            _upsert(api, "itemprototype", "itemid", [], {**proto, "hostid": hostid, "ruleid": ruleid})

    banner("4. Triggers")
    gatilho = {
        "description": "PMO: {#NOME} tem {ITEM.LASTVALUE} projeto(s) atrasado(s)",
        "expression": f"last(/{HOST}/pmo.atrasadas[{{#ID}}])>0",
        "priority": PRIO_ATENCAO,
        "manual_close": "1",
        "url_name": "Ver na PMO",
        "url": "https://noc.grupogadens.com.br/gov/pmo",
        "comments": "Projeto da lista PMO do ClickUp com vencimento passado e não concluído. "
        "Fecha sozinho quando a pessoa não tiver mais projeto atrasado.",
        "tags": [{"tag": "origem", "value": "pmo"}],
    }
    existentes = api.triggerprototype.get(
        output=["triggerid"], discoveryids=ruleid, filter={"description": gatilho["description"]}
    )
    _upsert(api, "triggerprototype", "triggerid", existentes, gatilho)

    sem_dados = {
        "description": "PMO: ClickUp sem resposta",
        "expression": f"nodata(/{HOST}/{CHAVE_RESUMO},40m)=1",
        "priority": PRIO_ATENCAO,
        "comments": "A coleta da lista PMO no ClickUp falhou nas últimas 4 tentativas (token ou API).",
        "tags": [{"tag": "origem", "value": "pmo"}],
    }
    existentes = api.trigger.get(output=["triggerid"], hostids=hostid, filter={"description": sem_dados["description"]})
    _upsert(api, "trigger", "triggerid", existentes, sem_dados)

    banner("5. Ação de chamado Zendesk ignora origem=pmo")
    acoes = api.action.get(output=["actionid"], selectFilter="extend", filter={"name": ACAO_ZENDESK})
    if not acoes:
        info(f"Ação '{ACAO_ZENDESK}' não encontrada — nada a ajustar")
    else:
        filtro = acoes[0]["filter"]
        # Copia só os campos preenchidos: condições como "problema suprimido"
        # (tipo 16) recusam value/value2, mesmo vazios.
        conds = [
            {
                k: c[k]
                for k in ("conditiontype", "operator", "value", "value2")
                if k in ("conditiontype", "operator") or c[k]
            }
            for c in filtro["conditions"]
        ]
        # conditiontype 26 = valor de tag; operator 1 = diferente de
        excluir = {"conditiontype": "26", "operator": "1", "value": "pmo", "value2": "origem"}
        if excluir in conds:
            info("Ação já ignora origem=pmo")
        else:
            api.action.update(
                actionid=acoes[0]["actionid"],
                filter={"evaltype": "0", "conditions": [*conds, excluir]},
            )
            ok("Ação de chamado Zendesk agora ignora problemas com tag origem=pmo")

    print("\n  Pronto. A primeira coleta acontece em até 10 min (ou 'Executar agora' no item).")


if __name__ == "__main__":
    main()
