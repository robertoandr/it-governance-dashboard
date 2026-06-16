"""Provisionamento idempotente dos itens/triggers de monitoramento do ITGov no Zabbix.

Cria (ou atualiza) os seguintes objetos no host configurado via HOST_NAME:

  Itens — Health (Fase 2)
  -----------------------
  itgov.health             HTTP agent master — GET /api/health/ready (texto completo)
  itgov.health.status      Dependente → JSONPath $.status
  itgov.health.db_latency  Dependente → JSONPath $.checks.sqlite.latency_ms

  Triggers — Health
  -----------------
  nodata 2m                Sem dado por 2 min → High (app não responde)
  status = not_ready       SQLite inacessível → High
  avg latency > 500ms      Latência alta 5 min → Warning

  Itens — SSL (Fase 3)
  --------------------
  ssl.cert.expiry_days[localhost,443]  Zabbix agent — dias até expirar o cert TLS

  Triggers — SSL (dependências encadeadas para suprimir spam)
  -----------------------------------------------------------
  < 30 dias   → Information  (janela de renovação manual — agir em breve)
  < 21 dias   → Warning      (suprime Information)
  < 10 dias   → High         (suprime Warning — ação urgente)
  <  3 dias   → Disaster     (suprime High — HTTPS vai cair)
  = -1        → High         (cert inacessível / coleta falhou)

Uso:
    export ZABBIX_URL="https://172.29.2.11/zabbix"
    export ZABBIX_TOKEN="<api_token>"
    export HOST_NAME="itgov-dev"
    python scripts/zabbix_provision.py

Variáveis de ambiente:
    ZABBIX_URL      URL base do Zabbix (obrigatório)
    ZABBIX_TOKEN    API token do Zabbix 5.4+ (obrigatório; não usar user/password)
    HOST_NAME       Nome exato do host no Zabbix (obrigatório)
    HEALTH_URL      URL do endpoint /api/health/ready (padrão: https://localhost:443/api/health/ready)
    SSL_HOST        Host do certificado a monitorar (padrão: localhost)
    SSL_PORT        Porta TLS a monitorar (padrão: 443)
    DRY_RUN         Se "1", imprime o que faria sem executar (padrão: 0)
"""

from __future__ import annotations

import os
import sys
from typing import Any

import structlog

log = structlog.get_logger(__name__)

# ── Configuração via env vars ─────────────────────────────────────────────────

ZABBIX_URL: str = os.environ.get("ZABBIX_URL", "")
ZABBIX_TOKEN: str = os.environ.get("ZABBIX_TOKEN", "")
HOST_NAME: str = os.environ.get("HOST_NAME", "")
HEALTH_URL: str = os.environ.get(
    "HEALTH_URL",
    "https://localhost:443/api/health/ready",
)
SSL_HOST: str = os.environ.get("SSL_HOST", "localhost")
SSL_PORT: str = os.environ.get("SSL_PORT", "443")
DRY_RUN: bool = os.environ.get("DRY_RUN", "0") == "1"

# Chaves dos itens — centralizadas para reusar nas expressões de trigger
KEY_MASTER = "itgov.health"
KEY_STATUS = "itgov.health.status"
KEY_LATENCY = "itgov.health.db_latency"
KEY_SSL_DAYS = f"ssl.cert.expiry_days[{SSL_HOST},{SSL_PORT}]"


def _require_env() -> None:
    missing = [v for v in ("ZABBIX_URL", "ZABBIX_TOKEN", "HOST_NAME") if not os.environ.get(v)]
    if missing:
        log.error("variaveis_obrigatorias_ausentes", faltando=missing)
        sys.exit(1)


# ── Helpers idempotentes ───────────────────────────────────────────────────────


def _get_or_none(api_method: Any, **kwargs: Any) -> dict[str, Any] | None:
    """Retorna o primeiro resultado ou None se não existir."""
    result = api_method(output=["itemid", "name", "key_", "type"], **kwargs)
    return result[0] if result else None


def _upsert_item(api: Any, hostid: str, payload: dict[str, Any]) -> str:
    """Cria ou atualiza um item; retorna o itemid."""
    existing = _get_or_none(
        api.item.get,
        hostids=hostid,
        filter={"key_": payload["key_"]},
    )
    if existing:
        itemid = existing["itemid"]
        if not DRY_RUN:
            api.item.update(itemid=itemid, **{k: v for k, v in payload.items() if k != "hostid"})
        log.info("item_atualizado", key=payload["key_"], itemid=itemid)
        return itemid
    if not DRY_RUN:
        result = api.item.create(**payload)
        itemid = result["itemids"][0]
    else:
        itemid = "DRY_RUN"
    log.info("item_criado", key=payload["key_"], itemid=itemid)
    return itemid


