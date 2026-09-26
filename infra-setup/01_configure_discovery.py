#!/usr/bin/env python3
"""
Configura Network Discovery de infraestrutura no Zabbix.

O que faz:
  1. Cria/atualiza a discovery rule "Scan Rede Corporativa" com checks completos
     (ICMP, Zabbix agent :10050, SSH :22, RDP :3389, SNMPv2 :161, HTTPS :443)
     sobre INFRA_SCAN_RANGES (padrão 172.29.0.0/22, o mesmo do collector)
  2. Cria/atualiza actions de auto-classificação:
     - Zabbix agent detectado  → grupo Linux servers  + template Linux by Zabbix agent
     - SNMP detectado          → grupo Firewall        + template Network Generic Device by SNMP
     - SSH sem agent           → grupo Servidores      + template ICMP ping
     - RDP detectado           → grupo Servidores      + template ICMP ping (Windows)
     - ICMP only               → grupo Discovered hosts (aguarda agente)
  3. Cria a discovery rule "Auto-reg: Zabbix Agent" para hosts que já
     têm o agente instalado e se auto-registram.

Uso:
    python3 infra-setup/01_configure_discovery.py

Requer: pip install zabbix-utils
Variáveis: ZABBIX_URL, ZABBIX_TOKEN, INFRA_SCAN_RANGES (lidas do .env ou ambiente)

Grupos, templates e a discovery rule são resolvidos pelo NOME (zbx_lookup.py):
os IDs mudam a cada instalação do Zabbix.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import urllib3

# Carregar .env manualmente (sem source) — última ocorrência de cada chave prevalece
_env_file = Path(__file__).resolve().parent.parent / ".env"
if _env_file.exists():
    _env_vals: dict[str, str] = {}
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            _env_vals[k] = v  # última ocorrência prevalece
    for k, v in _env_vals.items():
        if k not in os.environ:
            os.environ[k] = v

# Usar IP direto — host.docker.internal não resolve fora do Docker
ZABBIX_URL = os.environ.get("ZABBIX_URL", "http://172.29.2.11:8080/api_jsonrpc.php")
if "host.docker.internal" in ZABBIX_URL:
    ZABBIX_URL = "http://172.29.2.11:8080/api_jsonrpc.php"
ZABBIX_TOKEN = os.environ.get("ZABBIX_TOKEN", "")

if not ZABBIX_TOKEN:
    sys.exit("ZABBIX_TOKEN não definido. Verifique .env ou exporte a variável.")

# zabbix_utils lê ZABBIX_USER/ZABBIX_PASSWORD do ambiente automaticamente.
# Se ambos estiverem definidos junto com ZABBIX_TOKEN, levanta ProcessingError.
# Removemos temporariamente para forçar autenticação exclusiva por token.
os.environ.pop("ZABBIX_USER", None)
os.environ.pop("ZABBIX_PASSWORD", None)

urllib3.disable_warnings()

try:
    import zbx_lookup
    from zabbix_utils import ZabbixAPI
    from zabbix_utils.exceptions import APIRequestError
except ImportError:
    sys.exit("Instale: pip install zabbix-utils --break-system-packages")

# ── Discovery rule ────────────────────────────────────────────────────────────
DRULE_CORPORATIVA = "Scan Rede Corporativa"
SCAN_RANGES = os.environ.get("INFRA_SCAN_RANGES", "172.29.0.0/22")

# ── Zabbix check types ────────────────────────────────────────────────────────
TYPE_SSH = 0  # TCP port check on SSH
TYPE_LDAP = 1
TYPE_TCP = 8  # Generic TCP
TYPE_AGENT = 9  # Zabbix agent
TYPE_SNMPV2 = 11  # SNMPv2c
TYPE_ICMP = 12  # ICMP ping
TYPE_HTTPS = 14
TYPE_TELNET = 15

# ── Templates e grupos (por nome; IDs resolvidos em runtime) ─────────────────
TMPL_LINUX_AGENT = "Linux by Zabbix agent"
TMPL_NETWORK_SNMP = "Network Generic Device by SNMP"
TMPL_ICMP_PING = "ICMP Ping"

GRP_LINUX = "Linux servers"
GRP_SERVIDORES = "Servidores"
GRP_FIREWALL = "Firewall"
GRP_DISCOVERED = "Discovered hosts"


def banner(text: str) -> None:
    print(f"\n{'=' * 60}\n  {text}\n{'=' * 60}")


def ok(msg: str) -> None:
    print(f"  [OK] {msg}")


def info(msg: str) -> None:
    print(f"  [--] {msg}")


def main() -> None:
    banner("Configuração de Network Discovery — Infraestrutura")

    api = ZabbixAPI(url=ZABBIX_URL, token=ZABBIX_TOKEN, skip_version_check=True)
    print(f"  Zabbix {api.api_version()} conectado")

    ids = {nome: zbx_lookup.grupo(api, nome) for nome in (GRP_LINUX, GRP_SERVIDORES, GRP_FIREWALL, GRP_DISCOVERED)}
    ids |= {nome: zbx_lookup.template(api, nome) for nome in (TMPL_LINUX_AGENT, TMPL_NETWORK_SNMP, TMPL_ICMP_PING)}

    # ── 1. Criar/atualizar discovery rule com checks completos ────────────────
    banner(f"1. Discovery rule '{DRULE_CORPORATIVA}' ({SCAN_RANGES})")

    new_checks = [
        # ICMP ping — detecta qualquer host ativo
        {"type": str(TYPE_ICMP), "ports": "0", "key_": ""},
        # Zabbix agent — identifica hosts gerenciados
        {"type": str(TYPE_AGENT), "ports": "10050", "key_": "system.uname"},
        # SSH — identifica servidores Linux/Unix sem agente
        {"type": str(TYPE_TCP), "ports": "22", "key_": ""},
        # RDP — identifica servidores Windows
        {"type": str(TYPE_TCP), "ports": "3389", "key_": ""},
        # SNMP v2c — identifica switches, roteadores, firewalls (OID sysDescr)
        {"type": str(TYPE_SNMPV2), "ports": "161", "key_": ".1.3.6.1.2.1.1.1.0", "snmp_community": "public"},
        # HTTPS — identifica appliances web e servidores de aplicação
        {"type": str(TYPE_TCP), "ports": "443", "key_": ""},
        # MySQL / MariaDB
        {"type": str(TYPE_TCP), "ports": "3306", "key_": ""},
        # PostgreSQL
        {"type": str(TYPE_TCP), "ports": "5432", "key_": ""},
    ]

    drule_def = {
        "iprange": SCAN_RANGES,
        "dchecks": new_checks,
        "status": "0",  # enabled
        "delay": "1h",  # escanear a cada hora
    }
    existentes = api.drule.get(output=["druleid"], filter={"name": DRULE_CORPORATIVA})
    try:
        if existentes:
            ids[DRULE_CORPORATIVA] = existentes[0]["druleid"]
            api.drule.update(druleid=ids[DRULE_CORPORATIVA], **drule_def)
            ok(f"Discovery rule {ids[DRULE_CORPORATIVA]} atualizada com {len(new_checks)} checks")
        else:
            ids[DRULE_CORPORATIVA] = api.drule.create(name=DRULE_CORPORATIVA, **drule_def)["druleids"][0]
            ok(f"Discovery rule criada: ID {ids[DRULE_CORPORATIVA]} com {len(new_checks)} checks")
    except APIRequestError as e:
        sys.exit(f"  [ERRO] drule: {e}")

    # ── 2. Criar/atualizar action: Zabbix Agent detectado → Linux servers ──────
    banner("2. Configurando actions de auto-classificação")

    _configurar_action_linux(api, ids)
    _configurar_action_snmp(api, ids)
    _configurar_action_icmp_generic(api, ids)
    _configurar_autoreg_action(api, ids)

    # ── 3. Criar grupo de quarentena para hosts novos descobertos ─────────────
    banner("3. Garantindo grupo 'Infra/Descobertos'")
    existing = {g["name"]: g["groupid"] for g in api.hostgroup.get(output=["groupid", "name"])}
    for gname in ["Infra/Descobertos", "Infra/Servidores Linux", "Infra/Servidores Windows", "Infra/Rede"]:
        if gname not in existing:
            result = api.hostgroup.create(name=gname)
            ok(f"Grupo criado: {gname} → ID {result['groupids'][0]}")
        else:
            info(f"Grupo já existe: {gname} (ID {existing[gname]})")

    banner("CONCLUÍDO — Discovery configurado")
    print("  Execute o collector para varredura imediata:")
    print("  python3 collectors/network_discovery.py --scan-now\n")


def _get_or_none(api: ZabbixAPI, name: str) -> str | None:
    """Retorna actionid de uma action pelo nome, ou None."""
    actions = api.action.get(
        output=["actionid", "name"],
        filter={"name": name},
        eventsource=1,  # discovery
    )
    return actions[0]["actionid"] if actions else None


def _configurar_action_linux(api: ZabbixAPI, ids: dict[str, str]) -> None:
    """Action: drule 'Scan Rede Corporativa' detecta host UP → Linux servers.

    Filtra por druleid + status UP. A classificação precisa por tipo de serviço
    (agent/SNMP/SSH) é feita pelo collector Python nmap. Esta action serve de
    fallback para hosts que o Zabbix auto-descobrir antes do next scan.

    Nasce DESATIVADA: cria um host para cada IP que responder na faixa. Revise
    Monitoramento → Discovery e ative pela UI; re-execuções não mudam o status.
    """
    name = "Infra — Discovery: Adicionar servidores Linux"
    existing_id = _get_or_none(api, name)

    action_def = {
        "name": name,
        "eventsource": "1",  # discovery
        "status": "1",  # disabled na criação — ver docstring
        "filter": {
            "evaltype": "0",  # AND
            "conditions": [
                # Discovery status = UP (conditiontype 12, value 0)
                {"conditiontype": "12", "operator": "0", "value": "0"},
                # Discovery rule = DRULE_CORPORATIVA (conditiontype 18)
                {"conditiontype": "18", "operator": "0", "value": ids[DRULE_CORPORATIVA]},
            ],
        },
        "operations": [
            # Adicionar ao grupo Discovered hosts (triagem inicial)
            {"operationtype": "4", "opgroup": [{"groupid": ids[GRP_LINUX]}]},
            {"operationtype": "4", "opgroup": [{"groupid": ids[GRP_SERVIDORES]}]},
            {"operationtype": "6", "optemplate": [{"templateid": ids[TMPL_ICMP_PING]}]},
            # Habilitar host
            {"operationtype": "9"},
        ],
    }

    try:
        if existing_id:
            action_def.pop("status")  # preserva ativação feita pela UI
            api.action.update(actionid=existing_id, **action_def)
            ok(f"Action atualizada (status mantido): {name}")
        else:
            api.action.create(**action_def)
            ok(f"Action criada (DESATIVADA — ativar pela UI após revisar): {name}")
    except APIRequestError as e:
        print(f"  [AVISO] action Linux: {e}")


def _configurar_action_snmp(api: ZabbixAPI, ids: dict[str, str]) -> None:
    """Action: drule detecta host via SNMP → Firewall group.

    Nota: sem condição por tipo de serviço (não suportado nesta versão do
    conditiontype). O collector Python classifica SNMP via nmap porta 161.
    Esta action é um placeholder para visibilidade no Zabbix.
    """
    name = "Infra — Discovery: Dispositivos de Rede (SNMP)"
    existing_id = _get_or_none(api, name)

    action_def = {
        "name": name,
        "eventsource": "1",
        "status": "1",  # disabled — o collector Python faz a classificação
        "filter": {
            "evaltype": "0",
            "conditions": [
                {"conditiontype": "12", "operator": "0", "value": "0"},
                {"conditiontype": "18", "operator": "0", "value": ids[DRULE_CORPORATIVA]},
            ],
        },
        "operations": [
            {"operationtype": "4", "opgroup": [{"groupid": ids[GRP_FIREWALL]}]},
            {"operationtype": "6", "optemplate": [{"templateid": ids[TMPL_NETWORK_SNMP]}]},
            {"operationtype": "9"},
        ],
    }

    try:
        if existing_id:
            api.action.update(actionid=existing_id, **action_def)
            ok(f"Action atualizada (disabled): {name}")
        else:
            api.action.create(**action_def)
            ok(f"Action criada (disabled): {name}")
    except APIRequestError as e:
        print(f"  [AVISO] action SNMP: {e}")


def _configurar_action_icmp_generic(api: ZabbixAPI, ids: dict[str, str]) -> None:
    """Action: qualquer host ICMP UP → Discovered hosts (quarentena até classificação).

    Nasce DESATIVADA pelo mesmo motivo da action Linux; re-execuções não mudam o status.
    """
    name = "Infra — ICMP UP: adicionar a Discovered hosts"
    existing_id = _get_or_none(api, name)

    action_def = {
        "name": name,
        "eventsource": "1",
        "status": "1",  # disabled na criação — ver docstring
        "filter": {
            "evaltype": "0",
            "conditions": [
                {"conditiontype": "12", "operator": "0", "value": "0"},  # UP
            ],
        },
        "operations": [
            {"operationtype": "4", "opgroup": [{"groupid": ids[GRP_DISCOVERED]}]},
            {"operationtype": "9"},
        ],
    }

    try:
        if existing_id:
            action_def.pop("status")  # preserva ativação feita pela UI
            api.action.update(actionid=existing_id, **action_def)
            ok(f"Action atualizada (status mantido): {name}")
        else:
            api.action.create(**action_def)
            ok(f"Action criada (DESATIVADA — ativar pela UI após revisar): {name}")
    except APIRequestError as e:
        print(f"  [AVISO] action ICMP: {e}")


def _configurar_autoreg_action(api: ZabbixAPI, ids: dict[str, str]) -> None:
    """Action de auto-registration: agente se registra automaticamente."""
    name = "Infra — Auto-registro: Zabbix Agent"
    # eventsource=2 para auto-registration
    actions = api.action.get(
        output=["actionid", "name"],
        filter={"name": name},
        eventsource=2,
    )
    existing_id = actions[0]["actionid"] if actions else None

    action_def = {
        "name": name,
        "eventsource": "2",
        "status": "0",
        "filter": {"evaltype": "0", "conditions": []},
        "operations": [
            {"operationtype": "4", "opgroup": [{"groupid": ids[GRP_LINUX]}]},
            {"operationtype": "4", "opgroup": [{"groupid": ids[GRP_SERVIDORES]}]},
            {"operationtype": "6", "optemplate": [{"templateid": ids[TMPL_LINUX_AGENT]}]},
            {"operationtype": "9"},
        ],
    }

    try:
        if existing_id:
            api.action.update(actionid=existing_id, **action_def)
            ok(f"Auto-reg action atualizada: {name}")
        else:
            api.action.create(**action_def)
            ok(f"Auto-reg action criada: {name}")
    except APIRequestError as e:
        print(f"  [AVISO] auto-reg action: {e}")


if __name__ == "__main__":
    main()
