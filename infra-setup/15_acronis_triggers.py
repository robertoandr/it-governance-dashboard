#!/usr/bin/env python3
"""
Cyber: alertas do Acronis viram triggers no Zabbix/dashboard (sem e-mail).

O agente ``app/services/acronis_triggers.py`` lê os alertas do Acronis a cada
10 minutos e manda, por categoria, o texto "N máquinas: A, B, C" (vazio = nada)
para o item trapper ``acronis.alerta[<categoria>]``. Cria/ajusta (idempotente):

  - Grupo "Cyber" e host "acronis-alertas-dashboard" (sem interface).
  - Um item trapper (texto, 30 dias) e um trigger por categoria:
    "Acronis — <categoria>: {ITEM.LASTVALUE}", em problema enquanto o texto não
    estiver vazio, com a tag ``alerta=so-dashboard``.
  - Na ação "Governança — Chamado Zendesk", a exclusão de ``alerta=so-dashboard``
    (nenhuma outra ação pega essa tag: fica só no Zabbix e no NOC do dashboard).

Decisão de 05/11/2026: no lugar dos avisos por e-mail do console Acronis.

Uso:
    uv run --no-project --with zabbix-utils --with urllib3 python3 infra-setup/15_acronis_triggers.py [--dry-run]
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
from typing import Any

_AQUI = Path(__file__).resolve().parent


def _carregar(nome: str, arquivo: str) -> Any:
    spec = importlib.util.spec_from_file_location(nome, _AQUI / arquivo)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


wa = _carregar("whatsapp_alert_script", "09_create_whatsapp_alert.py")
cftv12 = _carregar("cftv_verificacao_script", "12_cftv_verificacao.py")

from zabbix_utils import ZabbixAPI  # noqa: E402
from zabbix_utils.exceptions import APIRequestError  # noqa: E402

GRUPO = "Cyber"
HOST = "acronis-alertas-dashboard"
HOST_NOME = "Acronis — alertas (dashboard)"
ACAO_CHAMADO = "Governança — Chamado Zendesk"
TAG_ALERTA = ("alerta", "so-dashboard")

# Espelha app/services/acronis_triggers.CATEGORIAS (chave, nome, severidade)
CATEGORIAS: tuple[tuple[str, str, int], ...] = (
    ("edr_critico", "EDR crítico não mitigado", 4),
    ("malware", "Malware ou ameaça", 4),
    ("backup", "Falha de backup", 4),
    ("protecao", "Proteção com falha", 3),
    ("cota", "Sem licença (cota)", 3),
    ("offline", "Agente offline", 2),
)


def chave_item(categoria: str) -> str:
    """Chave do item trapper da categoria."""
    return f"acronis.alerta[{categoria}]"


def nome_trigger(nome: str) -> str:
    """Nome do trigger da categoria."""
    return f"Acronis — {nome}: {{ITEM.LASTVALUE}}"


def expr_trigger(categoria: str) -> str:
    """Problema enquanto o último texto enviado não estiver vazio."""
    return f"length(last(/{HOST}/{chave_item(categoria)}))>0"


def condicao_sem_chamado() -> dict[str, str]:
    """Condição da ação de chamado: tag ``alerta`` diferente de ``so-dashboard``."""
    return {
        "conditiontype": cftv12.COND_TAG_VALOR,
        "operator": cftv12.OP_DIFERENTE,
        "value": TAG_ALERTA[1],
        "value2": TAG_ALERTA[0],
    }


def filtro_sem_chamado(filtro: dict[str, Any]) -> dict[str, Any] | None:
    """Filtro da ação de chamado com a exclusão de ``alerta=so-dashboard``, ou None se já tem."""
    conds = filtro.get("conditions", [])
    alvo = condicao_sem_chamado()
    if any(all(c.get(k) == alvo[k] for k in ("conditiontype", "operator", "value", "value2")) for c in conds):
        return None
    usados = {c.get("formulaid") for c in conds}
    letra = next(chr(n) for n in range(ord("A"), ord("Z") + 1) if chr(n) not in usados)
    formula = filtro.get("formula") or " and ".join(sorted(u for u in usados if u))
    return {
        "evaltype": "3",
        "formula": f"({formula}) and {letra}" if " or " in formula else f"{formula} and {letra}",
        "conditions": [cftv12._condicao_escrita(c) for c in conds] + [{**alvo, "formulaid": letra}],
    }


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
        description="Recebe do dashboard os alertas do Acronis por categoria. Ver infra-setup/15_acronis_triggers.py.",
        tags=[{"tag": "componente", "value": "cyber"}],
    )["hostids"][0]
    wa.ok(f"host '{HOST_NOME}' criado ({hostid})")
    return hostid


def garantir_categoria(api: ZabbixAPI, hostid: str | None, chave: str, nome: str, sev: int, dry: bool) -> None:
    """Item trapper e trigger de uma categoria (o que faltar)."""
    item, trigger = chave_item(chave), nome_trigger(nome)
    if hostid is None:
        wa.info(f"criaria o item {item} e o trigger '{trigger}'")
        return
    if api.item.get(hostids=[hostid], filter={"key_": item}, output=["itemid"]):
        wa.ok(f"item {item} já existe")
    elif dry:
        wa.info(f"criaria o item {item}")
    else:
        api.item.create(
            hostid=hostid,
            name=f"Acronis: {nome}",
            key_=item,
            type=2,  # trapper
            value_type=4,  # texto
            history="30d",
            description="Enviado pelo dashboard a cada 10 min. Vazio = nenhuma máquina na categoria.",
            tags=[{"tag": "componente", "value": "cyber"}, {"tag": "acronis", "value": chave}],
        )
        wa.ok(f"item {item} criado")

    if api.trigger.get(hostids=[hostid], filter={"description": trigger}, output=["triggerid"]):
        wa.ok(f"trigger '{trigger}' já existe")
        return
    if dry:
        wa.info(f"criaria o trigger '{trigger}'")
        return
    api.trigger.create(
        description=trigger,
        expression=expr_trigger(chave),
        priority=str(sev),
        manual_close=1,
        comments="Alertas do Acronis lidos pelo dashboard (/gov/cyber/noc, bloco Triggers do Acronis). Some sozinho.",
        tags=[
            {"tag": TAG_ALERTA[0], "value": TAG_ALERTA[1]},
            {"tag": "componente", "value": "cyber"},
            {"tag": "acronis", "value": chave},
        ],
    )
    wa.ok(f"trigger '{trigger}' criado")


def ajustar_acao_chamado(api: ZabbixAPI, dry: bool) -> None:
    """A ação de chamado deixa de pegar ``alerta=so-dashboard``."""
    acao = api.action.get(filter={"name": ACAO_CHAMADO}, output=["actionid"], selectFilter="extend")
    if not acao:
        sys.exit(f"  [ERRO] ação '{ACAO_CHAMADO}' não encontrada")
    novo = filtro_sem_chamado(acao[0]["filter"])
    if novo is None:
        wa.ok(f"'{ACAO_CHAMADO}' já ignora alerta=so-dashboard")
        return
    if dry:
        wa.info(f"'{ACAO_CHAMADO}': fórmula passaria a ser {novo['formula']}")
        return
    api.action.update(actionid=acao[0]["actionid"], filter=novo)
    wa.ok(f"'{ACAO_CHAMADO}': fórmula {novo['formula']}")


def main() -> None:
    """Aplica (ou só mostra, com ``--dry-run``) grupo, host, itens, triggers e a exclusão na ação."""
    parser = argparse.ArgumentParser(description="Cyber: alertas do Acronis → triggers no Zabbix (sem e-mail).")
    parser.add_argument("--dry-run", action="store_true", help="só mostra o que mudaria")
    dry = parser.parse_args().dry_run
    wa.banner("Cyber: triggers do Acronis" + (" (DRY-RUN)" if dry else ""))
    api = ZabbixAPI(url=wa.ZABBIX_URL, token=wa.ZABBIX_TOKEN, skip_version_check=True)
    print(f"  Zabbix {api.api_version()} conectado")
    try:
        # Primeiro a exclusão: trigger novo nunca chega a abrir chamado
        ajustar_acao_chamado(api, dry)
        hostid = garantir_host(api, garantir_grupo(api, dry), dry)
        for chave, nome, sev in CATEGORIAS:
            garantir_categoria(api, hostid, chave, nome, sev, dry)
    except APIRequestError as exc:
        sys.exit(f"  [ERRO] {exc}")


if __name__ == "__main__":
    main()