def _upsert_trigger(api: Any, hostid: str, description: str, expression: str, priority: int) -> str:
    """Cria ou atualiza um trigger; retorna o triggerid."""
    existing = api.trigger.get(
        hostids=hostid,
        filter={"description": description},
        output=["triggerid", "description"],
    )
    if existing:
        triggerid = existing[0]["triggerid"]
        if not DRY_RUN:
            api.trigger.update(
                triggerid=triggerid,
                expression=expression,
                priority=priority,
            )
        log.info("trigger_atualizado", descricao=description, triggerid=triggerid)
        return triggerid
    if not DRY_RUN:
        result = api.trigger.create(
            description=description,
            expression=expression,
            priority=priority,
        )
        triggerid = result["triggerids"][0]
    else:
        triggerid = "DRY_RUN"
    log.info("trigger_criado", descricao=description, triggerid=triggerid)
    return triggerid


def _upsert_trigger_dep(
    api: Any,
    hostid: str,
    description: str,
    expression: str,
    priority: int,
    depends_on_id: str | None = None,
) -> str:
    """Cria ou atualiza um trigger com dependência opcional; retorna o triggerid.

    depends_on_id: triggerid do trigger que DEVE estar PROBLEM para este ser suprimido.
    Na semântica do Zabbix: este trigger depende de depends_on_id, ou seja, só dispara
    se depends_on_id também estiver em PROBLEM — o que NÃO é o comportamento desejado aqui.

    Na verdade queremos o inverso: o trigger de menor severidade é suprimido quando o de
    maior severidade dispara. No Zabbix isso se faz configurando a dependência no trigger
    de MENOR severidade apontando para o de MAIOR severidade.
    """
    triggerid = _upsert_trigger(api, hostid, description, expression, priority)
    if depends_on_id and triggerid != "DRY_RUN":
        # Verifica se a dependência já existe antes de adicionar
        existing_deps = api.trigger.get(
            triggerids=triggerid,
            selectDependencies=["triggerid"],
            output=["triggerid"],
        )
        already_linked = any(
            d["triggerid"] == depends_on_id for d in (existing_deps[0].get("dependencies", []) if existing_deps else [])
        )
        if not already_linked:
            if not DRY_RUN:
                api.trigger.update(
                    triggerid=triggerid,
                    dependencies=[{"triggerid": depends_on_id}],
                )
            log.info("trigger_dependencia_adicionada", triggerid=triggerid, depende_de=depends_on_id)
    return triggerid


# ── Provisionamento SSL (Fase 3) ──────────────────────────────────────────────


def provision_ssl(api: Any, hostid: str) -> None:
    """Provisiona item e triggers de expiração do certificado TLS.

    Requer que o UserParameter ssl.cert.expiry_days[*] esteja configurado no
    agent2 via /etc/zabbix/zabbix_agent2.d/ssl_expiry.conf.

    Hierarquia de severidade (cada trigger suprime o de severidade inferior):
        Information (<30d) ← suprimido por Warning
        Warning     (<21d) ← suprimido por High
        High        (<10d) ← suprimido por Disaster
        Disaster    (< 3d)
        High        (=-1)  trigger isolado para falha de coleta
    """
    log.info("provision_ssl_iniciado", key=KEY_SSL_DAYS)

    # Resolve interface do agent no host (necessário para itens do tipo Zabbix agent)
    interfaces = api.hostinterface.get(
        hostids=hostid,
        output=["interfaceid", "type"],
        filter={"type": 1},  # type=1 → Zabbix agent
    )
    if not interfaces:
        log.error("interface_agent_nao_encontrada", hostid=hostid)
        return
    interfaceid: str = interfaces[0]["interfaceid"]

    # ── Item: dias até expirar o cert ─────────────────────────────────────────
    _upsert_item(
        api,
        hostid,
        {
            "hostid": hostid,
            "name": f"SSL cert expiry — {SSL_HOST}:{SSL_PORT} (dias restantes)",
            "key_": KEY_SSL_DAYS,
            "type": 0,  # Zabbix agent
            "value_type": 3,  # Unsigned integer
            "units": "days",
            "delay": "1h",
            "interfaceid": interfaceid,
        },
    )

    # ── Triggers encadeados (menor severidade depende da maior) ───────────────
    # Disaster < 3d — topo da cadeia, sem dependência
    t_disaster = _upsert_trigger(
        api,
        hostid,
        description=f"SSL cert CRITICO: expira em menos de 3 dias ({HOST_NAME})",
        expression=f"last(/{HOST_NAME}/{KEY_SSL_DAYS})<3 and last(/{HOST_NAME}/{KEY_SSL_DAYS})>=0",
        priority=5,  # Disaster
    )

    # High < 10d — suprimido por Disaster
    t_high = _upsert_trigger_dep(
        api,
        hostid,
        description=f"SSL cert URGENTE: expira em menos de 10 dias ({HOST_NAME})",
        expression=f"last(/{HOST_NAME}/{KEY_SSL_DAYS})<10 and last(/{HOST_NAME}/{KEY_SSL_DAYS})>=0",
        priority=4,  # High
        depends_on_id=t_disaster,
    )

    # Warning < 21d — suprimido por High
    t_warning = _upsert_trigger_dep(
        api,
        hostid,
        description=f"SSL cert: expira em menos de 21 dias ({HOST_NAME})",
        expression=f"last(/{HOST_NAME}/{KEY_SSL_DAYS})<21 and last(/{HOST_NAME}/{KEY_SSL_DAYS})>=0",
        priority=2,  # Warning
        depends_on_id=t_high,
    )

    # Information < 30d — suprimido por Warning
    _upsert_trigger_dep(
        api,
        hostid,
        description=f"SSL cert: expira em menos de 30 dias — renovar ({HOST_NAME})",
        expression=f"last(/{HOST_NAME}/{KEY_SSL_DAYS})<30 and last(/{HOST_NAME}/{KEY_SSL_DAYS})>=0",
        priority=1,  # Information
        depends_on_id=t_warning,
    )

    # High isolado: -1 indica falha de coleta (cert inacessível ou openssl timeout)
    _upsert_trigger(
        api,
        hostid,
        description=f"SSL cert: coleta falhou / cert inacessivel ({HOST_NAME})",
        expression=f"last(/{HOST_NAME}/{KEY_SSL_DAYS})=-1",
        priority=4,  # High
    )

    log.info("provision_ssl_concluido", key=KEY_SSL_DAYS)


