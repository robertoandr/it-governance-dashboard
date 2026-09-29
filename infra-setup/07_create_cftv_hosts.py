#!/usr/bin/env python3
"""
Recria no Zabbix os hosts de CFTV (NVR, câmeras IP e DVRs) lidos pela página /cftv.

Substitui cftv-setup/01, 03a, 07, 08a e 08b, que dependiam de caminhos da VM
antiga (/opt/it-gov-dashboard) e de groupids fixos (47, 48, 52, 53) que não
existem mais após a perda da VM.

Cria (idempotente, tudo resolvido por nome):
  - Hostgroups "CFTV/NVRs", "CFTV/Cameras" e "CFTV/DVRs"
  - Hikvision: 1 NVR, 18 câmeras IP e 3 DVRs com o template "ICMP Ping"
    (item icmpping + triggers nativas de indisponibilidade/perda/latência)
  - Intelbras (lista em infra-setup/cftv_intelbras.json, se existir): câmeras
    com "ICMP Ping"; NVRs/DVRs com interface SNMPv3, "ICMP Ping" e
    "Intelbras NVR SNMP" (importado por 03_install_templates.py)
  - Macros globais {$CFTV.SNMPV3.USER} e, como Secret, {$CFTV.SNMPV3.AUTHPASS} /
    {$CFTV.SNMPV3.PRIVPASS}, lidas do .env (CFTV_SNMPV3_USER, CFTV_SNMPV3_AUTHPASS,
    CFTV_SNMPV3_PRIVPASS). Secrets só são gravadas quando a variável está definida.
  - Sede Centro (infra-setup/cftv_sede_centro.json): câmeras dos DVRs 1-3,
    faciais, telas e antenas (grupo "CFTV/Controle de acesso") com "ICMP Ping"
  - DVR-1/2/3 com SNMPv3 + "Intelbras NVR SNMP"; se o host antigo só tiver
    interface de agente, a interface SNMP é adicionada
  - Tags category=cftv, subcategory=nvr|camera|dvr e andar, usadas pela página
    /cftv para agrupar os dispositivos

Hosts que já existem têm grupos, templates e tags atualizados; interface e
status não são alterados.

Uso:
    venv/bin/python infra-setup/03_install_templates.py   # template Intelbras
    venv/bin/python infra-setup/07_create_cftv_hosts.py
"""

from __future__ import annotations

import json
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
    from zabbix_utils.exceptions import APIRequestError
except ImportError:
    sys.exit("Instale: pip install zabbix-utils --break-system-packages")

sys.path.insert(0, str(Path(__file__).resolve().parent))
import zbx_lookup  # noqa: E402

TEMPLATE_ICMP = "ICMP Ping"
TEMPLATE_INTELBRAS = "Intelbras NVR SNMP"
GRP_ACESSO = "CFTV/Controle de acesso"
GRP_BY_SUBCAT = {
    "nvr": "CFTV/NVRs",
    "camera": "CFTV/Cameras",
    "dvr": "CFTV/DVRs",
    "facial": GRP_ACESSO,
    "tela": GRP_ACESSO,
    "antena": GRP_ACESSO,
}
NVR_HOST = "nvr-centro-01"
INTELBRAS_FILE = Path(__file__).resolve().parent / "cftv_intelbras.json"
SEDE_FILE = Path(__file__).resolve().parent / "cftv_sede_centro.json"

# SNMPv3 authPriv, MD5 + DES — mesma configuração cadastrada nos equipamentos Intelbras
MACRO_USER = "{$CFTV.SNMPV3.USER}"
MACRO_AUTH = "{$CFTV.SNMPV3.AUTHPASS}"
MACRO_PRIV = "{$CFTV.SNMPV3.PRIVPASS}"
SNMPV3_DETAILS = {
    "version": 3,
    "bulk": 1,
    "securityname": MACRO_USER,
    "securitylevel": 2,  # authPriv
    "authprotocol": 0,  # MD5
    "authpassphrase": MACRO_AUTH,
    "privprotocol": 0,  # DES
    "privpassphrase": MACRO_PRIV,
    "contextname": "",
}
MACRO_TEXT, MACRO_SECRET = 0, 1

