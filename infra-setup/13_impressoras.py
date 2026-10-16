#!/usr/bin/env python3
"""
Impressoras no Zabbix, cada uma com o template do seu modelo.

Lista vinda da planilha de impressoras (16/10/2026). Cria/ajusta (idempotente):

  - Template "SNMP Impressora (Printer-MIB)" (grupo Templates/Printer): modelo,
    série e contador de páginas, e descoberta dos suprimentos pela tabela
    padrão da Printer-MIB (``prtMarkerSupplies``) — toner, cilindro, fusor,
    kit de manutenção, o que o aparelho informar —, com o nível em % e
    trigger abaixo de ``PCT_BAIXO``. Serve para qualquer marca; os templates
    fixos ("SNMP - Lexmark COLOR") leem índices de impressora colorida
    Lexmark e erram nas mono e nas outras marcas.
  - Um host por impressora no grupo Impressoras, interface SNMP v2 com
    ``{$SNMP_COMMUNITY}``, nome visível "Impressora <unidade> - <local>
    (<marca> <modelo>)" e tags ``loja``, ``local``, ``marca``, ``modelo``,
    ``serie`` e ``ip``. Host que já existe (achado pelo IP) é ajustado:
    sai de outros grupos e troca de template (os itens do template antigo
    são apagados).

Template por modelo: Lexmark CX725 → "SNMP - Lexmark COLOR"; RICOH →
"SNMP Ricoh Printers"; o resto → "SNMP Impressora (Printer-MIB)".

Uso:
    uv run --no-project --with zabbix-utils --with urllib3 python3 infra-setup/13_impressoras.py [--dry-run]
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
from typing import Any, NamedTuple

_AQUI = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("whatsapp_alert_script", _AQUI / "09_create_whatsapp_alert.py")
wa = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(wa)  # type: ignore[union-attr]

from zabbix_utils import ZabbixAPI  # noqa: E402
from zabbix_utils.exceptions import APIRequestError  # noqa: E402

GRUPO = "Impressoras"
GRUPO_TEMPLATES = "Templates/Printer"
TEMPLATE = "snmp-impressora-printer-mib"
TEMPLATE_NOME = "SNMP Impressora (Printer-MIB)"
TPL_LEXMARK_COR = "SNMP - Lexmark COLOR"
TPL_RICOH = "SNMP Ricoh Printers"
PCT_BAIXO = 10

ITEM_SNMP, ITEM_CALCULADO = 20, 15
VALOR_FLOAT, VALOR_TEXTO_CURTO, VALOR_UINT = 0, 1, 3
PREPROC_JS = 21

# Printer-MIB (RFC 3805) e Host-Resources-MIB
OID_MODELO = "1.3.6.1.2.1.25.3.2.1.3.1"
OID_SERIE = "1.3.6.1.2.1.43.5.1.1.17.1"
OID_PAGINAS = "1.3.6.1.2.1.43.10.2.1.4.1.1"
OID_SUPRIMENTO_DESC = "1.3.6.1.2.1.43.11.1.1.6.1"
OID_SUPRIMENTO_MAX = "1.3.6.1.2.1.43.11.1.1.8.1"
OID_SUPRIMENTO_NIVEL = "1.3.6.1.2.1.43.11.1.1.9.1"

# A descrição do suprimento traz número de série/peça ("Black Toner Cartridge S/N:CRUM-...",
# "Fuser, PN 115R00114"): sai do nome, senão o item muda a cada troca de cartucho
JS_LIMPA_DESC = r"""
var linhas = JSON.parse(value);
for (var i = 0; i < linhas.length; i++) {
    linhas[i]['{#DESC}'] = linhas[i]['{#DESC}'].replace(/\s*(S\/N:|,\s*PN\s).*$/, '').replace(/\s+/g, ' ').trim();
}
return JSON.stringify(linhas);
"""


class Impressora(NamedTuple):
    """Uma linha da planilha."""

    loja: str
    local: str
    marca: str
    modelo: str
    ip: str
    serie: str


IMPRESSORAS: tuple[Impressora, ...] = (
    Impressora("Sede Centro", "A3 6º Andar", "RICOH", "IM C2000", "172.29.1.27", "3080R200536"),
    Impressora("Sede Centro", "7º Andar", "Lexmark", "CX725", "172.29.1.26", "75288320F2VYF"),
    Impressora("Sede Centro", "5º Andar", "Lexmark", "CX725", "172.29.1.32", "75287220117NM"),
    Impressora("Sede Centro", "Loja / Gadens", "Lexmark", "CX725", "172.29.1.33", "752872001168P"),
    Impressora("Sede Centro", "RH", "HP", "Color LaserJet Pro MFP 4303", "172.29.2.195", "BRBSTBD0CS"),
    Impressora("Sede Centro", "6º Andar", "Lexmark", "CX725", "172.29.1.25", "752880301255H"),
    Impressora("Sede Centro", "8º Andar", "Lexmark", "CX725", "172.29.1.29", "752872001169M"),
    Impressora("Shopping", "Balcão de Informações", "Samsung", "CLX-6260", "10.41.103.206", "Z99ZB07KB14H9SY"),
    Impressora("Shopping", "Balcão de Informações 2", "Samsung", "SL-M4070FR", "10.41.103.205", "ZDDPBQAJ500011L"),
    Impressora("Shopping", "Cobrança", "Samsung", "SL-M4070FR", "10.41.103.201", "ZER4BQAG20010DM"),
    Impressora("Shopping", "Marketing A3", "Xerox", "VersaLink C7130", "10.41.103.204", "QPD132033"),
    Impressora("Shopping", "Superintendência", "Samsung", "CLX-6260", "10.41.103.203", "Z99ZBQBGB000EPT"),
    Impressora("Triunfo", "Extrato", "Lexmark", "XM1246", "172.17.1.191", "701721740VXBT"),
    Impressora("Triunfo", "Laboratório Folhas", "Lexmark", "XM1246", "172.17.1.195", "701722940W6F0"),
    Impressora("Triunfo", "Administrativo", "RICOH", "IM C2500", "172.17.10.73", "3090R700372"),
    Impressora("Triunfo", "Laboratório Extrato", "Samsung", "CLX-6260", "172.17.10.198", "Z99ZBQAHA0001ER"),
    Impressora("Triunfo", "Recepção", "Lexmark", "XM1246", "172.17.10.44", "701721740VXBR"),
)


def template_do_modelo(imp: Impressora) -> str:
    """Nome técnico do template que serve para a impressora."""
    if imp.marca.lower() == "lexmark" and imp.modelo.upper().startswith("CX"):
        return TPL_LEXMARK_COR
    if imp.marca.upper() == "RICOH":
        return TPL_RICOH
    return TEMPLATE


def nome_visivel(imp: Impressora) -> str:
    """Ex.: "Impressora Shopping - Cobrança (Samsung SL-M4070FR)"."""
    return f"Impressora {imp.loja} - {imp.local} ({imp.marca} {imp.modelo})"


def nome_tecnico(imp: Impressora) -> str:
    """Nome técnico de host novo (o de host existente é mantido)."""
    return f"impressora_{imp.ip}"


def tags(imp: Impressora, existentes: list[dict[str, str]] | None = None) -> list[dict[str, str]]:
    """Tags da planilha; mantém as outras que o host já tinha (ex.: ``origem``)."""
    novas = {
        "loja": imp.loja,
        "local": imp.local,
        "marca": imp.marca,
        "modelo": imp.modelo,
        "serie": imp.serie,
        "ip": imp.ip,
    }
    resto = [{"tag": t["tag"], "value": t["value"]} for t in existentes or [] if t["tag"] not in novas]
    return resto + [{"tag": k, "value": v} for k, v in novas.items()]


def expr_baixo() -> str:
    """Suprimento abaixo de ``PCT_BAIXO``% (nível negativo = desconhecido, não alerta)."""
    chave = "impressora.suprimento.pct[{#SNMPINDEX}]"
    return f"last(/{TEMPLATE}/{chave})>=0 and last(/{TEMPLATE}/{chave})<{PCT_BAIXO}"


def _snmp(nome: str, chave: str, oid: str, tipo: int, delay: str, **extra: Any) -> dict[str, Any]:
    return {
        "name": nome,
        "key_": chave,
        "type": ITEM_SNMP,
        "snmp_oid": oid,
        "value_type": tipo,
        "delay": delay,
        "tags": [{"tag": "componente", "value": "impressora"}],
        **extra,
    }


ITENS = (
    _snmp("Modelo", "impressora.modelo", OID_MODELO, VALOR_TEXTO_CURTO, "1h", history="7d"),
    _snmp("Número de série", "impressora.serie", OID_SERIE, VALOR_TEXTO_CURTO, "1h", history="7d"),
    _snmp("Contador de páginas", "impressora.paginas", OID_PAGINAS, VALOR_UINT, "1h"),
)


def garantir_template(api: ZabbixAPI, dry: bool) -> str | None:
    """Cria o template Printer-MIB com itens, descoberta e protótipos (o que faltar)."""
    existente = api.template.get(filter={"host": TEMPLATE}, output=["templateid"])
    if existente:
        templateid = existente[0]["templateid"]
        wa.ok(f"template '{TEMPLATE_NOME}' já existe ({templateid})")
        if dry:
            return templateid
    elif dry:
        wa.info(f"criaria o template '{TEMPLATE_NOME}'")
        return None
    else:
        grupo = api.templategroup.get(filter={"name": GRUPO_TEMPLATES}, output=["groupid"])
        if not grupo:
            sys.exit(f"  [ERRO] grupo de templates '{GRUPO_TEMPLATES}' não encontrado")
        templateid = api.template.create(
            host=TEMPLATE,
            name=TEMPLATE_NOME,
            groups=[{"groupid": grupo[0]["groupid"]}],
            description="Qualquer impressora com SNMP: suprimentos pela Printer-MIB (infra-setup/13_impressoras.py).",
        )["templateids"][0]
        wa.ok(f"template '{TEMPLATE_NOME}' criado ({templateid})")

    chaves = {i["key_"] for i in api.item.get(templateids=[templateid], output=["key_"])}
    for item in ITENS:
        if item["key_"] not in chaves:
            api.item.create(hostid=templateid, **item)
            wa.ok(f"item {item['key_']} criado")

    regra = api.discoveryrule.get(
        templateids=[templateid], filter={"key_": "impressora.suprimentos"}, output=["itemid"]
    )
    if regra:
        ruleid = regra[0]["itemid"]
    else:
        ruleid = api.discoveryrule.create(
            hostid=templateid,
            name="Suprimentos",
            key_="impressora.suprimentos",
            type=ITEM_SNMP,
            snmp_oid=f"discovery[{{#DESC}},{OID_SUPRIMENTO_DESC}]",
            delay="1h",
            lifetime="7d",
            preprocessing=[
                {"type": PREPROC_JS, "params": JS_LIMPA_DESC, "error_handler": 0, "error_handler_params": ""}
            ],
        )["itemids"][0]
        wa.ok("descoberta de suprimentos criada")

    prototipos = {p["key_"] for p in api.itemprototype.get(discoveryids=[ruleid], output=["key_"])}
    tag_sup = [{"tag": "componente", "value": "impressora"}, {"tag": "suprimento", "value": "{#DESC}"}]
    novos = [
        _snmp(
            "{#DESC}: nível",
            "impressora.suprimento.nivel[{#SNMPINDEX}]",
            f"{OID_SUPRIMENTO_NIVEL}.{{#SNMPINDEX}}",
            VALOR_FLOAT,
            "30m",
            tags=tag_sup,
        ),
        _snmp(
            "{#DESC}: capacidade",
            "impressora.suprimento.max[{#SNMPINDEX}]",
            f"{OID_SUPRIMENTO_MAX}.{{#SNMPINDEX}}",
            VALOR_FLOAT,
            "1h",
            tags=tag_sup,
        ),
        {
            "name": "{#DESC}: %",
            "key_": "impressora.suprimento.pct[{#SNMPINDEX}]",
            "type": ITEM_CALCULADO,
            # nível -2 (desconhecido) / -3 (há algum) dá negativo: o trigger ignora
            "params": "last(//impressora.suprimento.nivel[{#SNMPINDEX}])*100"
            "/last(//impressora.suprimento.max[{#SNMPINDEX}])",
            "value_type": VALOR_FLOAT,
            "units": "%",
            "delay": "30m",
            "tags": tag_sup,
        },
    ]
    for p in novos:
        if p["key_"] not in prototipos:
            api.itemprototype.create(ruleid=ruleid, hostid=templateid, **p)
            wa.ok(f"protótipo {p['key_']} criado")

    nome_trigger = f"{{HOST.NAME}}: {{#DESC}} abaixo de {PCT_BAIXO}%"
    if not api.triggerprototype.get(discoveryids=[ruleid], filter={"description": nome_trigger}, output=["triggerid"]):
        api.triggerprototype.create(
            description=nome_trigger,
            expression=expr_baixo(),
            priority=2,
            manual_close=1,
            comments="Trocar o suprimento. Recipiente de toner usado: nível baixo = quase cheio.",
            tags=[{"tag": "componente", "value": "impressora"}],
        )
        wa.ok("protótipo de trigger criado")
    return templateid


def _interface(ip: str) -> dict[str, Any]:
    return {
        "type": 2,
        "main": 1,
        "useip": 1,
        "ip": ip,
        "dns": "",
        "port": "161",
        "details": {"version": 2, "bulk": 1, "community": "{$SNMP_COMMUNITY}"},
    }


def ajustar_hosts(api: ZabbixAPI, dry: bool) -> None:
    """Cria ou ajusta o host de cada impressora."""
    grupo = api.hostgroup.get(filter={"name": GRUPO}, output=["groupid"])
    if not grupo:
        sys.exit(f"  [ERRO] grupo '{GRUPO}' não encontrado")
    groupid = grupo[0]["groupid"]
    templates = {
        t["host"]: t["templateid"]
        for t in api.template.get(filter={"host": [TEMPLATE, TPL_LEXMARK_COR, TPL_RICOH]}, output=["host"])
    }
    por_ip: dict[str, dict[str, Any]] = {}
    for iface in api.hostinterface.get(filter={"ip": [i.ip for i in IMPRESSORAS]}, output=["ip", "hostid"]):
        por_ip.setdefault(iface["ip"], {"hostid": iface["hostid"]})
    hosts = (
        api.host.get(
            hostids=[h["hostid"] for h in por_ip.values()],
            output=["hostid", "host", "name"],
            selectHostGroups=["groupid"],
            selectParentTemplates=["templateid", "host"],
            selectTags=["tag", "value"],
            selectInterfaces=["interfaceid", "ip", "type"],
        )
        if por_ip
        else []
    )
    por_id = {h["hostid"]: h for h in hosts}

    for imp in IMPRESSORAS:
        alvo = template_do_modelo(imp)
        templateid = templates.get(alvo)
        host = por_id.get(por_ip.get(imp.ip, {}).get("hostid", ""))
        if host is None:
            if dry or not templateid:
                wa.info(f"criaria {nome_visivel(imp)} [{imp.ip}] com '{alvo}'")
                continue
            api.host.create(
                host=nome_tecnico(imp),
                name=nome_visivel(imp),
                groups=[{"groupid": groupid}],
                templates=[{"templateid": templateid}],
                interfaces=[_interface(imp.ip)],
                tags=tags(imp),
            )
            wa.ok(f"criada: {nome_visivel(imp)} [{imp.ip}] com '{alvo}'")
            continue

        atuais = {t["host"]: t["templateid"] for t in host["parentTemplates"]}
        sair = {n: i for n, i in atuais.items() if n != alvo}
        mudancas = []
        if host["name"] != nome_visivel(imp):
            mudancas.append(f"nome '{host['name']}' → '{nome_visivel(imp)}'")
        if [g["groupid"] for g in host["hostgroups"]] != [groupid]:
            mudancas.append("grupo → Impressoras")
        if alvo not in atuais or sair:
            mudancas.append(f"template {', '.join(atuais) or '-'} → {alvo}")
        if not any(i["type"] == "2" for i in host["interfaces"]):
            mudancas.append("interface SNMP")
        if dry:
            wa.info(f"{host['host']}: " + ("; ".join(mudancas) + "; tags" if mudancas else "só tags"))
            continue
        params: dict[str, Any] = {
            "hostid": host["hostid"],
            "name": nome_visivel(imp),
            "groups": [{"groupid": groupid}],
            "tags": tags(imp, host["tags"]),
        }
        if sair:
            params["templates_clear"] = [{"templateid": i} for i in sair.values()]
        if templateid:
            params["templates"] = [{"templateid": templateid}]
        api.host.update(**params)
        if not any(i["type"] == "2" for i in host["interfaces"]):
            api.hostinterface.create(hostid=host["hostid"], **{**_interface(imp.ip), "main": 1})
        wa.ok(f"{host['host']}: " + ("; ".join(mudancas) + "; tags" if mudancas else "só tags"))


def main() -> None:
    """Aplica (ou só mostra, com ``--dry-run``) o template e os hosts das impressoras."""
    parser = argparse.ArgumentParser(description="Impressoras no Zabbix com o template do modelo.")
    parser.add_argument("--dry-run", action="store_true", help="só mostra o que mudaria")
    dry = parser.parse_args().dry_run
    wa.banner("Impressoras" + (" (DRY-RUN)" if dry else ""))
    api = ZabbixAPI(url=wa.ZABBIX_URL, token=wa.ZABBIX_TOKEN, skip_version_check=True)
    print(f"  Zabbix {api.api_version()} conectado")
    try:
        garantir_template(api, dry)
        ajustar_hosts(api, dry)
    except APIRequestError as exc:
        sys.exit(f"  [ERRO] {exc}")
    wa.banner("CONCLUÍDO" + (" (nada foi gravado)" if dry else ""))


if __name__ == "__main__":
    main()