# ── Provisionamento principal ─────────────────────────────────────────────────


def provision() -> None:
    """Executa o provisionamento completo de forma idempotente."""
    from zabbix_utils import ZabbixAPI
    from zabbix_utils.exceptions import APIRequestError

    _require_env()

    log.info(
        "provisionamento_iniciado",
        host=HOST_NAME,
        url=ZABBIX_URL,
        dry_run=DRY_RUN,
    )

    api = ZabbixAPI(url=ZABBIX_URL, skip_version_check=True)
    try:
        api.login(token=ZABBIX_TOKEN)
    except APIRequestError as exc:
        log.error("autenticacao_falhou", erro=str(exc))
        sys.exit(1)

    # Resolver hostid
    hosts = api.host.get(filter={"host": HOST_NAME}, output=["hostid", "host"])
    if not hosts:
        log.error("host_nao_encontrado", host=HOST_NAME)
        sys.exit(1)
    hostid: str = hosts[0]["hostid"]
    log.info("host_encontrado", host=HOST_NAME, hostid=hostid)

    # ── Item master: HTTP agent ────────────────────────────────────────────────
    master_id = _upsert_item(
        api,
        hostid,
        {
            "hostid": hostid,
            "name": "ITGov Health (readiness)",
            "key_": KEY_MASTER,
            "type": 19,  # HTTP agent
            "value_type": 4,  # Text
            "url": HEALTH_URL,
            "retrieve_mode": 0,  # Corpo da resposta
            "status_codes": "200,503",
            "verify_peer": 0,
            "verify_host": 0,
            "delay": "30s",
            "timeout": "5s",
            "follow_redirects": 1,
        },
    )

    # ── Item dependente: $.status ──────────────────────────────────────────────
    _upsert_item(
        api,
        hostid,
        {
            "hostid": hostid,
            "name": "ITGov Health — status",
            "key_": KEY_STATUS,
            "type": 18,  # Dependent item
            "value_type": 1,  # Character
            "master_itemid": master_id,
            "delay": "0",
            "preprocessing": [
                {
                    "type": 12,  # JSONPath
                    "params": "$.status",
                    "error_handler": 1,
                }
            ],
        },
    )

    # ── Item dependente: $.checks.database.latency_ms ─────────────────────────
    _upsert_item(
        api,
        hostid,
        {
            "hostid": hostid,
            "name": "ITGov Health — DB latency (ms)",
            "key_": KEY_LATENCY,
            "type": 18,  # Dependent item
            "value_type": 0,  # Float
            "master_itemid": master_id,
            "delay": "0",
            "units": "ms",
            "preprocessing": [
                {
                    "type": 12,  # JSONPath
                    "params": "$.checks.sqlite.latency_ms",
                    "error_handler": 1,
                }
            ],
        },
    )

    # ── Triggers (sintaxe Zabbix 7.0) ─────────────────────────────────────────
    _upsert_trigger(
        api,
        hostid,
        description=f"ITGov: app não responde ({HOST_NAME})",
        expression=f"nodata(/{HOST_NAME}/{KEY_MASTER},2m)=1",
        priority=4,  # High
    )

    _upsert_trigger(
        api,
        hostid,
        description=f"ITGov: SQLite inacessível ({HOST_NAME})",
        expression=f'last(/{HOST_NAME}/{KEY_STATUS})="not_ready"',
        priority=4,  # High
    )

    _upsert_trigger(
        api,
        hostid,
        description=f"ITGov: latência DB alta ({HOST_NAME})",
        expression=f"avg(/{HOST_NAME}/{KEY_LATENCY},5m)>500",
        priority=2,  # Warning
    )

    # ── Fase 3: SSL ───────────────────────────────────────────────────────────
    provision_ssl(api, hostid)

    log.info("provisionamento_concluido", dry_run=DRY_RUN)
    api.logout()


if __name__ == "__main__":
    provision()
