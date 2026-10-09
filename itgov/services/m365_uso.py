"""Uso dos apps e serviços do M365 (Conferência V2.0, item a1).

Três relatórios do Graph (permissão ``Reports.Read.All``), todos em CSV:

- ``getOffice365ServicesUserCounts(D30)``: contas ativas e inativas por serviço
  nos últimos 30 dias — base do "% de uso".
- ``getM365AppUserDetail(D30)``: uma linha por conta dizendo se usou Outlook,
  Word, Excel, PowerPoint, OneNote, Teams e em que plataforma. Os relatórios
  agregados de apps vêm vazios neste tenant, então a contagem sai daqui.
- ``getOffice365ActiveUserCounts(D180)``: ativos por dia e serviço nos últimos
  180 dias — o histórico, resumido mês a mês.

Os nomes das contas vêm mascarados (opção de privacidade do tenant no centro
de administração); para os percentuais isso não importa.
"""

from __future__ import annotations

import asyncio
import csv
import io
from collections import defaultdict
from datetime import date

import httpx
import structlog
from pydantic import BaseModel

from itgov.services.graph_client import GraphAuthError, _fetch_token
from itgov.utils.cache_swr import CacheSWR

log = structlog.get_logger(__name__)

_REPORTS = "https://graph.microsoft.com/v1.0/reports"
_URL_SERVICOS = f"{_REPORTS}/getOffice365ServicesUserCounts(period='D30')"
_URL_APPS = f"{_REPORTS}/getM365AppUserDetail(period='D30')"
_URL_HISTORICO = f"{_REPORTS}/getOffice365ActiveUserCounts(period='D180')"
# Contas que usaram cada app, por dia (o CSV do relatório acima às vezes não baixa:
# o servidor de download da Microsoft derruba a conexão; este continua saindo)
_URL_HISTORICO_APPS = f"{_REPORTS}/getM365AppUserCounts(period='D180')"

# Coluna do relatório → nome na tela. Skype for Business foi descontinuado.
SERVICOS = {
    "Exchange": "E-mail (Exchange)",
    "OneDrive": "OneDrive",
    "SharePoint": "SharePoint",
    "Teams": "Teams",
    "Yammer": "Viva Engage (Yammer)",
}
APPS = ["Outlook", "Word", "Excel", "PowerPoint", "OneNote", "Teams"]
PLATAFORMAS = {"Windows": "Windows", "Mac": "Mac", "Mobile": "Celular", "Web": "Navegador"}
_MIN_DIAS_MES_ATUAL = 3
_MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]


class UsoItem(BaseModel):
    """Quantas contas usaram um serviço, app ou plataforma no período."""

    nome: str
    usuarios: int
    total: int

    @property
    def pct(self) -> float:
        """Percentual de contas que usaram (0 quando não há contas)."""
        return round(self.usuarios / self.total * 100, 1) if self.total else 0.0


class MesUso(BaseModel):
    """Média de contas ativas por dia útil em um mês, por serviço."""

    rotulo: str
    dias: int
    parcial: bool
    medias: dict[str, float]


class UsoM365(BaseModel):
    """Resumo do uso do M365 para a tela."""

    atualizado_em: str
    contas: int
    ativas: int
    servicos: list[UsoItem]
    apps: list[UsoItem]
    plataformas: list[UsoItem]
    contas_apps: int
    meses: list[MesUso]
    colunas_historico: list[str]
    meses_apps: list[MesUso] = []
    colunas_historico_apps: list[str] = []

    @property
    def pct_ativas(self) -> float:
        """Percentual de contas que usaram qualquer serviço em 30 dias."""
        return round(self.ativas / self.contas * 100, 1) if self.contas else 0.0


def ler_csv(texto: str) -> list[dict[str, str]]:
    """Linhas do CSV dos relatórios do Graph (que vem com BOM)."""
    return list(csv.DictReader(io.StringIO(texto.lstrip("﻿"))))


def _int(valor: str | None) -> int:
    try:
        return int(valor or 0)
    except ValueError:
        return 0


def resumir_servicos(linhas: list[dict[str, str]]) -> tuple[list[UsoItem], int, int, str]:
    """Ativos/inativos por serviço em 30 dias.

    Returns:
        Itens por serviço, total de contas, contas ativas em algum serviço e a
        data de atualização do relatório.
    """
    if not linhas:
        return [], 0, 0, ""
    linha = linhas[0]
    itens = []
    for coluna, nome in SERVICOS.items():
        ativos, inativos = _int(linha.get(f"{coluna} Active")), _int(linha.get(f"{coluna} Inactive"))
        if ativos + inativos:
            itens.append(UsoItem(nome=nome, usuarios=ativos, total=ativos + inativos))
    ativas = _int(linha.get("Office 365 Active"))
    contas = ativas + _int(linha.get("Office 365 Inactive"))
    return itens, contas, ativas, linha.get("Report Refresh Date", "")


def resumir_apps(linhas: list[dict[str, str]]) -> tuple[list[UsoItem], list[UsoItem]]:
    """Contas que usaram cada app e cada plataforma, a partir do detalhe por conta."""
    total = len(linhas)

    def contar(coluna: str) -> int:
        return sum(1 for linha in linhas if linha.get(coluna) == "Yes")

    apps = [UsoItem(nome=a, usuarios=contar(a), total=total) for a in APPS]
    plataformas = [UsoItem(nome=nome, usuarios=contar(col), total=total) for col, nome in PLATAFORMAS.items()]
    apps.sort(key=lambda i: i.usuarios, reverse=True)
    plataformas.sort(key=lambda i: i.usuarios, reverse=True)
    return apps, plataformas


