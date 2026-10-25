#!/usr/bin/env python3
"""
Cyber: achado grave do agente SIEM → aviso no WhatsApp, sem chamado.

O agente SIEM do dashboard (``app/services/siem_agente.py``) analisa os logs
dos FortiGate a cada 15 minutos e manda ao Zabbix, no item trapper
``cyber.siem.grave``, o texto dos achados graves (crítica/alta) ainda ativos;
vazio = nada grave. Cria/ajusta (idempotente):

  - Grupo "Cyber" e host "cyber-siem-dashboard" (sem interface: só recebe
    valores do dashboard).
  - Item trapper ``cyber.siem.grave`` (texto, 30 dias de histórico).
  - Trigger "Cyber SIEM: {ITEM.LASTVALUE}" (Alta) com a tag
    ``alerta=so-whatsapp``: entra na ação de WhatsApp criada pelo script 12
    (que filtra por essa tag) e fica fora da ação de chamado Zendesk. Some
    sozinho quando o agente manda texto vazio.

Requer o script 12 já aplicado (ação só WhatsApp e exclusão na de chamado).

Uso:
    uv run --no-project --with zabbix-utils --with urllib3 python3 infra-setup/13_cyber_siem.py [--dry-run]
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

_AQUI = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("whatsapp_alert_script", _AQUI / "09_create_whatsapp_alert.py")
wa = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(wa)  # type: ignore[union-attr]

from zabbix_utils import ZabbixAPI  # noqa: E402
from zabbix_utils.exceptions import APIRequestError  # noqa: E402

GRUPO = "Cyber"
HOST = "cyber-siem-dashboard"
HOST_NOME = "Cyber SIEM (dashboard)"
CHAVE = "cyber.siem.grave"
TRIGGER = "Cyber SIEM: {ITEM.LASTVALUE}"
ACAO_WHATSAPP = "CFTV — Sem gravar/sem imagem (só WhatsApp)"  # filtra só pela tag alerta=so-whatsapp
TAG_ALERTA = ("alerta", "so-whatsapp")
SEVERIDADE_ALTA = "4"


def expr_trigger() -> str:
    """Problema enquanto o último texto enviado pelo agente não estiver vazio."""
    return f"length(last(/{HOST}/{CHAVE}))>0"


def garantir_grupo(api: ZabbixAPI, dry: bool) -> str | None:
    """Grupo de hosts "Cyber". Devolve o groupid."""
    existente = api.hostgroup.get(filter={"name": GRUPO}, output=["groupid"])
    if existente:
        wa.ok(f"grupo '{GRUPO}' já existe")
        return existente[0]["groupid"]
    if dry:
        wa.info(f"criaria o grupo '{GRUPO}'")
        return None
    groupid = api.hostgroup.create(name=GRUPO)["groupids"][0]
    wa.ok(f"grupo '{GRUPO}' criado ({groupid})")
    return groupid


def garantir_host(api: ZabbixAPI, groupid: str | None, dry: bool) -> str | None:
    """Host sem interface que recebe os valores do agente. Devolve o hostid."""
    existente = api.host.get(filter={"host": HOST}, output=["hostid"])
    if existente:
        wa.ok(f"host '{HOST_NOME}' já existe")
        return existente[0]["hostid"]
    if dry or groupid is None:
        wa.info(f"criaria o host '{HOST_NOME}' ({HOST})")
        return None
    hostid = api.host.create(
        host=HOST,
        name=HOST_NOME,
        groups=[{"groupid": groupid}],
        description="Recebe do dashboard (agente SIEM do módulo Cyber) os achados graves. Ver infra-setup/13_cyber_siem.py.",
        tags=[{"tag": "componente", "value": "cyber"}],
    )["hostids"][0]
    wa.ok(f"host '{HOST_NOME}' criado ({hostid})")
    return hostid


def garantir_item_e_trigger(api: ZabbixAPI, hostid: str | None, dry: bool) -> None:
    """Item trapper e trigger (o que faltar)."""
    if hostid is None:
        wa.info(f"criaria o item {CHAVE} e o trigger '{TRIGGER}'")
        return
    if api.item.get(hostids=[hostid], filter={"key_": CHAVE}, output=["itemid"]):
        wa.ok(f"item {CHAVE} já existe")
    elif dry:
        wa.info(f"criaria o item {CHAVE}")
    else:
        api.item.create(
            hostid=hostid,
            name="Achados graves do agente SIEM",
            key_=CHAVE,
            type=2,  # trapper
            value_type=4,  # texto
            history="30d",
            description="Enviado pelo dashboard a cada análise do agente SIEM. Vazio = nada grave.",
            tags=[{"tag": "componente", "value": "cyber"}],
        )
        wa.ok(f"item {CHAVE} criado")

    if api.trigger.get(hostids=[hostid], filter={"description": TRIGGER}, output=["triggerid"]):
        wa.ok(f"trigger '{TRIGGER}' já existe")
        return
    if dry:
        wa.info(f"criaria o trigger '{TRIGGER}'")
        return
    api.trigger.create(
        description=TRIGGER,
        expression=expr_trigger(),
        priority=SEVERIDADE_ALTA,
        manual_close=1,
        comments="Agente SIEM do dashboard (/gov/cyber, aba Agente SIEM). Some quando não há achado grave ativo.",
        tags=[
            {"tag": TAG_ALERTA[0], "value": TAG_ALERTA[1]},
            {"tag": "componente", "value": "cyber"},
            {"tag": "problema", "value": "siem"},
        ],
    )
    wa.ok(f"trigger '{TRIGGER}' criado")


def conferir_acao(api: ZabbixAPI) -> None:
    """Avisa se a ação de WhatsApp do script 12 não existe (o trigger ficaria sem aviso)."""
    if not api.action.get(filter={"name": ACAO_WHATSAPP}, output=["actionid"]):
        sys.exit(f"  [ERRO] ação '{ACAO_WHATSAPP}' não encontrada: rode antes infra-setup/12_cftv_verificacao.py")
    wa.ok(f"ação '{ACAO_WHATSAPP}' existe (filtra por alerta=so-whatsapp)")


def main() -> None:
    """Aplica (ou só mostra, com ``--dry-run``) grupo, host, item e trigger do agente SIEM."""
    parser = argparse.ArgumentParser(description="Cyber: achado grave do agente SIEM → WhatsApp, sem chamado.")
    parser.add_argument("--dry-run", action="store_true", help="só mostra o que mudaria")
    dry = parser.parse_args().dry_run
    wa.banner("Cyber: agente SIEM" + (" (DRY-RUN)" if dry else ""))
    api = ZabbixAPI(url=wa.ZABBIX_URL, token=wa.ZABBIX_TOKEN, skip_version_check=True)
    print(f"  Zabbix {api.api_version()} conectado")
    try:
        conferir_acao(api)
        garantir_item_e_trigger(api, garantir_host(api, garantir_grupo(api, dry), dry), dry)
    except APIRequestError as exc:
        sys.exit(f"  [ERRO] {exc}")
    wa.banner("CONCLUÍDO" + (" (nada foi gravado)" if dry else ""))


if __name__ == "__main__":
    main()
