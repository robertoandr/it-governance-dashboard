"""Programas instalados nas máquinas, pelos avisos do monitoramento do Acronis (item 3.4).

A API pública do Acronis não traz o inventário de software completo nem os
dispositivos descobertos (o console usa endpoint interno). O que ela traz é o
alerta ``MiMonitoringSoftwareInstalled`` do plano de monitoramento, um por
instalação, com ``software_name``, ``software_version`` e a máquina. Ou seja:
o que foi **instalado** desde que o monitoramento está ligado, não tudo o que
existe na máquina.

Destaca programas de acesso remoto (TeamViewer, AnyDesk…), que dão controle da
máquina a quem está de fora.
"""

from __future__ import annotations

import asyncio
import re
from collections import Counter
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog
from pydantic import BaseModel

from itgov.services import acronis_cyber
from itgov.utils.cache_swr import CacheSWR

log = structlog.get_logger(__name__)

TIPO_ALERTA = "MiMonitoringSoftwareInstalled"
_LIMITE_ALERTAS = 5000

_ACESSO_REMOTO = re.compile(
    r"teamviewer|anydesk|rustdesk|ultraviewer|splashtop|remote ?utilities|supremo|logmein|"
    r"chrome remote desktop|\bvnc\b|realvnc|tightvnc|ultravnc|ammyy|parsec|screenconnect|connectwise|"
    r"dwservice|getscreen|zoho assist|remotepc|nomachine|meshagent|action1",
    re.I,
)


class Instalacao(BaseModel):
    """Um programa instalado numa máquina.

    Attributes:
        maquina: Nome da máquina (sem o domínio).
        programa: Nome do programa.
        versao: Versão instalada.
        quando: Quando o Acronis avisou a instalação.
    """

    maquina: str
    programa: str
    versao: str = ""
    quando: datetime | None = None

    @property
    def acesso_remoto(self) -> bool:
        """É programa de acesso remoto?"""
        return bool(_ACESSO_REMOTO.search(self.programa))


class PainelProgramas(BaseModel):
    """O que a aba Programas mostra."""

    atualizado_em: datetime
    instalacoes: list[Instalacao] = []

    @property
    def maquinas(self) -> int:
        """Máquinas com alguma instalação avisada."""
        return len({i.maquina for i in self.instalacoes})

    @property
    def desde(self) -> datetime | None:
        """Aviso mais antigo (desde quando o monitoramento está avisando)."""
        datas = [i.quando for i in self.instalacoes if i.quando]
        return min(datas) if datas else None

    @property
    def acesso_remoto(self) -> list[Instalacao]:
        """Instalações de programas de acesso remoto, da mais nova para a mais antiga."""
        return [i for i in self.instalacoes if i.acesso_remoto]

    def mais_instalados(self, n: int = 15) -> list[tuple[str, int]]:
        """Programas instalados em mais máquinas: ``(programa, máquinas)``."""
        conta = Counter(programa for programa, _ in {(i.programa, i.maquina.lower()) for i in self.instalacoes})
        return sorted(conta.items(), key=lambda x: (-x[1], x[0].lower()))[:n]


def _data(valor: Any) -> datetime | None:
    texto = str(valor or "").strip().replace("Z", "+00:00")
    if not texto:
        return None
    if "." in texto:  # o Acronis manda até 9 casas de fração
        inteiro, resto = texto.split(".", 1)
        fracao = "".join(ch for ch in resto if ch.isdigit())
        texto = f"{inteiro}.{fracao[:6]}{resto[len(fracao) :]}"
    try:
        data = datetime.fromisoformat(texto)
    except ValueError:
        return None
    return data if data.tzinfo else data.replace(tzinfo=UTC)


def montar(alertas: list[dict[str, Any]], agora: datetime) -> PainelProgramas:
    """Instalações a partir dos alertas, sem repetir (máquina, programa, versão).

    Args:
        alertas: Itens crus de ``/api/alert_manager/v1/alerts``.
        agora: Momento da leitura.

    Returns:
        O painel, com as instalações da mais nova para a mais antiga.
    """
    vistas: dict[tuple[str, str, str], Instalacao] = {}
    for a in alertas:
        if a.get("type") != TIPO_ALERTA:
            continue
        d = a.get("details") or {}
        programa = str(d.get("software_name") or "").strip()
        if not programa:
            continue
        maquina = str(d.get("resourceName") or d.get("workload_name") or "?").split(".")[0]
        versao = str(d.get("software_version") or "").strip()
        quando = _data(a.get("createdAt") or a.get("receivedAt"))
        chave = (maquina.lower(), programa.lower(), versao)
        atual = vistas.get(chave)
        if atual is None or (quando and (atual.quando is None or quando > atual.quando)):
            vistas[chave] = Instalacao(maquina=maquina, programa=programa, versao=versao, quando=quando)
    minimo = datetime.min.replace(tzinfo=UTC)
    instalacoes = sorted(vistas.values(), key=lambda i: i.quando or minimo, reverse=True)
    return PainelProgramas(atualizado_em=agora, instalacoes=instalacoes)


def _carregar() -> PainelProgramas | None:
    if not acronis_cyber.configurado():
        return None
    try:
        alertas = asyncio.run(acronis_cyber._buscar_alertas(_LIMITE_ALERTAS))
    except (httpx.HTTPError, KeyError, ValueError, RuntimeError) as exc:
        log.warning("acronis_programas.falhou", erro=type(exc).__name__, detalhe=str(exc)[:200])
        return None
    return montar(alertas, datetime.now(UTC))


_cache: CacheSWR[PainelProgramas | None] = CacheSWR(
    "acronis.programas", ttl=3600, valido=lambda v: v is not None, ttl_falha=600
)


def obter() -> PainelProgramas | None:
    """Instalações (cache de 1 h; a primeira leitura roda em segundo plano).

    Returns:
        O painel; None sem Acronis, durante a primeira leitura ou se falhou.
    """
    if not acronis_cyber.configurado():
        return None
    if not _cache.carregado:
        _cache.aquecer(_carregar)
        return None
    return _cache.get(_carregar)


def carregando() -> bool:
    """A primeira leitura ainda não terminou?"""
    return acronis_cyber.configurado() and not _cache.carregado
