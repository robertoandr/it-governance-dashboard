"""Template do Zabbix para cada dispositivo descoberto na rede (item r2).

``sugerir`` escolhe o template pelo tipo e pelo fabricante (descrição SNMP,
fabricante do MAC, nome do host). Confiança "alta" quando o template certo é
inequívoco e o aparelho responde ao que ele precisa (SNMP); esses podem ser
monitorados sozinhos. "baixa" fica como sugestão para alguém confirmar.

``monitorar`` cria o host no Zabbix (ou troca o template de um host criado
pelo dashboard, para corrigir falso positivo).
"""

from __future__ import annotations

import re
from typing import Any, Literal

import requests
import structlog
from pydantic import BaseModel

from itgov.api.v1.cftv_monitoring import _zbx
from itgov.utils.cache_swr import CacheSWR

log = structlog.get_logger(__name__)

TIPO_AGENTE, TIPO_SNMP = "1", "2"
TAG_ORIGEM = ("origem", "dashboard-rede")
TEMPLATE_PING = "ICMP Ping"
# Tipos que não se monitora no Zabbix (aparelhos pessoais e estações)
NAO_MONITORAR = frozenset({"movel", "endpoint", "app", "licenca"})


class Sugestao(BaseModel):
    """Template sugerido para um dispositivo."""

    template: str
    grupo: str
    interface: Literal["snmp", "agente"]
    confianca: Literal["alta", "baixa"]
    motivo: str


# (tipo, padrão no fabricante/SNMP/nome, template, grupo, interface, precisa SNMP p/ confiança alta)
_REGRAS: tuple[tuple[str, str, str, str, str, bool], ...] = (
    ("camera", r"\b(?:dvr|nvr|mhdx|xvr)\b", "Intelbras NVR SNMP", "CFTV/NVRs", "snmp", True),
    ("camera", r".", TEMPLATE_PING, "CFTV/Cameras", "agente", False),
    ("impressora", r"ricoh", "SNMP Ricoh Printers", "Impressoras", "snmp", True),
    ("impressora", r"brother", "Brother Printers", "Impressoras", "snmp", True),
    ("impressora", r"lexmark", "SNMP - Lexmark COLOR", "Impressoras", "snmp", True),
    ("switch", r"mikrotik|routeros", "Mikrotik by SNMP", "Rede/Switches", "snmp", True),
    ("switch", r"cisco", "Cisco IOS by SNMP", "Rede/Switches", "snmp", True),
    ("switch", r"tp-?link", "TP-LINK by SNMP", "Rede/Switches", "snmp", True),
    ("switch", r"huawei", "Huawei VRP by SNMP", "Rede/Switches", "snmp", True),
    ("switch", r"d-?link", "D-Link DES_DGS Switch by SNMP", "Rede/Switches", "snmp", True),
    ("switch", r"aruba|procurve|hewlett|\bhpe?\b", "HP Enterprise Switch by SNMP", "Rede/Switches", "snmp", True),
    ("ap", r"ubiquiti|unifi|airos", "Ubiquiti AirOS by SNMP", "Rede/Access points", "snmp", True),
    ("firewall", r"forti", "FortiGate by SNMP", "Links WAN", "snmp", True),
    ("", r"\bapc\b|smart-ups|symmetra", "APC UPS by SNMP", "Nobreaks", "snmp", True),
)


def _texto(host: dict[str, Any]) -> str:
    return " ".join(str(host.get(c) or "") for c in ("vendor", "snmp_descr", "hostname", "ia_nome", "os_guess")).lower()


def sugerir(host: dict[str, Any]) -> Sugestao | None:
    """Template sugerido para o host descoberto.

    Args:
        host: Host da descoberta (``tipo_sugerido``, ``vendor``, ``snmp_descr``,
            ``hostname``, ``has_snmp``...).

    Returns:
        A sugestão, ou None para tipos que não se monitora (celular, estação).
    """
    tipo = str(host.get("tipo_sugerido") or "outro")
    if tipo in NAO_MONITORAR:
        return None
    texto, snmp = _texto(host), bool(host.get("has_snmp") or host.get("snmp_descr"))
    for t, padrao, template, grupo, interface, exige_snmp in _REGRAS:
        if (t and t != tipo) or not re.search(padrao, texto, re.I):
            continue
        alta = snmp if exige_snmp else True
        motivo = (
            f"{tipo} {('+ ' + m.group(0)) if (m := re.search(padrao, texto, re.I)) and padrao != '.' else ''}".strip()
        )
        if exige_snmp and not snmp:
            motivo += " (sem resposta SNMP — confirmar)"
        return Sugestao(template=template, grupo=grupo, interface=interface, confianca="alta" if alta else "baixa",
                        motivo=motivo)  # fmt: skip
    if tipo == "impressora" and snmp:
        return Sugestao(template="Generic by SNMP", grupo="Impressoras", interface="snmp", confianca="baixa",
                        motivo="impressora sem template do fabricante")  # fmt: skip
    if tipo in ("switch", "ap") and snmp:
        return Sugestao(template="Network Generic Device by SNMP", grupo="Rede/Switches", interface="snmp",
                        confianca="baixa", motivo="equipamento de rede sem template do fabricante")  # fmt: skip
    if tipo == "servidor":
        windows = "windows" in texto or "3389" in str(host.get("portas") or "")
        return Sugestao(template="Windows by Zabbix agent active" if windows else "Linux by Zabbix agent active",
                        grupo="Servidores", interface="agente", confianca="baixa",
                        motivo="servidor: precisa do agente Zabbix instalado")  # fmt: skip
    return Sugestao(template=TEMPLATE_PING, grupo="Dispositivos de rede", interface="agente", confianca="baixa",
                    motivo="sem template específico: só disponibilidade (ping)")  # fmt: skip