def resumir_historico(linhas: list[dict[str, str]], colunas: list[str] | None = None) -> list[MesUso]:
    """Média de contas ativas por dia útil, mês a mês, do mais antigo ao mais novo.

    Fim de semana fica de fora: com ele a média cai pela metade e esconde a
    tendência. O primeiro e o último mês costumam vir incompletos (o relatório
    cobre 180 dias corridos), e ficam marcados como parciais; o último sai
    se tiver menos de três dias úteis.

    Args:
        linhas: Linhas do CSV com ``Report Date``.
        colunas: Colunas a resumir; padrão: "Office 365" e os serviços.
    """
    colunas = colunas or ["Office 365", *SERVICOS]
    por_mes: dict[tuple[int, int], list[dict[str, str]]] = defaultdict(list)
    for linha in linhas:
        try:
            dia = date.fromisoformat(linha.get("Report Date", ""))
        except ValueError:
            continue
        if dia.weekday() < 5:
            por_mes[(dia.year, dia.month)].append(linha)

    chaves = sorted(por_mes)
    # O relatório chega com 1–2 dias de atraso e às vezes sem algumas colunas:
    # um mês que acabou de virar só teria um ou dois dias, e confunde mais que ajuda.
    if chaves and len(por_mes[chaves[-1]]) < _MIN_DIAS_MES_ATUAL:
        chaves.pop()
    meses = []
    for i, (ano, mes) in enumerate(chaves):
        dias = por_mes[(ano, mes)]
        medias = {}
        for col in colunas:
            valores = [_int(d.get(col)) for d in dias if d.get(col)]
            if valores:
                medias[col] = round(sum(valores) / len(dias), 1)
        meses.append(
            MesUso(
                rotulo=f"{_MESES[mes - 1]}/{ano}",
                dias=len(dias),
                parcial=i in (0, len(chaves) - 1),
                medias=medias,
            )
        )
    return meses


_TENTATIVAS = 3
_ESPERA_MAX = 20.0


async def _baixar(client: httpx.AsyncClient, token: str, url: str) -> list[dict[str, str]]:
    # O Graph responde 302 para o arquivo CSV em outro host; 429 = limite de
    # consultas aos relatórios do tenant: espera o Retry-After e tenta de novo
    for tentativa in range(_TENTATIVAS):
        resp = await client.get(url, headers={"Authorization": f"Bearer {token}"})
        if resp.status_code != 429 or tentativa == _TENTATIVAS - 1:
            break
        try:
            espera = min(float(resp.headers.get("Retry-After", "5")), _ESPERA_MAX)
        except ValueError:
            espera = 5.0
        log.info("m365_uso.limite_graph", relatorio=url.rsplit("/", 1)[-1], espera=espera)
        await asyncio.sleep(espera)
    resp.raise_for_status()
    return ler_csv(resp.text)


async def _historico(client: httpx.AsyncClient, token: str, url: str) -> list[dict[str, str]]:
    """Baixa um relatório de histórico; falha vira lista vazia (não derruba o uso atual)."""
    try:
        return await _baixar(client, token, url)
    except httpx.HTTPError as exc:
        status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
        log.warning(
            "m365_uso.historico_falhou", relatorio=url.rsplit("/", 1)[-1], erro=type(exc).__name__, status=status
        )
        return []


async def _buscar() -> UsoM365:
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        token = await _fetch_token(client)
        # Um de cada vez: em paralelo o Graph devolve 429 para os relatórios do tenant
        servicos = await _baixar(client, token, _URL_SERVICOS)
        apps = await _baixar(client, token, _URL_APPS)
        historico_apps = await _historico(client, token, _URL_HISTORICO_APPS)
        historico = await _historico(client, token, _URL_HISTORICO)
    itens, contas, ativas, atualizado = resumir_servicos(servicos)
    lista_apps, plataformas = resumir_apps(apps)
    meses = resumir_historico(historico)
    usadas = [c for c in ["Office 365", *SERVICOS] if any(c in m.medias for m in meses)]
    meses_apps = resumir_historico(historico_apps, APPS)
    usadas_apps = [c for c in APPS if any(c in m.medias for m in meses_apps)]
    return UsoM365(
        atualizado_em=atualizado,
        contas=contas,
        ativas=ativas,
        servicos=itens,
        apps=lista_apps,
        plataformas=plataformas,
        contas_apps=len(apps),
        meses=meses,
        colunas_historico=usadas,
        meses_apps=meses_apps,
        colunas_historico_apps=usadas_apps,
    )


def _carregar() -> UsoM365 | None:
    try:
        return asyncio.run(_buscar())
    except (httpx.HTTPError, GraphAuthError, RuntimeError, ValueError, KeyError) as exc:
        log.warning("m365_uso.falhou", erro=type(exc).__name__, detalhe=str(exc)[:200])
        return None


# Os relatórios do Graph só mudam uma vez por dia
_cache: CacheSWR[UsoM365 | None] = CacheSWR("m365.uso", ttl=6 * 3600, valido=lambda v: v is not None)


def obter_uso() -> UsoM365 | None:
    """Uso do M365 (cache de 6 h; ``None`` se o Graph estiver fora)."""
    return _cache.get(_carregar)


def aquecer() -> None:
    """Carrega o cache em segundo plano na subida, para a tela não esperar o Graph."""
    _cache.aquecer(_carregar)
