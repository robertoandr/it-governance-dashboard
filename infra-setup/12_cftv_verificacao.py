#!/usr/bin/env python3
"""
CFTV: canal sem gravar ou câmera sem imagem → aviso no WhatsApp, sem chamado.

O dashboard verifica sozinho, direto nos gravadores Intelbras, se cada canal
com câmera está gravando (a cada 30 min) e se a câmera entrega imagem (fotos
às 8h, 11h, 14h e 17h) — ver ``app/services/cftv_verificacao.py``. O
resultado chega ao Zabbix em dois itens trapper por gravador (texto com os
canais com problema; vazio = tudo ok). Cria/ajusta (idempotente):

  - Template "CFTV Verificação (dashboard)" com os itens
    ``cftv.verif.sem_gravar`` e ``cftv.verif.sem_imagem`` e um trigger para
    cada (tag ``alerta=so-whatsapp``), ligado a todos os gravadores
    (CFTV/DVRs e CFTV/NVRs). Gravador que o dashboard não consegue verificar
    não recebe valor e não alerta.
  - Ação "CFTV — Sem gravar/sem imagem (só WhatsApp)": manda no WhatsApp
    com as mesmas operações da ação "CFTV — Câmera com rede lenta (só
    WhatsApp)" (no horário vai na hora; fora dele e no fim de semana, nos
    resumos das 08:00, 14:00 e 18:00).
  - Ação "Governança — Chamado Zendesk": não abre chamado para
    ``alerta=so-whatsapp``.

Uso:
    uv run --no-project --with zabbix-utils --with urllib3 python3 infra-setup/12_cftv_verificacao.py [--dry-run]
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

# Nome técnico só aceita letras sem acento, números, espaço, ponto, traço e sublinhado
TEMPLATE = "cftv-verificacao-dashboard"
TEMPLATE_NOME = "CFTV Verificação (dashboard)"
TEMPLATE_GRUPO_DE = "Intelbras NVR SNMP"  # o novo template vai para o mesmo grupo deste
GRUPOS_GRAVADOR = ("CFTV/DVRs", "CFTV/NVRs")
ACAO_CHAMADO = "Governança — Chamado Zendesk"
ACAO_MODELO = "CFTV — Câmera com rede lenta (só WhatsApp)"
ACAO_NOVA = "CFTV — Sem gravar/sem imagem (só WhatsApp)"
TAG_ALERTA = ("alerta", "so-whatsapp")

# Condições de ação (Zabbix 7.4)
COND_TAG_VALOR = "26"
COND_MANUTENCAO = "16"
OP_IGUAL, OP_DIFERENTE, OP_NAO_EM = "0", "1", "11"
OP_AVISAR_ENVOLVIDOS = "11"  # tipo de operação de recuperação

ITENS = (
    ("cftv.verif.sem_gravar", "Canais sem gravar (verificação do dashboard)"),
    ("cftv.verif.sem_imagem", "Câmeras sem imagem (verificação do dashboard)"),
)
TRIGGERS = (
    # (chave, nome, severidade, valor da tag problema)
    ("cftv.verif.sem_gravar", "CFTV {HOST.NAME}: sem gravar — {ITEM.LASTVALUE}", "3", "gravacao"),
    ("cftv.verif.sem_imagem", "CFTV {HOST.NAME}: câmera sem imagem — {ITEM.LASTVALUE}", "2", "imagem"),
)


def expr_trigger(chave: str) -> str:
    """Problema enquanto o último texto enviado pelo dashboard não estiver vazio."""
    return f"length(last(/{TEMPLATE}/{chave}))>0"


def condicao_sem_chamado() -> dict[str, str]:
    """Condição da ação de chamado: tag ``alerta`` diferente de ``so-whatsapp``."""
    return {
        "conditiontype": COND_TAG_VALOR,
        "operator": OP_DIFERENTE,
        "value": TAG_ALERTA[1],
        "value2": TAG_ALERTA[0],
    }


def _condicao_escrita(c: dict[str, Any]) -> dict[str, str]:
    nova = {k: c[k] for k in ("conditiontype", "operator", "formulaid") if k in c}
    # A de manutenção (16) não aceita value; a de tag (26) precisa de value2
    if c["conditiontype"] != COND_MANUTENCAO:
        nova["value"] = c.get("value", "")
    if c.get("value2"):
        nova["value2"] = c["value2"]
    return nova


def filtro_sem_chamado(filtro: dict[str, Any]) -> dict[str, Any] | None:
    """Filtro da ação de chamado com a exclusão de ``alerta=so-whatsapp``, ou None se já tem.

    Mantém as condições atuais (só com os campos aceitos na escrita) e soma a
    nova com o próximo ``formulaid`` livre, ligada por ``and`` à fórmula.
    """
    conds = filtro.get("conditions", [])
    alvo = condicao_sem_chamado()
    if any(
        c["conditiontype"] == alvo["conditiontype"]
        and c["operator"] == alvo["operator"]
        and c.get("value") == alvo["value"]
        and c.get("value2") == alvo["value2"]
        for c in conds
    ):
        return None
    usados = {c.get("formulaid") for c in conds}
    letra = next(chr(n) for n in range(ord("A"), ord("Z") + 1) if chr(n) not in usados)
    formula = filtro.get("formula") or " and ".join(sorted(u for u in usados if u))
    return {
        "evaltype": "3",
        "formula": f"({formula}) and {letra}" if " or " in formula else f"{formula} and {letra}",
        "conditions": [_condicao_escrita(c) for c in conds] + [{**alvo, "formulaid": letra}],
    }


def operacoes_escrita(operacoes: list[dict[str, Any]], recuperacao: bool = False) -> list[dict[str, Any]]:
    """Operações de outra ação só com os campos aceitos na escrita (Zabbix 7.4 recusa ids).

    Operação de recuperação não tem escalonamento nem condições: o Zabbix
    recusa ``evaltype``, ``esc_*`` e ``opconditions`` nela. "Avisar todos os
    envolvidos" também recusa ``mediatypeid`` na mensagem.
    """
    campos_op = (
        ("operationtype",)
        if recuperacao
        else ("operationtype", "esc_period", "esc_step_from", "esc_step_to", "evaltype")
    )
    listas = ("opmessage_usr", "opmessage_grp") if recuperacao else ("opmessage_usr", "opmessage_grp", "opconditions")
    resultado = []
    for op in operacoes:
        nova = {k: op[k] for k in campos_op if k in op}
        if "opmessage" in op:
            # "Avisar todos os envolvidos" (11) usa a mídia de quem recebeu o alerta: não aceita mediatypeid
            campos: tuple[str, ...] = (
                ("default_msg",)
                if op.get("operationtype") == OP_AVISAR_ENVOLVIDOS
                else (
                    "default_msg",
                    "mediatypeid",
                )
            )
            if op["opmessage"].get("default_msg") == "0":
                campos += ("subject", "message")
            nova["opmessage"] = {k: v for k, v in op["opmessage"].items() if k in campos}
        for chave in listas:
            if op.get(chave):
                nova[chave] = [
                    {k: v for k, v in item.items() if k not in ("operationid", "actionid")} for item in op[chave]
                ]
        resultado.append(nova)
    return resultado


def _grupo_do_template(api: ZabbixAPI) -> str:
    modelo = api.template.get(
        filter={"host": TEMPLATE_GRUPO_DE}, output=["templateid"], selectTemplateGroups=["groupid"]
    )
    if not modelo or not modelo[0].get("templategroups"):
        sys.exit(f"  [ERRO] template '{TEMPLATE_GRUPO_DE}' não encontrado")
    return modelo[0]["templategroups"][0]["groupid"]


def garantir_template(api: ZabbixAPI, dry: bool) -> str | None:
    """Cria o template com itens e triggers (o que faltar). Devolve o templateid."""
    existente = api.template.get(filter={"host": TEMPLATE}, output=["templateid"])
    if existente:
        templateid = existente[0]["templateid"]
        wa.ok(f"template '{TEMPLATE_NOME}' já existe ({templateid})")
    elif dry:
        wa.info(f"criaria o template '{TEMPLATE_NOME}' ({TEMPLATE})")
        return None
    else:
        templateid = api.template.create(
            host=TEMPLATE, name=TEMPLATE_NOME, groups=[{"groupid": _grupo_do_template(api)}]
        )["templateids"][0]
        wa.ok(f"template '{TEMPLATE_NOME}' criado ({templateid})")

    chaves = {i["key_"] for i in api.item.get(templateids=[templateid], output=["key_"])}
    for chave, nome in ITENS:
        if chave in chaves:
            continue
        if dry:
            wa.info(f"criaria o item {chave}")
            continue
        api.item.create(
            hostid=templateid,
            name=nome,
            key_=chave,
            type=2,  # trapper
            value_type=4,  # texto
            history="30d",
            description="Enviado pelo dashboard (verificação de gravação/imagem). Vazio = tudo ok.",
            tags=[{"tag": "componente", "value": "cftv"}],
        )
        wa.ok(f"item {chave} criado")

    nomes = {t["description"] for t in api.trigger.get(templateids=[templateid], output=["description"])}
    for chave, nome, severidade, problema in TRIGGERS:
        if nome in nomes:
            continue
        if dry:
            wa.info(f"criaria o trigger '{nome}'")
            continue
        api.trigger.create(
            description=nome,
            expression=expr_trigger(chave),
            priority=severidade,
            manual_close=1,
            comments="Verificação automática do dashboard (/gov/cftv). Some sozinho quando o canal volta.",
            tags=[
                {"tag": TAG_ALERTA[0], "value": TAG_ALERTA[1]},
                {"tag": "componente", "value": "cftv"},
                {"tag": "problema", "value": problema},
            ],
        )
        wa.ok(f"trigger '{nome}' criado")
    return templateid


def ligar_gravadores(api: ZabbixAPI, templateid: str | None, dry: bool) -> None:
    """Liga o template a todos os gravadores que ainda não o têm."""
    grupos = api.hostgroup.get(filter={"name": list(GRUPOS_GRAVADOR)}, output=["groupid"])
    hosts = api.host.get(
        groupids=[g["groupid"] for g in grupos], output=["hostid", "name"], selectParentTemplates=["templateid"]
    )
    faltam = [h for h in hosts if not templateid or templateid not in {t["templateid"] for t in h["parentTemplates"]}]
    if not faltam:
        wa.ok(f"template já ligado aos {len(hosts)} gravadores")
        return
    if dry or not templateid:
        wa.info(f"ligaria o template a {len(faltam)} gravador(es): " + ", ".join(h["name"] for h in faltam))
        return
    api.template.massadd(templates=[{"templateid": templateid}], hosts=[{"hostid": h["hostid"]} for h in faltam])
    wa.ok(f"template ligado a {len(faltam)} gravador(es)")


def garantir_acao_whatsapp(api: ZabbixAPI, dry: bool) -> None:
    """Cria a ação só WhatsApp copiando as operações da ação de rede lenta."""
    if api.action.get(filter={"name": ACAO_NOVA}, output=["actionid"]):
        wa.ok(f"ação '{ACAO_NOVA}' já existe")
        return
    modelo = api.action.get(
        filter={"name": ACAO_MODELO},
        output=["actionid", "esc_period"],
        selectOperations="extend",
        selectRecoveryOperations="extend",
    )
    if not modelo:
        sys.exit(f"  [ERRO] ação '{ACAO_MODELO}' não encontrada (rode antes a reconferência de 14/10)")
    if dry:
        wa.info(f"criaria a ação '{ACAO_NOVA}' (operações de '{ACAO_MODELO}')")
        return
    api.action.create(
        name=ACAO_NOVA,
        eventsource=0,
        status=0,
        # Mesmo passo de envio do modelo: o WhatsApp sai no mesmo tempo que o da rede lenta
        esc_period=modelo[0]["esc_period"],
        filter={
            "evaltype": "0",
            "conditions": [
                {"conditiontype": COND_MANUTENCAO, "operator": OP_NAO_EM},
                {
                    "conditiontype": COND_TAG_VALOR,
                    "operator": OP_IGUAL,
                    "value": TAG_ALERTA[1],
                    "value2": TAG_ALERTA[0],
                },
            ],
        },
        operations=operacoes_escrita(modelo[0]["operations"]),
        recovery_operations=operacoes_escrita(modelo[0].get("recovery_operations") or [], recuperacao=True),
    )
    wa.ok(f"ação '{ACAO_NOVA}' criada")


def ajustar_acao_chamado(api: ZabbixAPI, dry: bool) -> None:
    """A ação de chamado deixa de pegar ``alerta=so-whatsapp``."""
    acao = api.action.get(filter={"name": ACAO_CHAMADO}, output=["actionid"], selectFilter="extend")
    if not acao:
        sys.exit(f"  [ERRO] ação '{ACAO_CHAMADO}' não encontrada")
    novo = filtro_sem_chamado(acao[0]["filter"])
    if novo is None:
        wa.ok(f"'{ACAO_CHAMADO}' já ignora alerta=so-whatsapp")
        return
    if dry:
        wa.info(f"'{ACAO_CHAMADO}': fórmula passaria a ser {novo['formula']}")
        return
    api.action.update(actionid=acao[0]["actionid"], filter=novo)
    wa.ok(f"'{ACAO_CHAMADO}': fórmula {novo['formula']}")


def main() -> None:
    """Aplica (ou só mostra, com ``--dry-run``) o template, a ação e o ajuste da ação de chamado."""
    parser = argparse.ArgumentParser(description="CFTV: sem gravar/sem imagem → WhatsApp, sem chamado.")
    parser.add_argument("--dry-run", action="store_true", help="só mostra o que mudaria")
    dry = parser.parse_args().dry_run
    wa.banner("CFTV: sem gravar / sem imagem" + (" (DRY-RUN)" if dry else ""))
    api = ZabbixAPI(url=wa.ZABBIX_URL, token=wa.ZABBIX_TOKEN, skip_version_check=True)
    print(f"  Zabbix {api.api_version()} conectado")
    try:
        # Primeiro a ação de chamado: assim nenhum alerta novo abre chamado
        ajustar_acao_chamado(api, dry)
        garantir_acao_whatsapp(api, dry)
        ligar_gravadores(api, garantir_template(api, dry), dry)
    except APIRequestError as exc:
        sys.exit(f"  [ERRO] {exc}")
    wa.banner("CONCLUÍDO" + (" (nada foi gravado)" if dry else ""))


if __name__ == "__main__":
    main()
