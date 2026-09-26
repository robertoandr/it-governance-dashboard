"""Resolução de IDs do Zabbix por nome, para os scripts de infra-setup/.

IDs numéricos (groupid, templateid, druleid) mudam a cada instalação do
Zabbix — após a perda da VM, o banco novo reaproveitou o groupid 22 (antes
"Firewall") para "Certificados SSL". Por isso os scripts resolvem tudo pelo
nome, criando o que faltar, e podem ser rodados em qualquer banco.
"""

from __future__ import annotations

import sys

from zabbix_utils import ZabbixAPI


def grupo(api: ZabbixAPI, nome: str) -> str:
    """Retorna o groupid do hostgroup ``nome``, criando-o se não existir."""
    existentes = api.hostgroup.get(output=["groupid"], filter={"name": nome})
    if existentes:
        return existentes[0]["groupid"]
    groupid = api.hostgroup.create(name=nome)["groupids"][0]
    print(f"  [OK] Grupo criado: {nome} → ID {groupid}")
    return groupid


def template(api: ZabbixAPI, nome: str) -> str:
    """Retorna o templateid do template ``nome`` (nome técnico). Aborta se não existir."""
    existentes = api.template.get(output=["templateid"], filter={"host": nome})
    if not existentes:
        sys.exit(f"  [ERRO] Template '{nome}' não existe neste Zabbix.")
    return existentes[0]["templateid"]


def grupo_templates(api: ZabbixAPI, nome: str = "Templates") -> str:
    """Retorna o groupid do template group ``nome``, criando-o se não existir."""
    existentes = api.templategroup.get(output=["groupid"], filter={"name": nome})
    if existentes:
        return existentes[0]["groupid"]
    return api.templategroup.create(name=nome)["groupids"][0]


def versao_major_minor(api: ZabbixAPI) -> str:
    """Retorna a versão do Zabbix no formato ``7.4`` (para URLs release/X.Y)."""
    partes = str(api.api_version()).split(".")
    return f"{partes[0]}.{partes[1]}"
