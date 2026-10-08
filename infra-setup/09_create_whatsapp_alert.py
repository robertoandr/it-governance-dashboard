#!/usr/bin/env python3
"""
Liga o alerta WhatsApp (Evolution API) à ação 12 "Governança — Chamado Zendesk".

Cria/reaproveita (idempotente):
  - Macros globais {$EVO.URL} e {$EVO.APIKEY} (secreta)
  - Media type webhook "WhatsApp (Evolution)" — POST /message/sendText na
    instância `alertas-zabbix` (chip 41 98903-8337). O texto é montado em
    português simples por whatsapp/alerta.js; fora das 08:00–19:00 o envio
    fica para o resumo da manhã (10_create_whatsapp_resumo.py)
  - Mídia no usuário `alertas-integracao` apontando para o grupo de destino
  - Operação na ação 12 (mesmo passo 2 do Zendesk, ou seja, só alerta se o
    problema durar >= 5 min). Recovery/update já são "notificar todos os
    envolvidos", então a resolução também chega no grupo.

Uso:
    python3 infra-setup/09_create_whatsapp_alert.py

Pré-requisitos: ZABBIX_URL/ZABBIX_TOKEN no .env (raiz do projeto) e
EVO_API_KEY no ambiente ou em ~/evolution/.env. Grupo de destino em
WHATSAPP_GRUPO_JID (padrão: grupo "TI").
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


for _arquivo in (Path(__file__).resolve().parent.parent / ".env", Path.home() / "evolution" / ".env"):
    for _k, _v in _ler_env(_arquivo).items():
        os.environ.setdefault(_k, _v)

ZABBIX_URL = os.environ.get("ZABBIX_URL", "http://172.29.2.11:5443/api_jsonrpc.php")
if "host.docker.internal" in ZABBIX_URL:
    ZABBIX_URL = "http://172.29.2.11:5443/api_jsonrpc.php"
if not ZABBIX_URL.endswith("/api_jsonrpc.php"):
    ZABBIX_URL = ZABBIX_URL.rstrip("/") + "/api_jsonrpc.php"
ZABBIX_TOKEN = os.environ.get("ZABBIX_TOKEN", "")
EVO_API_KEY = os.environ.get("EVO_API_KEY", "")
GRUPO_JID = os.environ.get("WHATSAPP_GRUPO_JID", "554196491150-1438869877@g.us")

os.environ.pop("ZABBIX_USER", None)
os.environ.pop("ZABBIX_PASSWORD", None)

urllib3.disable_warnings()

try:
    from zabbix_utils import ZabbixAPI
    from zabbix_utils.exceptions import APIRequestError
except ImportError:
    sys.exit("Instale: pip install zabbix-utils --break-system-packages")

# ── Constantes ─────────────────────────────────────────────────────────────
MEDIATYPE_NAME = "WhatsApp (Evolution)"
ACTION_ID = "12"
USUARIO = "alertas-integracao"
EVO_URL = "http://evolution-api:8080"  # rede Docker compartilhada com o zabbix_server
EVO_INSTANCIA = "alertas-zabbix"
ESC_STEP = 2  # mesmo passo da operação Zendesk (esc_period 5m na ação)

MEDIATYPE_TYPE_WEBHOOK = "4"

# Janela de envio (horário de Brasília, sem horário de verão). Fora dela o
# webhook não envia e o alerta entra no resumo das 08:00 (script 10).
JANELA_INICIO = "08:00"
JANELA_FIM = "19:00"
FUSO_HORAS = "-3"

# Sandbox JS do Zabbix Server — `value` é o JSON dos `parameters` abaixo.
_JS = Path(__file__).resolve().parent / "whatsapp"
WEBHOOK_SCRIPT = "\n".join((_JS / nome).read_text(encoding="utf-8") for nome in ("comum.js", "alerta.js"))

LINK_EVENTO = "{$ZABBIX.URL}/tr_events.php?triggerid={TRIGGER.ID}&eventid={EVENT.ID}"

# eventsource 0 = trigger; recovery 0/1/2 = problema/resolução/atualização
MESSAGE_TEMPLATES = [
    {
        "eventsource": "0",
        "recovery": "0",
        "subject": "🔴 {EVENT.SEVERITY}: {EVENT.NAME}",
        "message": (
            "Host: {HOST.NAME}\n"
            "Início: {EVENT.DATE} {EVENT.TIME} (há {EVENT.AGE})\n"
            "Valor: {EVENT.OPDATA}\n"
            "Evento #{EVENT.ID} — chamado aberto no Zendesk\n"
            f"{LINK_EVENTO}"
        ),
    },
    {
        "eventsource": "0",
        "recovery": "1",
        "subject": "✅ Resolvido: {EVENT.NAME}",
        "message": (
            "Host: {HOST.NAME}\n"
            "Resolvido em {EVENT.RECOVERY.DATE} {EVENT.RECOVERY.TIME} (duração {EVENT.DURATION})\n"
            "Evento #{EVENT.ID}"
        ),
    },
    {
        "eventsource": "0",
        "recovery": "2",
        "subject": "📝 Atualização: {EVENT.NAME}",
        "message": "{USER.FULLNAME} {EVENT.UPDATE.ACTION}\n{EVENT.UPDATE.MESSAGE}\nEvento #{EVENT.ID}",
    },
]


def banner(text: str) -> None:
    print(f"\n{'=' * 60}\n  {text}\n{'=' * 60}")


def ok(msg: str) -> None:
    print(f"  [OK] {msg}")


def info(msg: str) -> None:
    print(f"  [--] {msg}")


def _somar_midia(medias: list[dict], mediatypeid: str, sendto: str) -> list[dict]:
    """Devolve as mídias do usuário com a do WhatsApp incluída/atualizada.

    Mantém as demais mídias (Zendesk etc.) sem os campos somente-leitura que
    o user.update recusa.
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
    """Devolve as operações da ação com o envio WhatsApp somado, ou None se já existe.

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
    banner("Alerta WhatsApp (Evolution) — ação 12")

    if not EVO_API_KEY:
        sys.exit("  [ERRO] EVO_API_KEY não encontrada (ambiente ou ~/evolution/.env)")

    api = ZabbixAPI(url=ZABBIX_URL, token=ZABBIX_TOKEN, skip_version_check=True)
    print(f"  Zabbix {api.api_version()} conectado — destino {GRUPO_JID}")

    banner("1. Macros globais {$EVO.URL} / {$EVO.APIKEY}")
    _ensure_global_macro(api, "{$EVO.URL}", EVO_URL, "Evolution API (WhatsApp) na rede Docker")
    _ensure_global_macro(api, "{$EVO.APIKEY}", EVO_API_KEY, "Chave global da Evolution API", secret=True)

    banner(f"2. Media type '{MEDIATYPE_NAME}'")
    mediatype_def = {
        "name": MEDIATYPE_NAME,
        "type": MEDIATYPE_TYPE_WEBHOOK,
        "status": "0",
        "script": WEBHOOK_SCRIPT,
        "timeout": "15s",
        "description": "Envia o alerta para um grupo de WhatsApp pela Evolution API (instância alertas-zabbix).",
        "parameters": [
            {"name": "evo_url", "value": "{$EVO.URL}"},
            {"name": "evo_apikey", "value": "{$EVO.APIKEY}"},
            {"name": "instance", "value": EVO_INSTANCIA},
            {"name": "to", "value": "{ALERT.SENDTO}"},
            {"name": "severity", "value": "{EVENT.NSEVERITY}"},
            {"name": "event_name", "value": "{EVENT.NAME}"},
            {"name": "host", "value": "{HOST.NAME}"},
            {"name": "event_value", "value": "{EVENT.VALUE}"},
            {"name": "update_status", "value": "{EVENT.UPDATE.STATUS}"},
            {"name": "update_user", "value": "{USER.FULLNAME}"},
            {"name": "update_action", "value": "{EVENT.UPDATE.ACTION}"},
            {"name": "update_message", "value": "{EVENT.UPDATE.MESSAGE}"},
            {"name": "opdata", "value": "{EVENT.OPDATA}"},
            {"name": "start_time", "value": "{EVENT.TIME}"},
            {"name": "recovery_time", "value": "{EVENT.RECOVERY.TIME}"},
            {"name": "duration", "value": "{EVENT.DURATION}"},
            {"name": "link", "value": LINK_EVENTO},
            {"name": "janela_inicio", "value": JANELA_INICIO},
            {"name": "janela_fim", "value": JANELA_FIM},
            {"name": "fuso_horas", "value": FUSO_HORAS},
        ],
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
        api.user.update(userid=userid, medias=_somar_midia(users[0].get("medias", []), mediatypeid, GRUPO_JID))
        ok(f"Mídia WhatsApp → {GRUPO_JID}")
    except APIRequestError as exc:
        sys.exit(f"  [ERRO] user.update medias: {exc}")

    banner(f"4. Operação na ação {ACTION_ID}")
    actions = api.action.get(actionids=ACTION_ID, output=["name"], selectOperations="extend")
    if not actions:
        sys.exit(f"  [ERRO] Ação {ACTION_ID} não encontrada")
    operacoes = _somar_operacao(actions[0]["operations"], mediatypeid, userid)
    if operacoes is None:
        info("Ação já envia pelo WhatsApp — nada a fazer")
    else:
        try:
            api.action.update(actionid=ACTION_ID, operations=operacoes)
            ok(f"Operação WhatsApp somada à ação '{actions[0]['name']}' (passo {ESC_STEP})")
        except APIRequestError as exc:
            sys.exit(f"  [ERRO] action.update: {exc}")

    banner("CONCLUÍDO")
    print(f"  Media type: {MEDIATYPE_NAME} (ID {mediatypeid})")
    print("  Teste: Alertas → Tipos de mídia → WhatsApp (Evolution) → Testar\n")


if __name__ == "__main__":
    main()
