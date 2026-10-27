#!/usr/bin/env python3
"""
Liga a abertura de chamado no GDesk à ação 12 "Governança — Chamado Zendesk".

A partir de agora o mesmo alerta que abre chamado no Zendesk também abre no
GDesk (vm-sistemas:4000). O Zendesk continua intacto — isto é uma operação a
mais na mesma ação.

Cria/reaproveita (idempotente):
  - Macros globais {$GDESK.URL}, {$GDESK.QUEUE_ID} e {$GDESK.APIKEY} (secreta)
  - Media type webhook "GDesk" — POST /api/public/tickets (Bearer {$GDESK.APIKEY}).
    O texto do chamado é montado em gdesk/alerta.js. Abre chamado só em problema
    novo (recovery/atualização são ignorados: a API pública do GDesk hoje só
    cria chamado; a baixa segue no Zendesk). Dedup por tag do evento
    (gdesk_ticket), gravada de volta no evento (process_tags).
  - Mídia no usuário `alertas-integracao`
  - Operação na ação 12 (mesmo passo 2 do Zendesk: só se o problema durar >= 5 min)

Uso:
    python3 infra-setup/14_create_gdesk_alert.py

Pré-requisitos: ZABBIX_URL/ZABBIX_TOKEN no .env (raiz) e GDESK_API_KEY no
ambiente ou no .env/.env.local. URL e fila em GDESK_URL (padrão
http://172.29.3.70:4000) e GDESK_QUEUE_ID (padrão 2 = T.I.).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import urllib3


def _ler_env(arquivo: Path) -> dict[str, str]:
    valores: dict[str, str] = {}
    if arquivo.exists():
        for line in arquivo.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                valores[k.strip()] = v.strip().strip('"').strip("'")
    return valores


_RAIZ = Path(__file__).resolve().parent.parent
for _arquivo in (_RAIZ / ".env", _RAIZ / ".env.local"):
    for _k, _v in _ler_env(_arquivo).items():
        os.environ.setdefault(_k, _v)

ZABBIX_URL = os.environ.get("ZABBIX_URL", "http://172.29.2.11:5443/api_jsonrpc.php")
if "host.docker.internal" in ZABBIX_URL:
    ZABBIX_URL = "http://172.29.2.11:5443/api_jsonrpc.php"
if not ZABBIX_URL.endswith("/api_jsonrpc.php"):
    ZABBIX_URL = ZABBIX_URL.rstrip("/") + "/api_jsonrpc.php"
ZABBIX_TOKEN = os.environ.get("ZABBIX_TOKEN", "")
GDESK_API_KEY = os.environ.get("GDESK_API_KEY", "")
GDESK_URL = os.environ.get("GDESK_URL", "http://172.29.3.70:4000").rstrip("/")
GDESK_QUEUE_ID = os.environ.get("GDESK_QUEUE_ID", "2")

os.environ.pop("ZABBIX_USER", None)
os.environ.pop("ZABBIX_PASSWORD", None)

urllib3.disable_warnings()

try:
    from zabbix_utils import ZabbixAPI
    from zabbix_utils.exceptions import APIRequestError
except ImportError:
    sys.exit("Instale: pip install zabbix-utils --break-system-packages")

# ── Constantes ─────────────────────────────────────────────────────────────
MEDIATYPE_NAME = "GDesk"
ACTION_ID = "12"
USUARIO = "alertas-integracao"
SENDTO = "T.I."  # cosmético: o webhook usa {$GDESK.QUEUE_ID}, não o sendto
ESC_STEP = 2  # mesmo passo da operação Zendesk (esc_period 5m na ação)

MEDIATYPE_TYPE_WEBHOOK = "4"

LINK_EVENTO = "{$ZABBIX.URL}/tr_events.php?triggerid={TRIGGER.ID}&eventid={EVENT.ID}"

# Sandbox JS do Zabbix Server — `value` é o JSON dos `parameters` abaixo.
WEBHOOK_SCRIPT = (Path(__file__).resolve().parent / "gdesk" / "alerta.js").read_text(encoding="utf-8")

# Severidade do Zabbix -> prioridade do GDesk (baixa/normal/alta/urgente).
PRIORIDADE = {
    "severity_not_classified": "baixa",
    "severity_information": "baixa",
    "severity_warning": "normal",
    "severity_average": "normal",
    "severity_high": "alta",
    "severity_disaster": "urgente",
}

# Só o problema abre chamado; o webhook ignora recovery/atualização. Mesmo
# assim definimos só o template de problema (recovery=0).
MESSAGE_TEMPLATES = [
    {
        "eventsource": "0",
        "recovery": "0",
        "subject": "{EVENT.SEVERITY}: {EVENT.NAME}",
        "message": "Chamado GDesk para {HOST.NAME} — evento #{EVENT.ID}",
    },
]


def parametros_webhook() -> list[dict[str, str]]:
    """Parâmetros do media type (viram o JSON `value` no webhook)."""
    params = [
        {"name": "gdesk_url", "value": "{$GDESK.URL}"},
        {"name": "gdesk_apikey", "value": "{$GDESK.APIKEY}"},
        {"name": "queue_id", "value": "{$GDESK.QUEUE_ID}"},
        {"name": "event_value", "value": "{EVENT.VALUE}"},
        {"name": "event_update_status", "value": "{EVENT.UPDATE.STATUS}"},
        {"name": "event_tags", "value": "{EVENT.TAGS}"},
        {"name": "severity", "value": "{EVENT.NSEVERITY}"},
        {"name": "event_name", "value": "{EVENT.NAME}"},
        {"name": "event_severity", "value": "{EVENT.SEVERITY}"},
        {"name": "host", "value": "{HOST.NAME}"},
        {"name": "opdata", "value": "{EVENT.OPDATA}"},
        {"name": "event_id", "value": "{EVENT.ID}"},
        {"name": "event_date", "value": "{EVENT.DATE}"},
        {"name": "event_time", "value": "{EVENT.TIME}"},
        {"name": "age", "value": "{EVENT.AGE}"},
        {"name": "link", "value": LINK_EVENTO},
    ]
    params += [{"name": k, "value": v} for k, v in PRIORIDADE.items()]
    return params


def banner(text: str) -> None:
    print(f"\n{'=' * 60}\n  {text}\n{'=' * 60}")


def ok(msg: str) -> None:
    print(f"  [OK] {msg}")


def info(msg: str) -> None:
    print(f"  [--] {msg}")


def _somar_midia(medias: list[dict], mediatypeid: str, sendto: str) -> list[dict]:
    """Devolve as mídias do usuário com a do GDesk incluída/atualizada.

    Mantém as demais mídias (Zendesk, WhatsApp...) sem os campos somente-leitura
    que o user.update recusa.
    """
    campos = ("mediatypeid", "sendto", "active", "severity", "period")
    resultado = [{k: m[k] for k in campos if k in m} for m in medias if m.get("mediatypeid") != mediatypeid]
    resultado.append(
        {
            "mediatypeid": mediatypeid,
            "sendto": sendto,
            "active": "0",
            "severity": "63",  # todas as severidades
            "period": "1-7,00:00-24:00",
        }
    )
    return resultado


def _somar_operacao(operations: list[dict], mediatypeid: str, userid: str) -> list[dict] | None:
    """Devolve as operações da ação com o envio GDesk somado, ou None se já existe.

    action.update substitui a lista inteira, então as operações atuais são
    reenviadas só com os campos aceitos na escrita.
    """
    if any(op.get("opmessage", {}).get("mediatypeid") == mediatypeid for op in operations):
        return None

    def _limpa(op: dict) -> dict:
        nova = {
            k: op[k] for k in ("operationtype", "esc_period", "esc_step_from", "esc_step_to", "evaltype") if k in op
        }
        if "opmessage" in op:
            # subject/message só são aceitos com default_msg=0 (mensagem própria).
            campos = ("default_msg", "mediatypeid")
            if op["opmessage"].get("default_msg") == "0":
                campos += ("subject", "message")
            nova["opmessage"] = {k: v for k, v in op["opmessage"].items() if k in campos}
        # Zabbix 7.4 recusa operationid/actionid em qualquer nível da escrita.
        for chave in ("opmessage_usr", "opmessage_grp", "opconditions"):
            if op.get(chave):
                nova[chave] = [
                    {k: v for k, v in item.items() if k not in ("operationid", "actionid")} for item in op[chave]
                ]
        return nova

    resultado = [_limpa(op) for op in operations]
    resultado.append(
        {
            "operationtype": "0",  # enviar mensagem
            "esc_period": "0",
            "esc_step_from": str(ESC_STEP),
            "esc_step_to": str(ESC_STEP),
            "opmessage": {"default_msg": "1", "mediatypeid": mediatypeid},
            "opmessage_usr": [{"userid": userid}],
        }
    )
    return resultado


def _ensure_global_macro(api: ZabbixAPI, macro: str, value: str, description: str, secret: bool = False) -> None:
    macro_type = "1" if secret else "0"
    existing = api.usermacro.get(globalmacro=True, output=["globalmacroid"], filter={"macro": macro})
    if existing:
        api.usermacro.updateglobal(
            globalmacroid=existing[0]["globalmacroid"], value=value, description=description, type=macro_type
        )
        info(f"Macro global atualizada: {macro}")
    else:
        api.usermacro.createglobal(macro=macro, value=value, description=description, type=macro_type)
        ok(f"Macro global criada: {macro}")


def main() -> None:
    banner("Alerta GDesk — ação 12 (abre chamado também no GDesk)")

    if not GDESK_API_KEY:
        sys.exit("  [ERRO] GDESK_API_KEY não encontrada (ambiente, .env ou .env.local)")

    api = ZabbixAPI(url=ZABBIX_URL, token=ZABBIX_TOKEN, skip_version_check=True)
    print(f"  Zabbix {api.api_version()} conectado — GDesk {GDESK_URL} fila {GDESK_QUEUE_ID}")

    banner("1. Macros globais {$GDESK.URL} / {$GDESK.QUEUE_ID} / {$GDESK.APIKEY}")
    _ensure_global_macro(api, "{$GDESK.URL}", GDESK_URL, "API pública do GDesk (abre chamado)")
    _ensure_global_macro(api, "{$GDESK.QUEUE_ID}", GDESK_QUEUE_ID, "Fila de destino dos chamados do Zabbix no GDesk")
    _ensure_global_macro(api, "{$GDESK.APIKEY}", GDESK_API_KEY, "Chave da API pública do GDesk", secret=True)

    banner(f"2. Media type '{MEDIATYPE_NAME}'")
    mediatype_def = {
        "name": MEDIATYPE_NAME,
        "type": MEDIATYPE_TYPE_WEBHOOK,
        "status": "0",
        "script": WEBHOOK_SCRIPT,
        "timeout": "20s",
        "process_tags": "1",
        "description": "Abre chamado no GDesk (POST /api/public/tickets) no mesmo alerta que abre no Zendesk.",
        "parameters": parametros_webhook(),
        "message_templates": MESSAGE_TEMPLATES,
    }
    existing_mt = api.mediatype.get(output=["mediatypeid"], filter={"name": MEDIATYPE_NAME})
    try:
        if existing_mt:
            mediatypeid = existing_mt[0]["mediatypeid"]
            api.mediatype.update(mediatypeid=mediatypeid, **mediatype_def)
            info(f"Media type atualizado (ID {mediatypeid})")
        else:
            mediatypeid = api.mediatype.create(**mediatype_def)["mediatypeids"][0]
            ok(f"Media type criado → ID {mediatypeid}")
    except APIRequestError as exc:
        sys.exit(f"  [ERRO] mediatype: {exc}")

    banner(f"3. Mídia no usuário {USUARIO}")
    users = api.user.get(output=["userid"], selectMedias="extend", filter={"username": USUARIO})
    if not users:
        sys.exit(f"  [ERRO] Usuário '{USUARIO}' não encontrado")
    userid = users[0]["userid"]
    try:
        api.user.update(userid=userid, medias=_somar_midia(users[0].get("medias", []), mediatypeid, SENDTO))
        ok(f"Mídia GDesk adicionada (sendto cosmético '{SENDTO}')")
    except APIRequestError as exc:
        sys.exit(f"  [ERRO] user.update medias: {exc}")

    banner(f"4. Operação na ação {ACTION_ID}")
    actions = api.action.get(actionids=ACTION_ID, output=["name"], selectOperations="extend")
    if not actions:
        sys.exit(f"  [ERRO] Ação {ACTION_ID} não encontrada")
    operacoes = _somar_operacao(actions[0]["operations"], mediatypeid, userid)
    if operacoes is None:
        info("Ação já abre chamado no GDesk — nada a fazer")
    else:
        try:
            api.action.update(actionid=ACTION_ID, operations=operacoes)
            ok(f"Operação GDesk somada à ação '{actions[0]['name']}' (passo {ESC_STEP})")
        except APIRequestError as exc:
            sys.exit(f"  [ERRO] action.update: {exc}")

    banner("CONCLUÍDO")
    print(f"  Media type: {MEDIATYPE_NAME} (ID {mediatypeid})")
    print("  Teste: Alertas → Tipos de mídia → GDesk → Testar\n")


if __name__ == "__main__":
    main()