# ── Inventário Hikvision (fonte: cftv-setup/03a_create_dvrs.py, 08a e 08b) ──
HIKVISION: list[dict] = [
    {
        "host": NVR_HOST,
        "name": "NVR Centro - Hikvision DS-7632NXI-K2",
        "ip": "172.29.11.20",
        "subcategory": "nvr",
        "andar": "Loja Centro",
        "tags": {"vendor": "Hikvision", "model": "DS-7632NXI-K2", "loja": "Centro", "criticidade": "alta"},
    },
    # DVRs Intelbras da Sede: SNMPv3 ativo (engine ID net-snmp, sondado em 2026-09-24)
    *(
        {
            "host": f"DVR-{n}",
            "name": nome,
            "ip": f"172.29.11.{16 + n}",
            "subcategory": "dvr",
            "andar": andar,
            "snmp": True,
            "tags": {"vendor": "Intelbras", "loja": "Sede Centro"},
        }
        for n, nome, andar in (
            (1, "DVR-1 · 9º/8º/Elevadores/Faciais", "9º/8º"),
            (2, "DVR-2 · 7º/6º andar", "7º/6º"),
            (3, "DVR-3 · 5º/Térreo/Garagem", "5º/Térreo"),
        )
    ),
]

CAMERAS = [
    ("D1", "172.29.11.151"),
    ("D2", "172.29.11.5"),
    ("D3", "172.29.11.7"),
    ("D4", "172.29.11.6"),
    ("D5", "172.29.11.3"),
    ("D6", "172.29.11.2"),
    ("D7", "172.29.11.4"),
    ("D8", "172.29.11.12"),
    ("D9", "172.29.11.150"),
    ("D10", "172.29.11.8"),
    ("D11", "172.29.11.13"),
    ("D12", "172.29.11.11"),
    ("D13", "172.29.11.9"),
    ("D14", "172.29.11.15"),
    ("D15", "192.168.1.124"),  # IP fora da rede 172.29.11.0/24 — sem rota a partir do Zabbix
    ("D16", "172.29.11.10"),
    ("D17", "172.29.11.14"),
    ("D18", "172.29.11.148"),
]
for canal, ip in CAMERAS:
    HIKVISION.append(
        {
            "host": f"cam-loja-d{canal[1:].zfill(2)}",
            "name": f"Câmera {canal} - Loja Centro (canal {canal})",
            "ip": ip,
            "subcategory": "camera",
            "andar": "Loja Centro",
            "tags": {"vendor": "Hikvision", "canal_nvr": canal, "parent_nvr": NVR_HOST},
        }
    )


def _carregar(arquivo: Path, vendor: str | None = None) -> list[dict]:
    """Lê um inventário JSON: lista de {host, name, ip, subcategory, andar, tags?}.

    Com ``vendor="Intelbras"``, NVRs/DVRs da lista recebem SNMPv3 + template
    Intelbras (só gravadores têm esse MIB; câmeras ficam com ICMP).
    """
    if not arquivo.exists():
        print(f"  [--] {arquivo.name} não encontrado — pulando")
        return []
    hosts = json.loads(arquivo.read_text())
    for h in hosts:
        if vendor:
            h.setdefault("tags", {})["vendor"] = vendor
        h["snmp"] = vendor == "Intelbras" and h["subcategory"] in ("nvr", "dvr")
    return hosts


def _somar_vinculos(atual: dict, novo: dict) -> dict:
    """Soma grupos, templates e tags do inventário aos que o host já tem.

    ``host.update`` substitui as listas inteiras; sem isso, templates, grupos e
    tags configurados à mão (ex.: ``loja``/``dvr``) seriam removidos.
    """
    grupos = {g["groupid"] for g in atual.get("hostgroups", [])} | {g["groupid"] for g in novo["groups"]}
    templates = {t["templateid"] for t in atual.get("parentTemplates", [])} | {
        t["templateid"] for t in novo["templates"]
    }
    novas = {t["tag"] for t in novo["tags"]}
    tags = [t for t in atual.get("tags", []) if t["tag"] not in novas] + novo["tags"]
    return {
        **novo,
        "groups": [{"groupid": g} for g in sorted(grupos)],
        "templates": [{"templateid": t} for t in sorted(templates)],
        "tags": tags,
    }


def banner(text: str) -> None:
    print(f"\n{'=' * 60}\n  {text}\n{'=' * 60}")


