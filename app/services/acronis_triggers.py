"""Triggers do Acronis no dashboard (no lugar dos avisos por e-mail do console).

A cada ``INTERVALO`` o agente lê os alertas ativos do Acronis Cyber Protect
(``/api/alert_manager/v1/alerts``), separa os que importam em categorias e,
para cada uma, manda ao Zabbix o texto "N máquina(s): A, B, C" no item trapper
``acronis.alerta[<categoria>]`` do host ``acronis-alertas-dashboard`` (vazio =
nada na categoria). O trigger de cada item (``infra-setup/15_acronis_triggers.py``)
fica em problema enquanto o texto não estiver vazio e leva a tag
``alerta=so-dashboard``: aparece nos problemas do Zabbix e no NOC de Segurança
do dashboard, sem abrir chamado e sem WhatsApp.

A última leitura fica num arquivo JSON no diretório temporário do container
(lido por qualquer worker) e alimenta o bloco "Triggers do Acronis" do NOC.

Categorias (decisão de 05/11/2026, plano de implantação do Acronis, item 0.1):

- ``edr_critico`` — incidente EDR crítico não mitigado
- ``malware`` — URL maliciosa, quarentena que falhou, ransomware/ameaça
- ``protecao`` — serviço de proteção parado, driver ausente, conflito de
  antimalware, isolamento que falhou, definições desatualizadas
- ``cota`` — máquina sem licença suficiente (``OfferingItemIsNotSufficient``)
- ``offline`` — agente offline há 20/30 dias (``MachineOffline*``)

Backup de máquina não é contratado no plano da Gadens (05/11/2026): alertas de
backup ficam de fora.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import json
import os
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import httpx
import requests
import structlog
from pydantic import BaseModel

from itgov.services import acronis_cyber

if TYPE_CHECKING:
    from flask import Flask

log = structlog.get_logger(__name__)

INTERVALO = timedelta(minutes=10)
HOST_ZABBIX = "acronis-alertas-dashboard"
_PRIMEIRA_RODADA_S = 90
_LIMITE_ALERTAS = 5000
_MAX_NOMES = 8
_MAX_TEXTO = 250
_TRAVA = os.path.join(tempfile.gettempdir(), "itgov-acronis-triggers.lock")
_ARQUIVO = os.path.join(tempfile.gettempdir(), "itgov-acronis-triggers.json")


class Categoria(BaseModel):
    """Uma categoria de trigger.

    Attributes:
        chave: Usada na chave do item (``acronis.alerta[<chave>]``).
        nome: Nome curto mostrado no trigger e no NOC.
        severidade: Severidade do trigger no Zabbix (2 aviso, 3 média, 4 alta).
    """

    chave: str
    nome: str
    severidade: int


CATEGORIAS: tuple[Categoria, ...] = (
    Categoria(chave="edr_critico", nome="EDR crítico não mitigado", severidade=4),
    Categoria(chave="malware", nome="Malware ou ameaça", severidade=4),
    Categoria(chave="protecao", nome="Proteção com falha", severidade=3),
    Categoria(chave="cota", nome="Sem licença (cota)", severidade=3),
    Categoria(chave="offline", nome="Agente offline", severidade=2),
)

_PROTECAO = frozenset(
    {
        "ProtectionServiceNotWorking",
        "CPSDriverNotInstalled",
        "WSCProductConflictWithAntimalwareProtection",
        "FailToIsolate",
        "MiMonitoringAntimalwareDefinitionsOutdated",
    }
)
_MALWARE = frozenset({"MaliciousUrlDetected", "QuarantineFailed"})
_TERMOS_MALWARE = ("ransomware", "malware", "threat", "activeprotection")


def chave_item(categoria: str) -> str:
    """Chave do item trapper da categoria no Zabbix."""
    return f"acronis.alerta[{categoria}]"


def _sim(valor: object) -> bool:
    return str(valor).strip().lower() == "true"


def categoria_do_alerta(raw: dict[str, Any]) -> str | None:
    """Em que categoria o alerta entra (ou ``None`` se é ruído de monitoramento).

    Args:
        raw: Item cru de ``/api/alert_manager/v1/alerts``.

    Returns:
        A chave da categoria, ou ``None``.
    """
    tipo = str(raw.get("type") or "")
    baixo = tipo.lower()
    sev = str(raw.get("severity") or "").lower()
    detalhes = raw.get("details") or {}
    if tipo == "EDRIncidentDetected":
        if _sim(detalhes.get("isMitigated")):
            return None
        if _sim(detalhes.get("isMalicious")):
            return "malware"
        return "edr_critico" if sev == "critical" else None
    # Proteção antes de malware: "...AntimalwareProtection" tem "malware" no nome
    if tipo in _PROTECAO:
        return "protecao"
    if tipo in _MALWARE or any(t in baixo for t in _TERMOS_MALWARE):
        return "malware"
    if tipo == "OfferingItemIsNotSufficient":
        return "cota"
    if tipo.startswith("MachineOffline"):
        return "offline"
    return None


def _maquina(raw: dict[str, Any]) -> str:
    """Nome curto da máquina: sem o domínio (``PC-01.grupogadens.com.br`` → ``PC-01``)."""
    detalhes = raw.get("details") or {}
    nome = str(detalhes.get("resourceName") or raw.get("resourceName") or detalhes.get("resourceId") or "?")
    if nome.replace(".", "").isdigit():  # IP fica inteiro
        return nome
    return nome.split(".", 1)[0] or nome


def classificar(alertas: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Máquinas (sem repetição, em ordem alfabética) por categoria.

    Args:
        alertas: Itens crus de ``/alerts``.

    Returns:
        Um dicionário com todas as categorias (lista vazia quando não há nada).
    """
    por_cat: dict[str, set[str]] = {c.chave: set() for c in CATEGORIAS}
    for raw in alertas:
        cat = categoria_do_alerta(raw)
        if cat is not None:
            por_cat[cat].add(_maquina(raw))
    return {k: sorted(v, key=str.lower) for k, v in por_cat.items()}


