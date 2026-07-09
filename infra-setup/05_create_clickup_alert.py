#!/usr/bin/env python3
"""
Configura o alerta automático ClickUp para a trigger de temperatura alta do
datacenter (criada por infra-setup/04_create_datacenter_temp_sensor.py).

Cria/reaproveita (idempotente):
  - Macros globais {$CLICKUP_TOKEN} e {$CLICKUP_LIST_ID} (lidas do .env)
  - Media type webhook "ClickUp" (reaproveitado se já existir — ver checagem
    no início do main())
  - Action "Datacenter — Alerta ClickUp (temperatura alta)" ligando a trigger
    "Datacenter: temperatura acima de 20°C" ao webhook, notificando o usuário
    Admin

Uso:
    python3 infra-setup/05_create_clickup_alert.py

Pré-requisitos: ZABBIX_URL/ZABBIX_TOKEN, CLICKUP_TOKEN, CLICKUP_LIST_ID no
.env (raiz do projeto). Rodar 04_create_datacenter_temp_sensor.py antes.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import urllib3

_env_vals: dict[str, str] = {}
_env_file = Path(__file__).resolve().parent.parent / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            _env_vals[k] = v
    for k, v in _env_vals.items():
        if k not in os.environ:
            os.environ[k] = v

ZABBIX_URL = os.environ.get("ZABBIX_URL", "http://172.29.2.11:8080/api_jsonrpc.php")
if "host.docker.internal" in ZABBIX_URL:
    ZABBIX_URL = "http://172.29.2.11:8080/api_jsonrpc.php"
if not ZABBIX_URL.endswith("/api_jsonrpc.php"):
    ZABBIX_URL = ZABBIX_URL.rstrip("/") + "/api_jsonrpc.php"
ZABBIX_TOKEN = os.environ.get("ZABBIX_TOKEN", "")
CLICKUP_TOKEN = os.environ.get("CLICKUP_TOKEN", "")
CLICKUP_LIST_ID = os.environ.get("CLICKUP_LIST_ID", "")
DASHBOARD_URL = os.environ.get("APP_PUBLIC_URL", "https://noc.grupogadens.com.br/gov/infra")

os.environ.pop("ZABBIX_USER", None)
os.environ.pop("ZABBIX_PASSWORD", None)

urllib3.disable_warnings()

try:
    from zabbix_utils import ZabbixAPI
    from zabbix_utils.exceptions import APIRequestError
except ImportError:
    sys.exit("Instale: pip install zabbix-utils --break-system-packages")

# ── Constantes ─────────────────────────────────────────────────────────────
TRIGGER_DESCR = "Datacenter: temperatura acima de 20°C"
MEDIATYPE_NAME = "ClickUp"
ACTION_NAME = "Datacenter — Alerta ClickUp (temperatura alta)"
ADMIN_ALIAS = "Admin"

MEDIATYPE_TYPE_WEBHOOK = "4"

# Script executado pelo Zabbix Server (sandbox JS) — recebe `value` como
# string JSON com os parâmetros definidos em `parameters` abaixo.
WEBHOOK_SCRIPT = """\
try {
    var params = JSON.parse(value);

    var req = new CurlHttpRequest();
    req.AddHeader('Content-Type: application/json');
    req.AddHeader('Authorization: ' + params.clickup_token);

    var body = JSON.stringify({
        name: params.subject,
        description: params.message
    });

    var resp = req.Post('https://api.clickup.com/api/v2/list/' + params.clickup_list_id + '/task', body);

    if (req.Status() >= 300) {
        throw 'ClickUp respondeu HTTP ' + req.Status() + ': ' + resp;
    }

    return 'OK';
} catch (error) {
    Zabbix.Log(4, '[ClickUp webhook] ' + error);
    throw 'ClickUp webhook falhou: ' + error;
}
"""


def banner(text: str) -> None:
    print(f"\n{'=' * 60}\n  {text}\n{'=' * 60}")


def ok(msg: str) -> None:
    print(f"  [OK] {msg}")


def info(msg: str) -> None:
    print(f"  [--] {msg}")


def warn(msg: str) -> None:
    print(f"  [!!] {msg}")


def _ensure_global_macro(api: ZabbixAPI, macro: str, value: str, description: str) -> None:
    existing = api.usermacro.get(globalmacro=True, output="extend", filter={"macro": macro})
    if existing:
        api.usermacro.updateglobal(globalmacroid=existing[0]["globalmacroid"], value=value, description=description)
        info(f"Macro global atualizada: {macro}")
    else:
        api.usermacro.createglobal(macro=macro, value=value, description=description)
        ok(f"Macro global criada: {macro}")


def main() -> None:
    banner("Alerta ClickUp — Temperatura do Datacenter")

    if not CLICKUP_TOKEN or not CLICKUP_LIST_ID:
        sys.exit("  [ERRO] CLICKUP_TOKEN e/ou CLICKUP_LIST_ID não configurados no .env")

    api = ZabbixAPI(url=ZABBIX_URL, token=ZABBIX_TOKEN, skip_version_check=True)
    print(f"  Zabbix {api.api_version()} conectado")

    # ── 1. Macros globais ────────────────────────────────────────────────────
    banner("1. Macros globais {$CLICKUP_TOKEN} / {$CLICKUP_LIST_ID}")
    _ensure_global_macro(api, "{$CLICKUP_TOKEN}", CLICKUP_TOKEN, "Token de API do ClickUp (webhook de alertas)")
    _ensure_global_macro(
        api, "{$CLICKUP_LIST_ID}", CLICKUP_LIST_ID, "Lista ClickUp onde os chamados de alerta são criados"
    )

    # ── 2. Media type webhook ClickUp (reaproveita se já existir) ───────────
    banner("2. Media type webhook 'ClickUp'")
    existing_mt = api.mediatype.get(output=["mediatypeid", "name"], filter={"name": MEDIATYPE_NAME})

    mediatype_def = {
        "name": MEDIATYPE_NAME,
        "type": MEDIATYPE_TYPE_WEBHOOK,
        "status": "0",  # habilitado
        "script": WEBHOOK_SCRIPT,
        "description": "Cria uma tarefa no ClickUp a partir de um alerta do Zabbix.",
        "parameters": [
            {"name": "clickup_token", "value": "{$CLICKUP_TOKEN}"},
            {"name": "clickup_list_id", "value": "{$CLICKUP_LIST_ID}"},
            {"name": "subject", "value": "{ALERT.SUBJECT}"},
            {"name": "message", "value": "{ALERT.MESSAGE}"},
        ],
    }

    if existing_mt:
        mediatypeid = existing_mt[0]["mediatypeid"]
        try:
            api.mediatype.update(mediatypeid=mediatypeid, **mediatype_def)
            ok(f"Media type reaproveitado/atualizado: {MEDIATYPE_NAME} (ID {mediatypeid})")
        except APIRequestError as exc:
            warn(f"update mediatype: {exc}")
    else:
        try:
            result = api.mediatype.create(**mediatype_def)
            mediatypeid = result["mediatypeids"][0]
            ok(f"Media type criado: {MEDIATYPE_NAME} → ID {mediatypeid}")
        except APIRequestError as exc:
            sys.exit(f"  [ERRO] create mediatype: {exc}")

    # ── 3. Usuário Admin recebe a media type ─────────────────────────────────
    banner("3. Associar media type ao usuário Admin")
    users = api.user.get(output=["userid", "username"], selectMedias=["mediatypeid"], filter={"username": ADMIN_ALIAS})
    if not users:
        sys.exit(f"  [ERRO] Usuário '{ADMIN_ALIAS}' não encontrado no Zabbix")
    admin_userid = users[0]["userid"]
    ja_configurado = any(m["mediatypeid"] == mediatypeid for m in users[0].get("medias", []))

    if ja_configurado:
        info(f"Usuário {ADMIN_ALIAS} já possui a media type ClickUp")
    else:
        medias = [{"mediatypeid": m["mediatypeid"]} for m in users[0].get("medias", [])]
        medias.append(
            {
                "mediatypeid": mediatypeid,
                "sendto": "clickup",  # webhook não usa endereço real, mas o campo é obrigatório
                "active": "0",
                "severity": "63",  # todas as severidades
                "period": "1-7,00:00-24:00",
            }
        )
        try:
            api.user.update(userid=admin_userid, medias=medias)
            ok(f"Media type ClickUp associada ao usuário {ADMIN_ALIAS}")
        except APIRequestError as exc:
            sys.exit(f"  [ERRO] user.update medias: {exc}")

    # ── 4. Action ligando a trigger ao webhook ───────────────────────────────
    banner("4. Action de notificação")
    triggers = api.trigger.get(output=["triggerid", "description"], filter={"description": TRIGGER_DESCR})
    if not triggers:
        sys.exit(
            f"  [ERRO] Trigger '{TRIGGER_DESCR}' não encontrada — "
            "rode infra-setup/04_create_datacenter_temp_sensor.py primeiro"
        )
    triggerid = triggers[0]["triggerid"]

    existing_actions = api.action.get(output=["actionid", "name"], filter={"name": ACTION_NAME})

    action_def = {
        "name": ACTION_NAME,
        "eventsource": "0",  # trigger
        "status": "0",  # habilitada
        "esc_period": "1h",
        "filter": {
            "evaltype": "0",  # AND/OR (condição única)
            "conditions": [
                {"conditiontype": "2", "operator": "0", "value": triggerid},  # Trigger = esta
            ],
        },
        "operations": [
            {
                "operationtype": "0",  # send message
                "esc_period": "0",
                "esc_step_from": 1,
                "esc_step_to": 1,
                "opmessage_usr": [{"userid": admin_userid}],
                "opmessage": {
                    "default_msg": "0",
                    "mediatypeid": mediatypeid,
                    "subject": "🌡️ Alerta: Temperatura Datacenter {ITEM.LASTVALUE}°C",
                    "message": (
                        "🌡️ Temperatura do datacenter em {ITEM.LASTVALUE}°C — limite: 20°C\n"
                        "Horário: {EVENT.DATE} {EVENT.TIME}\n"
                        f"Dashboard: {DASHBOARD_URL}"
                    ),
                },
            }
        ],
    }

    if existing_actions:
        actionid = existing_actions[0]["actionid"]
        try:
            api.action.update(actionid=actionid, **action_def)
            ok(f"Action atualizada: {ACTION_NAME} (ID {actionid})")
        except APIRequestError as exc:
            warn(f"update action: {exc}")
    else:
        try:
            result = api.action.create(**action_def)
            actionid = result["actionids"][0]
            ok(f"Action criada: {ACTION_NAME} → ID {actionid}")
        except APIRequestError as exc:
            sys.exit(f"  [ERRO] create action: {exc}")

    banner("CONCLUÍDO")
    print(f"  Media type: {MEDIATYPE_NAME} (ID {mediatypeid})")
    print(f"  Action: {ACTION_NAME} (ID {actionid})")
    print(f"  Ao disparar, cria tarefa na lista ClickUp {CLICKUP_LIST_ID}\n")


if __name__ == "__main__":
    main()