def _buscar_templates() -> list[str]:
    try:
        return sorted(t["name"] for t in _zbx("template.get", {"output": ["name"]}))
    except (requests.RequestException, RuntimeError, ValueError, KeyError) as exc:
        log.warning("zabbix_templates.lista_falhou", erro=str(exc)[:200])
        return []


_cache_templates: CacheSWR[list[str]] = CacheSWR("zabbix.templates", ttl=3600, valido=bool)


def templates_disponiveis() -> list[str]:
    """Nomes dos templates do Zabbix (cache de 1 h), para trocar a sugestão na tela."""
    return _cache_templates.get(_buscar_templates)


def _grupo(nome: str) -> str:
    achados = _zbx("hostgroup.get", {"output": ["groupid"], "filter": {"name": [nome]}})
    if achados:
        return str(achados[0]["groupid"])
    return str(_zbx("hostgroup.create", {"name": nome})["groupids"][0])


def _template(nome: str) -> str:
    achados = _zbx("template.get", {"output": ["templateid"], "filter": {"name": [nome]}})
    if not achados:
        raise ValueError(f"template {nome} não existe no Zabbix")
    return str(achados[0]["templateid"])


def nome_host(nome: str, ip: str) -> tuple[str, str]:
    """Nome técnico e nome visível, únicos no Zabbix porque levam o IP.

    Returns:
        ``(host, name)`` — técnico só com letras, números, ``.``, ``-`` e ``_``.
    """
    limpo = re.sub(r"[^A-Za-z0-9._-]+", "-", nome).strip("-")[:100]
    return (f"{limpo}_{ip}" if limpo else f"dispositivo_{ip}"), (f"{nome[:100]} ({ip})" if nome else ip)


def host_por_ip(ip: str) -> dict[str, Any] | None:
    """Host do Zabbix que tem uma interface com este IP (com templates e tags)."""
    interfaces = _zbx("hostinterface.get", {"output": ["hostid"], "filter": {"ip": [ip]}})
    if not interfaces:
        return None
    hosts = _zbx(
        "host.get",
        {"output": ["hostid", "host", "name"], "hostids": [interfaces[0]["hostid"]],
         "selectParentTemplates": ["templateid", "name"], "selectTags": ["tag", "value"]},
    )  # fmt: skip
    return hosts[0] if hosts else None


def criado_pelo_dashboard(host: dict[str, Any]) -> bool:
    """O host foi criado por esta tela (pode ter o template trocado)?"""
    return any((t.get("tag"), t.get("value")) == TAG_ORIGEM for t in host.get("tags") or [])


def monitorar(ip: str, nome: str, template: str, grupo: str, interface: str) -> tuple[str, bool]:
    """Cria o host no Zabbix com o template, ou troca o template de um host do dashboard.

    Args:
        ip: IP do dispositivo.
        nome: Nome visível do host.
        template: Nome do template.
        grupo: Grupo de hosts (criado se não existir).
        interface: ``"snmp"`` (SNMPv2, macro ``{$SNMP_COMMUNITY}`` do Zabbix)
            ou ``"agente"`` (também serve para os checks de ping).

    Returns:
        ``(hostid, criado)``; ``criado`` é False quando só trocou o template.

    Raises:
        ValueError: Template inexistente ou IP já monitorado por host que
            não foi criado pelo dashboard.
        RuntimeError: Erro da API do Zabbix.
    """
    templateid = _template(template)
    existente = host_por_ip(ip)
    if existente:
        if not criado_pelo_dashboard(existente):
            raise ValueError(f"{ip} já é monitorado no Zabbix por {existente['name']}")
        _zbx(
            "host.update",
            {"hostid": existente["hostid"], "templates": [{"templateid": templateid}],
             "templates_clear": [{"templateid": t["templateid"]} for t in existente.get("parentTemplates") or []
                                 if t["templateid"] != templateid]},
        )  # fmt: skip
        log.info("zabbix_templates.template_trocado", ip=ip, template=template, hostid=existente["hostid"])
        return str(existente["hostid"]), False
    iface: dict[str, Any] = {"type": TIPO_SNMP if interface == "snmp" else TIPO_AGENTE, "main": "1", "useip": "1",
                             "ip": ip, "dns": "", "port": "161" if interface == "snmp" else "10050"}  # fmt: skip
    if interface == "snmp":
        iface["details"] = {"version": "2", "bulk": "1", "community": "{$SNMP_COMMUNITY}"}
    tecnico, visivel = nome_host(nome, ip)
    criado = _zbx(
        "host.create",
        {"host": tecnico, "name": visivel, "groups": [{"groupid": _grupo(grupo)}],
         "templates": [{"templateid": templateid}], "interfaces": [iface],
         "tags": [{"tag": TAG_ORIGEM[0], "value": TAG_ORIGEM[1]}, {"tag": "ip", "value": ip}]},
    )  # fmt: skip
    hostid = str(criado["hostids"][0])
    log.info("zabbix_templates.host_criado", ip=ip, template=template, grupo=grupo, hostid=hostid)
    return hostid, True