def texto(maquinas: list[str]) -> str:
    """Texto do item: "3 máquinas: A, B, C" (vazio quando não há máquina)."""
    if not maquinas:
        return ""
    n = len(maquinas)
    nomes = ", ".join(maquinas[:_MAX_NOMES])
    resto = f" e mais {n - _MAX_NOMES}" if n > _MAX_NOMES else ""
    return f"{n} máquina{'s' if n > 1 else ''}: {nomes}{resto}"[:_MAX_TEXTO]


def enviar_zabbix(por_cat: dict[str, list[str]]) -> int:
    """Manda o texto de cada categoria para o seu item trapper.

    Returns:
        Quantos itens receberam valor (0 sem Zabbix ou sem os itens criados).
    """
    from itgov.api.v1.cftv_monitoring import _zbx

    if not os.getenv("ZABBIX_URL"):
        return 0
    try:
        itens = _zbx(
            "item.get",
            {
                "output": ["itemid", "key_"],
                "host": HOST_ZABBIX,
                "filter": {"key_": [chave_item(c.chave) for c in CATEGORIAS]},
            },
        )
        por_chave = {i["key_"]: i["itemid"] for i in itens}
        valores = [
            {"itemid": por_chave[chave_item(c.chave)], "value": texto(por_cat.get(c.chave, []))}
            for c in CATEGORIAS
            if chave_item(c.chave) in por_chave
        ]
        if valores:
            _zbx("history.push", valores)
    except (requests.RequestException, RuntimeError, KeyError) as exc:
        log.warning("acronis_triggers.zabbix_falhou", erro=str(exc)[:200])
        return 0
    return len(valores)


def _gravar(dados: dict[str, Any]) -> None:
    tmp = f"{_ARQUIVO}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False)
    os.replace(tmp, _ARQUIVO)


def rodada(agora: datetime | None = None) -> dict[str, Any]:
    """Lê os alertas do Acronis, atualiza os itens do Zabbix e guarda a leitura.

    Args:
        agora: Momento da rodada (testes).

    Returns:
        Resumo da rodada (contagem por categoria, itens enviados ou erro).
    """
    agora = agora or datetime.now(UTC)
    if not acronis_cyber.configurado():
        return {"erro": "Acronis não configurado"}
    try:
        alertas = asyncio.run(acronis_cyber._buscar_alertas(_LIMITE_ALERTAS))
    except (httpx.HTTPError, KeyError, ValueError, RuntimeError) as exc:
        log.warning("acronis_triggers.acronis_falhou", erro=f"{type(exc).__name__}: {exc}"[:200])
        resumo: dict[str, Any] = {"erro": "Falha ao ler o Acronis", "em": agora.isoformat()}
        with contextlib.suppress(OSError):
            anterior = painel()
            _gravar({**anterior, "erro": resumo["erro"], "erro_em": agora.isoformat()})
        return resumo
    por_cat = classificar(alertas)
    enviados = enviar_zabbix(por_cat)
    dados = {
        "em": agora.isoformat(),
        "alertas_lidos": len(alertas),
        "zabbix_itens": enviados,
        "categorias": [
            c.model_dump()
            | {"maquinas": por_cat[c.chave], "total": len(por_cat[c.chave]), "texto": texto(por_cat[c.chave])}
            for c in CATEGORIAS
        ],
    }
    with contextlib.suppress(OSError):
        _gravar(dados)
    resumo = {c.chave: len(por_cat[c.chave]) for c in CATEGORIAS} | {"zabbix_itens": enviados}
    log.info("acronis_triggers.rodada", **resumo)
    return resumo


def painel() -> dict[str, Any]:
    """Última leitura gravada pela rodada (vazio se ainda não rodou).

    Returns:
        ``{"em", "alertas_lidos", "zabbix_itens", "categorias": [...]}`` ou ``{}``.
    """
    try:
        with open(_ARQUIVO, encoding="utf-8") as f:
            dados = json.load(f)
    except (OSError, ValueError):
        return {}
    return dados if isinstance(dados, dict) else {}


def iniciar_agendador(app: Flask) -> bool:
    """Liga o agente neste processo se for o gunicorn e ninguém mais tiver a trava.

    Returns:
        True se este processo ficou com o agente.
    """
    if app.config.get("TESTING") or "gunicorn" not in os.path.basename(sys.argv[0]):
        return False
    if not acronis_cyber.configurado():
        return False
    trava = open(_TRAVA, "w")  # noqa: SIM115 -- fica aberta enquanto o worker viver (segura a trava)
    try:
        fcntl.flock(trava, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        trava.close()
        return False

    def _laco() -> None:
        time.sleep(_PRIMEIRA_RODADA_S)
        while True:
            try:
                rodada()
            except (httpx.HTTPError, requests.RequestException, RuntimeError, OSError, ValueError) as exc:
                log.warning("acronis_triggers.rodada_falhou", erro=f"{type(exc).__name__}: {exc}"[:200])
            time.sleep(INTERVALO.total_seconds())

    app.extensions["acronis_triggers_trava"] = trava
    threading.Thread(target=_laco, name="acronis-triggers", daemon=True).start()
    log.info("acronis_triggers.agendador_ligado", pid=os.getpid())
    return True
