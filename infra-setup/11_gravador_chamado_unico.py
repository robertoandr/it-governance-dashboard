#!/usr/bin/env python3
"""
Gravador caiu ou reiniciou: um chamado só para ele, não um por câmera.

Quando o DVR/NVR reinicia, os canais aparecem "Unconnect"/"Empty" até as
câmeras voltarem e cada canal abria chamado e WhatsApp (11/10 23:39: 32 do
Shopping_1). A ação 12 só envia no passo 2 (5 min); este script faz os
alertas de câmera não chegarem lá quando a causa é o gravador. Ajusta
(idempotente):

  - Template "Intelbras NVR SNMP":
      * uptime coletado de 1 em 1 min (era 10 min): o "reiniciou" aparece
        logo que o SNMP volta, antes dos canais
      * "System has been restarted" ignora o reinício automático da
        madrugada (01:50–02:30, hora de Brasília), que é a manutenção
        programada dos gravadores
      * canais "Camera N absent/disconnected" só alertam com o gravador no ar
        há 15 min e dependem do "reiniciou" e do "No SNMP data collection"
      * canal sem câmera cadastrada (o gravador informa IP 192.168.0.0) não
        alerta: eram os 41 "absent" fixos de 13/10 (Triunfo_4 com 32 canais e
        9 câmeras); quando cadastram câmera no canal, ele volta a alertar
  - Cada gravador: "No SNMP data collection" e "Unavailable by Media Port"
    dependem do "Unavailable by ICMP ping" dele (caiu = um chamado só)
  - Cada câmera (tag ``dvr``/``parent_nvr``): o ping depende do ping e do
    "reiniciou" do seu gravador

Uso:
    uv run --no-project --with zabbix-utils --with urllib3 python3 infra-setup/11_gravador_chamado_unico.py [--dry-run]
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
from typing import Any

_AQUI = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("whatsapp_alert_script", _AQUI / "09_create_whatsapp_alert.py")
wa = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(wa)  # type: ignore[union-attr]

from zabbix_utils import ZabbixAPI  # noqa: E402
from zabbix_utils.exceptions import APIRequestError  # noqa: E402

TEMPLATE = "Intelbras NVR SNMP"
UPTIME_KEY = "enterprises.deviceUpTime"
UPTIME_DELAY = "1m"
GRUPOS_GRAVADOR = ("CFTV/DVRs", "CFTV/NVRs")
GRUPO_CAMERAS = "CFTV/Cameras"

PING = "ICMP Ping: Unavailable by ICMP ping"
REINICIOU = "System has been restarted (uptime less 10m)"
SEM_SNMP = "No SNMP data collection"
PORTA_MIDIA = "Unavailable by Media Port"

# Manutenção automática dos gravadores ~02:05; o fim cobre os 10 min do uptime
JANELA_MANUTENCAO = ("015000", "023000")
UPTIME_MINIMO_CANAIS = "15m"
# IP que o gravador Intelbras informa para canal sem câmera cadastrada
IP_CANAL_VAZIO = "192.168.0.0"

EXPR_REINICIOU = (
    f"last(/{TEMPLATE}/{UPTIME_KEY})<10m and (time()<{JANELA_MANUTENCAO[0]} or time()>{JANELA_MANUTENCAO[1]})"
)


def expr_canal(estado: str) -> str:
    """Expressão do canal ``estado`` ("Empty"/"Unconnect") com câmera cadastrada e o gravador no ar há 15 min."""
    return (
        f'last(/{TEMPLATE}/remoteCam[status{{#SNMPINDEX}}],#1)="{estado}"'
        " and {$CAM.STATUS.CONFIAVEL}=1"
        f" and last(/{TEMPLATE}/{UPTIME_KEY})>={UPTIME_MINIMO_CANAIS}"
        f' and last(/{TEMPLATE}/remoteCam[IpAddr{{#SNMPINDEX}}])<>"{IP_CANAL_VAZIO}"'
    )


CANAIS = {
    "Camera {#SNMPINDEX} absent": expr_canal("Empty"),
    "Camera {#SNMPINDEX} disconnected": expr_canal("Unconnect"),
}


def chave_gravador(host: dict[str, Any]) -> str:
    """Nome pelo qual as câmeras apontam para o gravador (tag ``dvr`` ou nome técnico)."""
    tags = {t["tag"]: t["value"] for t in host.get("tags") or []}
    return tags.get("dvr") or host["host"]


def gravador_da_camera(host: dict[str, Any]) -> str:
    """Gravador da câmera pela tag ``dvr`` (Intelbras) ou ``parent_nvr`` (Hikvision); "" se não tem."""
    tags = {t["tag"]: t["value"] for t in host.get("tags") or []}
    return tags.get("dvr") or tags.get("parent_nvr") or ""


def deps_faltando(atuais: list[dict[str, str]], desejadas: list[str]) -> list[str]:
    """IDs de ``desejadas`` que ainda não estão em ``atuais`` (sem repetir)."""
    tem = {d["triggerid"] for d in atuais}
    return [t for i, t in enumerate(desejadas) if t not in tem and t not in desejadas[:i]]


def _somar_deps(api: ZabbixAPI, trigger: dict[str, Any], desejadas: list[str], prototipo: bool, dry: bool) -> bool:
    """Acrescenta dependências sem tirar as que já existem. Retorna se mudou."""
    novas = deps_faltando(trigger.get("dependencies") or [], desejadas)
    if not novas:
        return False
    if not dry:
        todas = [{"triggerid": d["triggerid"]} for d in trigger.get("dependencies") or []]
        todas += [{"triggerid": t} for t in novas]
        metodo = api.triggerprototype.update if prototipo else api.trigger.update
        metodo(triggerid=trigger["triggerid"], dependencies=todas)
    return True


def _triggers(api: ZabbixAPI, hostids: list[str], descricao: str) -> dict[str, dict[str, Any]]:
    """Trigger ``descricao`` de cada host → {hostid: trigger}."""
    if not hostids:
        return {}
    achados = api.trigger.get(
        output=["triggerid", "description"],
        hostids=hostids,
        filter={"description": descricao},
        selectHosts=["hostid"],
        selectDependencies=["triggerid"],
    )
    return {t["hosts"][0]["hostid"]: t for t in achados}


def ajustar_template(api: ZabbixAPI, dry: bool) -> None:
    """Uptime a 1 min, janela da madrugada no "reiniciou" e canais esperando o gravador."""
    wa.banner(f"1. Template {TEMPLATE}")
    tpl = api.template.get(output=["templateid"], filter={"host": TEMPLATE})
    if not tpl:
        sys.exit(f"  [ERRO] Template '{TEMPLATE}' não existe")
    tplid = tpl[0]["templateid"]

    item = api.item.get(output=["itemid", "delay"], templateids=[tplid], filter={"key_": UPTIME_KEY})
    if not item:
        sys.exit(f"  [ERRO] Item {UPTIME_KEY} não existe no template")
    if item[0]["delay"] != UPTIME_DELAY:
        if not dry:
            api.item.update(itemid=item[0]["itemid"], delay=UPTIME_DELAY)
        wa.ok(f"Uptime: {item[0]['delay']} → {UPTIME_DELAY}")
    else:
        wa.info(f"Uptime já em {UPTIME_DELAY}")

    trig = {
        t["description"]: t
        for t in api.trigger.get(
            output=["triggerid", "description"], templateids=[tplid], filter={"description": [REINICIOU, SEM_SNMP]}
        )
    }
    if REINICIOU not in trig or SEM_SNMP not in trig:
        sys.exit(f"  [ERRO] Triggers '{REINICIOU}'/'{SEM_SNMP}' não existem no template")
    if not dry:
        api.trigger.update(
            triggerid=trig[REINICIOU]["triggerid"],
            expression=EXPR_REINICIOU,
            comments="Reinício entre 01:50 e 02:30 é a manutenção automática do gravador: não alerta (script 11).",
        )
    wa.ok(
        f"'{REINICIOU}' ignora {JANELA_MANUTENCAO[0][:2]}:{JANELA_MANUTENCAO[0][2:4]}–"
        f"{JANELA_MANUTENCAO[1][:2]}:{JANELA_MANUTENCAO[1][2:4]}"
    )

    protos = api.triggerprototype.get(
        output=["triggerid", "description"],
        templateids=[tplid],
        filter={"description": list(CANAIS)},
        selectDependencies=["triggerid"],
    )
    if len(protos) != len(CANAIS):
        sys.exit(
            f"  [ERRO] Protótipos de canal esperados: {list(CANAIS)}; achados: {[p['description'] for p in protos]}"
        )
    for p in protos:
        if not dry:
            api.triggerprototype.update(triggerid=p["triggerid"], expression=CANAIS[p["description"]])
        _somar_deps(api, p, [trig[REINICIOU]["triggerid"], trig[SEM_SNMP]["triggerid"]], prototipo=True, dry=dry)
        wa.ok(
            f"'{p['description']}': só com uptime ≥ {UPTIME_MINIMO_CANAIS} e câmera cadastrada (IP ≠ {IP_CANAL_VAZIO}),"
            " depende do reinício e do SNMP"
        )


def ajustar_gravadores(api: ZabbixAPI, dry: bool) -> dict[str, dict[str, Any]]:
    """Sem SNMP/porta de mídia dependem do ping do gravador. Retorna {chave: host} dos gravadores."""
    wa.banner("2. Gravadores: queda = um alerta (ping)")
    grupos = api.hostgroup.get(output=["groupid"], filter={"name": list(GRUPOS_GRAVADOR)})
    gravadores = api.host.get(
        output=["hostid", "host", "name"], groupids=[g["groupid"] for g in grupos], selectTags=["tag", "value"]
    )
    ids = [g["hostid"] for g in gravadores]
    ping = _triggers(api, ids, PING)
    for descricao in (SEM_SNMP, PORTA_MIDIA):
        for hostid, t in _triggers(api, ids, descricao).items():
            if hostid in ping and _somar_deps(api, t, [ping[hostid]["triggerid"]], prototipo=False, dry=dry):
                wa.ok(f"{next(g['name'] for g in gravadores if g['hostid'] == hostid)}: '{descricao}' → ping")
    return {chave_gravador(g): g for g in gravadores}


def ajustar_cameras(api: ZabbixAPI, gravadores: dict[str, dict[str, Any]], dry: bool) -> None:
    """Ping de cada câmera depende do ping e do "reiniciou" do seu gravador."""
    wa.banner("3. Câmeras: ping depende do gravador")
    ids = [g["hostid"] for g in gravadores.values()]
    ping_grav, reinicio_grav = _triggers(api, ids, PING), _triggers(api, ids, REINICIOU)

    grupo = api.hostgroup.get(output=["groupid"], filter={"name": GRUPO_CAMERAS})
    cameras = api.host.get(output=["hostid", "name"], groupids=[grupo[0]["groupid"]], selectTags=["tag", "value"])
    ping_cam = _triggers(api, [c["hostid"] for c in cameras], PING)
    mudou, sem_gravador = 0, []
    for cam in cameras:
        grav = gravadores.get(gravador_da_camera(cam))
        if not grav or cam["hostid"] not in ping_cam:
            sem_gravador.append(cam["name"])
            continue
        desejadas = [t["triggerid"] for t in (ping_grav.get(grav["hostid"]), reinicio_grav.get(grav["hostid"])) if t]
        try:
            mudou += _somar_deps(api, ping_cam[cam["hostid"]], desejadas, prototipo=False, dry=dry)
        except APIRequestError as exc:
            wa.info(f"{cam['name']}: {exc}")
    wa.ok(f"{mudou} câmera(s) ligadas ao gravador; {len(cameras) - len(sem_gravador) - mudou} já estavam")
    if sem_gravador:
        wa.info(f"{len(sem_gravador)} sem gravador (faciais, antenas, stand alone) — ficam como estão")


def main() -> None:
    """Aplica (ou só mostra, com ``--dry-run``) as dependências gravador → câmeras."""
    parser = argparse.ArgumentParser(description="Gravador caiu ou reiniciou: um chamado só para ele.")
    parser.add_argument("--dry-run", action="store_true", help="só mostra o que mudaria")
    dry = parser.parse_args().dry_run
    wa.banner("Gravador caiu/reiniciou: um chamado só" + (" (DRY-RUN)" if dry else ""))
    api = ZabbixAPI(url=wa.ZABBIX_URL, token=wa.ZABBIX_TOKEN, skip_version_check=True)
    print(f"  Zabbix {api.api_version()} conectado")
    try:
        ajustar_template(api, dry)
        ajustar_cameras(api, ajustar_gravadores(api, dry), dry)
    except APIRequestError as exc:
        sys.exit(f"  [ERRO] {exc}")
    wa.banner("CONCLUÍDO" + (" (nada foi gravado)" if dry else ""))


if __name__ == "__main__":
    main()
