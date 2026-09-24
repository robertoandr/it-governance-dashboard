#!/usr/bin/env python3
"""
Recria no Zabbix os hosts de CFTV (NVR, câmeras IP e DVRs) lidos pela página /cftv.

Substitui cftv-setup/01, 03a, 07, 08a e 08b, que dependiam de caminhos da VM
antiga (/opt/it-gov-dashboard) e de groupids fixos (47, 48, 52, 53) que não
existem mais após a perda da VM.

Cria (idempotente, tudo resolvido por nome):
  - Hostgroups "CFTV/NVRs", "CFTV/Cameras" e "CFTV/DVRs"
  - 1 NVR, 18 câmeras IP e 3 DVRs, todos com o template "ICMP Ping"
    (item icmpping + triggers nativas de indisponibilidade/perda/latência)
  - Tags category=cftv, subcategory=nvr|camera|dvr e andar, usadas pela página
    /cftv para agrupar os dispositivos

Hosts que já existem têm grupos, template e tags atualizados; interface e
status não são alterados.

Uso:
    venv/bin/python infra-setup/07_create_cftv_hosts.py
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
    from zabbix_utils.exceptions import APIRequestError
except ImportError:
    sys.exit("Instale: pip install zabbix-utils --break-system-packages")

sys.path.insert(0, str(Path(__file__).resolve().parent))
import zbx_lookup  # noqa: E402

# ── Inventário (fonte: cftv-setup/03a_create_dvrs.py, 08a e 08b) ───────────
TEMPLATE_ICMP = "ICMP Ping"
GRP_NVR = "CFTV/NVRs"
GRP_CAM = "CFTV/Cameras"
GRP_DVR = "CFTV/DVRs"
NVR_HOST = "nvr-centro-01"

# (host técnico, nome visível, IP, grupo, subcategory, andar, tags extras)
HOSTS: list[tuple[str, str, str, str, str, str, dict[str, str]]] = [
    (
        NVR_HOST,
        "NVR Centro - Hikvision DS-7632NXI-K2",
        "172.29.11.20",
        GRP_NVR,
        "nvr",
        "Loja Centro",
        {"vendor": "Hikvision", "model": "DS-7632NXI-K2", "loja": "Centro", "criticidade": "alta"},
    ),
    ("DVR-1", "DVR-1 · 9º/8º/Elevadores/Faciais", "172.29.11.17", GRP_DVR, "dvr", "9º/8º", {}),
    ("DVR-2", "DVR-2 · 7º/6º andar", "172.29.11.18", GRP_DVR, "dvr", "7º/6º", {}),
    ("DVR-3", "DVR-3 · 5º/Térreo/Garagem", "172.29.11.19", GRP_DVR, "dvr", "5º/Térreo", {}),
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
    HOSTS.append(
        (
            f"cam-loja-d{canal[1:].zfill(2)}",
            f"Câmera {canal} - Loja Centro (canal {canal})",
            ip,
            GRP_CAM,
            "camera",
            "Loja Centro",
            {"vendor": "Hikvision", "canal_nvr": canal, "parent_nvr": NVR_HOST},
        )
    )


def banner(text: str) -> None:
    print(f"\n{'=' * 60}\n  {text}\n{'=' * 60}")


def main() -> None:
    banner("CFTV — NVR, câmeras IP e DVRs")

    api = ZabbixAPI(url=ZABBIX_URL, token=ZABBIX_TOKEN, skip_version_check=True)
    print(f"  Zabbix {api.api_version()} conectado")

    grupos = {nome: zbx_lookup.grupo(api, nome) for nome in (GRP_NVR, GRP_CAM, GRP_DVR)}
    template_id = zbx_lookup.template(api, TEMPLATE_ICMP)

    existentes = {
        h["host"]: h["hostid"] for h in api.host.get(output=["hostid", "host"], filter={"host": [h[0] for h in HOSTS]})
    }

    criados = atualizados = erros = 0
    for host, nome, ip, grupo, subcat, andar, extras in HOSTS:
        tags = [
            {"tag": "category", "value": "cftv"},
            {"tag": "subcategory", "value": subcat},
            {"tag": "andar", "value": andar},
        ] + [{"tag": k, "value": v} for k, v in extras.items()]
        comum = {
            "name": nome,
            "groups": [{"groupid": grupos[grupo]}],
            "templates": [{"templateid": template_id}],
            "tags": tags,
        }
        try:
            if host in existentes:
                api.host.update(hostid=existentes[host], **comum)
                atualizados += 1
                print(f"  [--] {host:<16} {ip:<15} atualizado")
            else:
                api.host.create(
                    host=host,
                    interfaces=[{"type": 1, "main": 1, "useip": 1, "ip": ip, "dns": "", "port": "10050"}],
                    description=f"CFTV ({subcat}). Monitorado via ICMP ping. IP: {ip}.",
                    **comum,
                )
                criados += 1
                print(f"  [OK] {host:<16} {ip:<15} criado")
        except APIRequestError as exc:
            erros += 1
            print(f"  [!!] {host}: {exc}")

    banner(f"Resumo: {criados} criados, {atualizados} atualizados, {erros} erros")
    if erros:
        sys.exit(1)


if __name__ == "__main__":
    main()
