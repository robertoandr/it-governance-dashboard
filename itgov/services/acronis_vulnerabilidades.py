"""Vulnerabilidades e patches pendentes por máquina, das varreduras do Acronis (item 3.2).

A API pública do Acronis não tem seção de vulnerabilidades nem de patches (o
console usa um endpoint interno). O que existe é a API de tarefas: cada
varredura do agente vira uma atividade em ``/api/task_manager/v2/activities``,
e as de tipo ``WinScan`` (Windows) e ``TpScan`` (programas de terceiros)
trazem no resultado::

    {"va": [{"Firefox": 576}], "vCountTotal": 576,
     "patches": ["2026-10 Atualização de Segurança (KB5127648)", ...], "pCountTotal": 5}

Ou seja: por programa, quantas vulnerabilidades (sem o número de cada CVE) e a
lista dos patches pendentes. O ``executor.id`` da atividade é o id do agente,
que dá o nome da máquina em ``/api/agent_manager/v2/agents``.

Só leitura: aprovar ou instalar patch continua no console (não há API pública).
Para cada máquina vale a varredura mais recente de cada tipo.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog
from pydantic import BaseModel

from itgov.utils.cache_swr import CacheSWR

log = structlog.get_logger(__name__)

TIPOS_VARREDURA: dict[str, str] = {"WinScan": "Windows", "TpScan": "Programas"}
_TIMEOUT = 90
_LIMITE_ATIVIDADES = 5000


class MaquinaVulneravel(BaseModel):
    """Resultado da última varredura de uma máquina.

    Attributes:
        nome: Nome da máquina (sem o domínio).
        varredura_em: Conclusão da varredura mais recente (qualquer tipo).
        tipos: Tipos de varredura que a máquina tem (``Windows``, ``Programas``).
        programas: Programa → quantidade de vulnerabilidades.
        patches: Patches pendentes, sem repetição.
    """

    nome: str
    varredura_em: datetime | None = None
    tipos: list[str] = []
    programas: dict[str, int] = {}
    patches: list[str] = []

    @property
    def total_vulnerabilidades(self) -> int:
        """Soma das vulnerabilidades de todos os programas."""
        return sum(self.programas.values())


class PainelVulnerabilidades(BaseModel):
    """O que a aba Vulnerabilidades mostra."""

    atualizado_em: datetime
    maquinas: list[MaquinaVulneravel]
    agentes: int = 0

    @property
    def total_vulnerabilidades(self) -> int:
        """Vulnerabilidades somadas em todas as máquinas."""
        return sum(m.total_vulnerabilidades for m in self.maquinas)

    @property
    def total_patches(self) -> int:
        """Patches pendentes somados (o mesmo patch em duas máquinas conta duas vezes)."""
        return sum(len(m.patches) for m in self.maquinas)

    @property
    def mais_antiga(self) -> datetime | None:
        """Varredura mais antiga entre as máquinas (mostra o quanto os dados estão parados)."""
        datas = [m.varredura_em for m in self.maquinas if m.varredura_em]
        return min(datas) if datas else None

    def top_programas(self, n: int = 10) -> list[tuple[str, int, int]]:
        """Programas com mais vulnerabilidades: ``(programa, vulnerabilidades, máquinas)``."""
        soma: dict[str, list[int]] = {}
        for m in self.maquinas:
            for prog, qtd in m.programas.items():
                atual = soma.setdefault(prog, [0, 0])
                atual[0] += qtd
                atual[1] += 1
        return sorted(((p, v[0], v[1]) for p, v in soma.items()), key=lambda x: (-x[1], x[0]))[:n]

    def top_patches(self, n: int = 10) -> list[tuple[str, int]]:
        """Patches pendentes em mais máquinas: ``(patch, máquinas)``."""
        conta: dict[str, int] = {}
        for m in self.maquinas:
            for p in m.patches:
                conta[p] = conta.get(p, 0) + 1
        return sorted(conta.items(), key=lambda x: (-x[1], x[0]))[:n]


def _creds() -> tuple[str, str]:
    client_id = os.getenv("ACRONIS_CYBER_CLIENT_ID") or os.getenv("ACRONIS_CLIENT_ID") or ""
    secret = os.getenv("ACRONIS_CYBER_CLIENT_SECRET") or os.getenv("ACRONIS_CLIENT_SECRET") or ""
    return client_id, secret


def configurado() -> bool:
    """Há base e credenciais do Acronis?"""
    client_id, secret = _creds()
    return bool(os.getenv("ACRONIS_BASE_URL") and client_id and secret)


def _data(valor: Any) -> datetime | None:
    """ISO do Acronis (com 7 casas de fração) → datetime UTC."""
    texto = str(valor or "").strip()
    if not texto:
        return None
    texto = texto.replace("Z", "+00:00")
    if "." in texto:
        inteiro, resto = texto.split(".", 1)
        fracao = "".join(ch for ch in resto if ch.isdigit())
        fuso = resto[len(fracao) :]
        texto = f"{inteiro}.{fracao[:6]}{fuso}"
    try:
        data = datetime.fromisoformat(texto)
    except ValueError:
        return None
    return data if data.tzinfo else data.replace(tzinfo=UTC)


def programas_da_varredura(va: Any) -> dict[str, int]:
    """``[{"Firefox": 576}, {"TeamViewer": 1}]`` → ``{"Firefox": 576, "TeamViewer": 1}``."""
    saida: dict[str, int] = {}
    itens = va if isinstance(va, list) else [va] if isinstance(va, dict) else []
    for item in itens:
        if not isinstance(item, dict):
            continue
        for prog, qtd in item.items():
            try:
                saida[str(prog)] = saida.get(str(prog), 0) + int(qtd)
            except (TypeError, ValueError):
                continue
    return saida


def montar_painel(atividades: list[dict[str, Any]], nomes: dict[str, str], agora: datetime) -> PainelVulnerabilidades:
    """Junta as varreduras: por máquina, a mais recente de cada tipo.

    Args:
        atividades: Atividades ``WinScan``/``TpScan`` cruas do task manager.
        nomes: Id do agente → nome da máquina.
        agora: Momento da montagem.

    Returns:
        O painel, com as máquinas da que tem mais vulnerabilidades para a que tem menos.
    """
    ultimas: dict[tuple[str, str], tuple[datetime, dict[str, Any]]] = {}
    for a in atividades:
        tipo = str(a.get("type") or "")
        resultado = a.get("result") or {}
        agente = str((a.get("executor") or {}).get("id") or "")
        if tipo not in TIPOS_VARREDURA or resultado.get("code") != "ok" or not agente:
            continue
        quando = _data(a.get("completedAt")) or _data(a.get("updatedAt"))
        if quando is None:
            continue
        chave = (agente, tipo)
        if chave not in ultimas or quando > ultimas[chave][0]:
            ultimas[chave] = (quando, resultado.get("payload") or {})

    por_agente: dict[str, MaquinaVulneravel] = {}
    for (agente, tipo), (quando, payload) in sorted(ultimas.items()):
        nome = (nomes.get(agente) or agente[:8]).split(".")[0]
        m = por_agente.setdefault(agente, MaquinaVulneravel(nome=nome))
        m.tipos = sorted({*m.tipos, TIPOS_VARREDURA[tipo]})
        m.varredura_em = max(filter(None, [m.varredura_em, quando]))
        for prog, qtd in programas_da_varredura(payload.get("va")).items():
            m.programas[prog] = m.programas.get(prog, 0) + qtd
        m.patches = list(dict.fromkeys([*m.patches, *(str(p) for p in payload.get("patches") or [] if p)]))
    maquinas = sorted(por_agente.values(), key=lambda m: (-m.total_vulnerabilidades, -len(m.patches), m.nome.lower()))
    return PainelVulnerabilidades(atualizado_em=agora, maquinas=maquinas, agentes=len(nomes))


def _json(resp: httpx.Response) -> Any:
    """JSON tolerante: a API às vezes manda texto em latin-1 no meio do UTF-8."""
    resp.raise_for_status()
    return json.loads(resp.content.decode("utf-8", "replace"))


async def _buscar() -> PainelVulnerabilidades:
    base = os.getenv("ACRONIS_BASE_URL", "").rstrip("/")
    client_id, secret = _creds()
    async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
        token = _json(
            await c.post(f"{base}/api/2/idp/token", auth=(client_id, secret), data={"grant_type": "client_credentials"})
        )["access_token"]
        c.headers["Authorization"] = f"Bearer {token}"
        atividades: list[dict[str, Any]] = []
        for tipo in TIPOS_VARREDURA:
            params: dict[str, Any] = {"type": tipo, "limit": 1000}
            while True:
                dados = _json(await c.get(f"{base}/api/task_manager/v2/activities", params=params))
                atividades.extend(dados.get("items", []))
                depois = (dados.get("paging") or {}).get("cursors", {}).get("after")
                if not depois or not dados.get("items") or len(atividades) >= _LIMITE_ATIVIDADES:
                    break
                params["after"] = depois
        agentes = _json(await c.get(f"{base}/api/agent_manager/v2/agents", params={"limit": 1000})).get("items", [])
    nomes = {str(a.get("id")): str(a.get("hostname") or a.get("name") or "") for a in agentes if a.get("id")}
    return montar_painel(atividades, nomes, datetime.now(UTC))


async def buscar_atividades_desde(desde: datetime) -> list[dict[str, Any]]:
    """Atividades do Acronis concluídas depois de ``desde`` (todas, não só varreduras).

    É o que o encaminhamento por syslog entregaria como "atividades e tarefas":
    quem editou ou revogou plano, removeu agente, abriu sessão remota, e os
    patches e atualizações que falharam. Só leitura.

    Args:
        desde: Começo da janela (UTC).

    Returns:
        Itens crus de ``/api/task_manager/v2/activities``, no máximo ``_LIMITE_ATIVIDADES``,
        cada um com ``executorHostname`` (nome da máquina do agente, "" se não houver).

    Raises:
        httpx.HTTPError: Falha de rede ou HTTP.
    """
    base = os.getenv("ACRONIS_BASE_URL", "").rstrip("/")
    client_id, secret = _creds()
    atividades: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
        token = _json(
            await c.post(f"{base}/api/2/idp/token", auth=(client_id, secret), data={"grant_type": "client_credentials"})
        )["access_token"]
        c.headers["Authorization"] = f"Bearer {token}"
        params: dict[str, Any] = {
            "completedAt": f"gt({desde.astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')})",
            "limit": 1000,
        }
        while True:
            dados = _json(await c.get(f"{base}/api/task_manager/v2/activities", params=params))
            atividades.extend(dados.get("items", []))
            depois = (dados.get("paging") or {}).get("cursors", {}).get("after")
            if not depois or not dados.get("items") or len(atividades) >= _LIMITE_ATIVIDADES:
                break
            params["after"] = depois
        if atividades:  # o nome da máquina nem sempre vem na atividade: sai do agente que a executou
            agentes = _json(await c.get(f"{base}/api/agent_manager/v2/agents", params={"limit": 1000})).get("items", [])
            nomes = {str(g.get("id")): str(g.get("hostname") or g.get("name") or "") for g in agentes if g.get("id")}
            for a in atividades:
                a["executorHostname"] = nomes.get(str((a.get("executor") or {}).get("id") or ""), "")
    return atividades


def _carregar() -> PainelVulnerabilidades | None:
    try:
        return asyncio.run(_buscar())
    except (httpx.HTTPError, KeyError, ValueError, RuntimeError) as exc:
        log.warning("acronis_vulnerabilidades.falhou", erro=type(exc).__name__, detalhe=str(exc)[:200])
        return None


_cache: CacheSWR[PainelVulnerabilidades | None] = CacheSWR(
    "acronis.vulnerabilidades", ttl=3600, valido=lambda v: v is not None, ttl_falha=600
)


def obter_painel() -> PainelVulnerabilidades | None:
    """Painel das varreduras (cache de 1 h).

    Não espera a primeira coleta: enquanto ela roda em segundo plano, devolve
    None e ``carregando()`` fica True.

    Returns:
        O painel; None sem Acronis, durante a primeira coleta ou se falhou.
    """
    if not configurado():
        return None
    if not _cache.carregado:
        _cache.aquecer(_carregar)
        return None
    return _cache.get(_carregar)


def carregando() -> bool:
    """A primeira coleta ainda não terminou?"""
    return configurado() and not _cache.carregado