def _garantir_macros(api: ZabbixAPI) -> None:
    """Cria/atualiza as macros globais SNMPv3. Senhas só são escritas se vierem do .env."""
    desejadas = [
        (MACRO_USER, os.environ.get("CFTV_SNMPV3_USER", "zbxro"), MACRO_TEXT),
        (MACRO_AUTH, os.environ.get("CFTV_SNMPV3_AUTHPASS", ""), MACRO_SECRET),
        (MACRO_PRIV, os.environ.get("CFTV_SNMPV3_PRIVPASS", ""), MACRO_SECRET),
    ]
    existentes = {
        m["macro"]: m["globalmacroid"] for m in api.usermacro.get(globalmacro=True, output=["globalmacroid", "macro"])
    }
    for macro, valor, tipo in desejadas:
        if not valor:
            estado = "já existe" if macro in existentes else "AUSENTE — defina no .env e rode de novo"
            print(f"  [--] {macro}: sem valor no .env ({estado})")
            continue
        if macro in existentes:
            api.usermacro.updateglobal(globalmacroid=existentes[macro], value=valor, type=tipo)
        else:
            api.usermacro.createglobal(macro=macro, value=valor, type=tipo)
        print(f"  [OK] {macro} gravada{' (Secret)' if tipo == MACRO_SECRET else ''}")


def main() -> None:
    banner("CFTV — NVRs, câmeras, DVRs e controle de acesso")

    api = ZabbixAPI(url=ZABBIX_URL, token=ZABBIX_TOKEN, skip_version_check=True)
    print(f"  Zabbix {api.api_version()} conectado")

    hosts = HIKVISION + _carregar(INTELBRAS_FILE, vendor="Intelbras") + _carregar(SEDE_FILE)

    grupos = {sub: zbx_lookup.grupo(api, nome) for sub, nome in GRP_BY_SUBCAT.items()}
    tpl_icmp = zbx_lookup.template(api, TEMPLATE_ICMP)
    # Template e macros SNMP só são exigidos quando há gravadores Intelbras na lista
    tem_snmp = any(h.get("snmp") for h in hosts)
    tpl_intelbras = zbx_lookup.template(api, TEMPLATE_INTELBRAS) if tem_snmp else None
    if tem_snmp:
        _garantir_macros(api)

    existentes: dict[str, dict] = {}
    com_snmp: set[str] = set()  # hostids que já têm interface SNMP
    for h in api.host.get(
        output=["hostid", "host"],
        selectInterfaces=["type"],
        selectHostGroups=["groupid"],
        selectParentTemplates=["templateid"],
        selectTags=["tag", "value"],
        filter={"host": [h["host"] for h in hosts]},
    ):
        existentes[h["host"]] = h
        if any(i["type"] == "2" for i in h["interfaces"]):
            com_snmp.add(h["hostid"])

    criados = atualizados = erros = 0
    for h in hosts:
        sub = h["subcategory"]
        templates = [{"templateid": tpl_icmp}]
        if h.get("snmp"):
            templates.append({"templateid": tpl_intelbras})
        tags = [
            {"tag": "category", "value": "cftv"},
            {"tag": "subcategory", "value": sub},
            {"tag": "andar", "value": h["andar"]},
        ] + [{"tag": k, "value": v} for k, v in h.get("tags", {}).items()]
        comum = {"name": h["name"], "groups": [{"groupid": grupos[sub]}], "templates": templates, "tags": tags}

        if h.get("snmp"):
            interface = {
                "type": 2,
                "main": 1,
                "useip": 1,
                "ip": h["ip"],
                "dns": "",
                "port": "161",
                "details": SNMPV3_DETAILS,
            }
        else:
            interface = {"type": 1, "main": 1, "useip": 1, "ip": h["ip"], "dns": "", "port": "10050"}

        try:
            if h["host"] in existentes:
                atual = existentes[h["host"]]
                hostid = atual["hostid"]
                if h.get("snmp") and hostid not in com_snmp:
                    # Host antigo só com interface de agente: o template SNMP exige interface SNMP
                    api.hostinterface.create(hostid=hostid, **interface)
                    print(f"  [OK] {h['host']:<24} interface SNMPv3 adicionada")
                api.host.update(hostid=hostid, **_somar_vinculos(atual, comum))
                atualizados += 1
                print(f"  [--] {h['host']:<24} {h['ip']:<15} atualizado")
            else:
                api.host.create(
                    host=h["host"],
                    interfaces=[interface],
                    description=f"CFTV ({sub}). Monitorado via ICMP ping{' e SNMPv3' if h.get('snmp') else ''}. IP: {h['ip']}.",
                    **comum,
                )
                criados += 1
                print(f"  [OK] {h['host']:<24} {h['ip']:<15} criado")
        except APIRequestError as exc:
            erros += 1
            print(f"  [!!] {h['host']}: {exc}")

    banner(f"Resumo: {criados} criados, {atualizados} atualizados, {erros} erros")
    if erros:
        sys.exit(1)


if __name__ == "__main__":
    main()
