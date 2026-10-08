#!/usr/bin/env python3
"""
Resumo da manhã no WhatsApp: o que chegou fora do horário vai num bloco só.

O webhook "WhatsApp (Evolution)" (script 09) só envia entre 08:00 e 19:00.
Fora disso o alerta fica registrado no Zabbix sem ir para o grupo, e este
item, agendado para as 08:00, junta os alertas da noite e manda uma mensagem
só. Cria/reaproveita (idempotente):

  - Token de API "whatsapp-resumo" do usuário `alertas-integracao` (leitura em
    todos os hostgroups), guardado na macro global secreta {$WHATSAPP.RESUMO.TOKEN}
  - Item Script `whatsapp.resumo.manha` no host "Zabbix server", agendado
    para o início da janela (h8m0)
  - Trigger avisando se o resumo não sair (item sem valor por 25 h)

Uso (depois do script 09):
    uv run --no-project --with zabbix-utils --with urllib3 python3 infra-setup/10_create_whatsapp_resumo.py

Para testar fora das 08:00: Coleta de dados → Itens → "Resumo da manhã no
WhatsApp" → Executar agora (manda o resumo da última noite).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_AQUI = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("whatsapp_alert_script", _AQUI / "09_create_whatsapp_alert.py")
wa = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(wa)  # type: ignore[union-attr]

from zabbix_utils import ZabbixAPI  # noqa: E402
from zabbix_utils.exceptions import APIRequestError  # noqa: E402

HOST = "Zabbix server"
ITEM_KEY = "whatsapp.resumo.manha"
ITEM_NOME = "Resumo da manhã no WhatsApp"
TOKEN_NOME = "whatsapp-resumo"  # noqa: S105 — nome do token, não o segredo
TOKEN_MACRO = "{$WHATSAPP.RESUMO.TOKEN}"  # noqa: S105 — nome da macro
ITEM_TYPE_SCRIPT = "21"
VALUE_TYPE_TEXT = "4"

ITEM_SCRIPT = "\n".join((_AQUI / "whatsapp" / nome).read_text(encoding="utf-8") for nome in ("comum.js", "resumo.js"))


def agendamento(janela_inicio: str) -> str:
    """Intervalo do item: só o agendamento diário no início da janela ("08:00" → "0;h8m0")."""
    hora, minuto = (int(x) for x in janela_inicio.split(":"))
    return f"0;h{hora}m{minuto}"


def parametros_item(mediatypeid: str) -> list[dict[str, str]]:
    """Parâmetros do item Script (o JSON que chega em `value` no resumo.js)."""
    return [
        {"name": "api_url", "value": "{$ZABBIX.URL}/api_jsonrpc.php"},
        {"name": "api_token", "value": TOKEN_MACRO},
        {"name": "acao_id", "value": wa.ACTION_ID},
        {"name": "mediatype_id", "value": mediatypeid},
        {"name": "evo_url", "value": "{$EVO.URL}"},
        {"name": "evo_apikey", "value": "{$EVO.APIKEY}"},
        {"name": "instance", "value": wa.EVO_INSTANCIA},
        {"name": "to", "value": wa.GRUPO_JID},
        {"name": "janela_inicio", "value": wa.JANELA_INICIO},
        {"name": "janela_fim", "value": wa.JANELA_FIM},
        {"name": "fuso_horas", "value": wa.FUSO_HORAS},
    ]


def _token(api: ZabbixAPI, userid: str) -> str:
    """Gera (ou regenera) o token do usuário de integração e devolve o segredo."""
    existentes = api.token.get(output=["tokenid"], userids=[userid], filter={"name": TOKEN_NOME})
    if existentes:
        tokenid = existentes[0]["tokenid"]
        wa.info(f"Token '{TOKEN_NOME}' já existe (ID {tokenid}) — regenerando")
    else:
        tokenid = api.token.create(
            name=TOKEN_NOME, userid=userid, description="Resumo da manhã no WhatsApp (item Script)"
        )["tokenids"][0]
        wa.ok(f"Token '{TOKEN_NOME}' criado (ID {tokenid})")
    return api.token.generate(tokenid)[0]["token"]


def main() -> None:
    wa.banner("Resumo da manhã no WhatsApp")
    api = ZabbixAPI(url=wa.ZABBIX_URL, token=wa.ZABBIX_TOKEN, skip_version_check=True)
    print(f"  Zabbix {api.api_version()} conectado — janela {wa.JANELA_INICIO}–{wa.JANELA_FIM}")

    mt = api.mediatype.get(output=["mediatypeid"], filter={"name": wa.MEDIATYPE_NAME})
    if not mt:
        sys.exit(f"  [ERRO] Media type '{wa.MEDIATYPE_NAME}' não existe — rode o script 09 antes")
    mediatypeid = mt[0]["mediatypeid"]

    wa.banner(f"1. Token de API do usuário {wa.USUARIO}")
    users = api.user.get(output=["userid"], filter={"username": wa.USUARIO})
    if not users:
        sys.exit(f"  [ERRO] Usuário '{wa.USUARIO}' não encontrado")
    try:
        segredo = _token(api, users[0]["userid"])
    except APIRequestError as exc:
        sys.exit(f"  [ERRO] token: {exc}")
    wa._ensure_global_macro(api, TOKEN_MACRO, segredo, "Token do resumo da manhã no WhatsApp", secret=True)

    wa.banner(f"2. Item '{ITEM_NOME}' em {HOST}")
    hosts = api.host.get(output=["hostid"], filter={"host": HOST})
    if not hosts:
        sys.exit(f"  [ERRO] Host '{HOST}' não encontrado")
    hostid = hosts[0]["hostid"]
    item_def = {
        "name": ITEM_NOME,
        "type": ITEM_TYPE_SCRIPT,
        "value_type": VALUE_TYPE_TEXT,
        "delay": agendamento(wa.JANELA_INICIO),
        "timeout": "60s",
        "history": "30d",
        "params": ITEM_SCRIPT,
        "parameters": parametros_item(mediatypeid),
        "description": "Manda no grupo do WhatsApp os alertas que chegaram fora do horário (script 10).",
    }
    existente = api.item.get(output=["itemid"], hostids=[hostid], filter={"key_": ITEM_KEY})
    try:
        if existente:
            itemid = existente[0]["itemid"]
            api.item.update(itemid=itemid, **item_def)
            wa.info(f"Item atualizado (ID {itemid})")
        else:
            itemid = api.item.create(hostid=hostid, key_=ITEM_KEY, **item_def)["itemids"][0]
            wa.ok(f"Item criado → ID {itemid}")
    except APIRequestError as exc:
        sys.exit(f"  [ERRO] item: {exc}")

    wa.banner("3. Trigger de resumo não enviado")
    descricao = "Resumo da manhã do WhatsApp não foi enviado"
    if api.trigger.get(output=["triggerid"], hostids=[hostid], filter={"description": descricao}):
        wa.info("Trigger já existe")
    else:
        try:
            api.trigger.create(
                description=descricao,
                expression=f"nodata(/{HOST}/{ITEM_KEY},25h)=1",
                priority="2",
                comments="O item só recebe valor quando o resumo sai. Veja o erro do item em Coleta de dados → Itens.",
            )
            wa.ok("Trigger criada")
        except APIRequestError as exc:
            sys.exit(f"  [ERRO] trigger: {exc}")

    wa.banner("CONCLUÍDO")
    print(f"  Item {ITEM_KEY} (ID {itemid}) roda todo dia às {wa.JANELA_INICIO}")
    print("  Teste: Coleta de dados → Itens → Resumo da manhã no WhatsApp → Executar agora\n")


if __name__ == "__main__":
    main()
