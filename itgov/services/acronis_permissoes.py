"""Lista de permissões e bloqueios dos planos do Acronis, e incidentes por categoria (item 3.3).

A API pública não tem quarentena nem tática MITRE. O que ela tem:

- **Permissões e bloqueios**: nas configurações de cada plano
  (``/api/policy_management/v4/policies?include_settings=true``), as listas de
  processos/hashes confiáveis, pastas não verificadas, conexões liberadas e as
  de bloqueio. Cada lista ``X`` vem com ``XDetails`` (``Description`` +
  ``Values``), que diz de onde veio o item: "Adicionado da Quarentena 11 de set.
  de 2026" ou "Added for incident [913]".
- **Categorias**: ``incident_categories`` dos incidentes abertos do EDR
  (``acronis_edr.listar_abertos``), no lugar da tática MITRE.

Só leitura.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog
from pydantic import BaseModel

from itgov.services import acronis_cyber, acronis_edr
from itgov.utils.cache_swr import CacheSWR

log = structlog.get_logger(__name__)

_TIMEOUT = 60

# Lista nas configurações do plano → (rótulo, permitido?)
LISTAS: dict[str, tuple[str, bool]] = {
    "TrustedProcesses": ("Processo confiável", True),
    "TrustedHashes": ("Hash confiável", True),
    "BackupProtectionWhitelist": ("Processo liberado", True),
    "ProcessWhitelist": ("Processo liberado (proteção ativa)", True),
    "NotMonitoredFilesAndFolders": ("Arquivo ou pasta fora da verificação", True),
    "UnmonitoredFilelist": ("Arquivo fora da proteção ativa", True),
    "ConnectionWhitelist": ("Conexão liberada", True),
    "BlockedProcesses": ("Processo bloqueado", False),
    "BlockedHashes": ("Hash bloqueado", False),
    "BlockedFilesAndFolders": ("Arquivo ou pasta bloqueado", False),
    "ProcessBlacklist": ("Processo bloqueado (proteção ativa)", False),
    "ConnectionBlacklist": ("Conexão bloqueada", False),
}

CATEGORIAS: dict[str, str] = {
    "URL_BLOCKED": "URL bloqueada",
    "WEBSITE_URL_BLOCKED": "Site bloqueado",
    "POTENTIALLY_UNWANTED_APPLICATION": "Programa indesejado (PUA)",
    "PROCESS_DETECTED": "Processo suspeito",
    "MALWARE_DETECTED": "Malware",
    "RANSOMWARE_DETECTED": "Ransomware",
    "EXPLOIT_DETECTED": "Exploração de falha",
    "CRYPTOMINING_DETECTED": "Mineração de criptomoeda",
}

# Binários do sistema e interpretadores que atacantes usam ("living off the land"):
# confiar neles libera qualquer script que rodem.
_BINARIOS_ARRISCADOS = frozenset(
    {
        "cmd.exe", "powershell.exe", "pwsh.exe", "wscript.exe", "cscript.exe", "mshta.exe",
        "rundll32.exe", "regsvr32.exe", "certutil.exe", "bitsadmin.exe", "wmic.exe",
        "python.exe", "pythonw.exe", "node.exe", "java.exe", "javaw.exe", "msiexec.exe",
    }
)  # fmt: skip
_PASTA_USUARIO = re.compile(
    r"\\users\\[^\\]+(\\(downloads|desktop|documents|appdata(\\(local|roaming))?(\\temp)?))?\\?$", re.I
)
_INCIDENTE = re.compile(r"incident\s*\[(\d+)\]", re.I)
_QUARENTENA = re.compile(r"quarentena|quarantine", re.I)


class ItemLista(BaseModel):
    """Um item de lista de permissão ou bloqueio.

    Attributes:
        plano: Nome do plano de proteção.
        lista: Rótulo da lista (ex.: "Processo confiável").
        permitido: ``True`` para permissão, ``False`` para bloqueio.
        valor: Caminho, hash ou endereço.
        origem: ``quarentena``, ``incidente`` ou ``manual``.
        descricao: Texto do Acronis (ex.: "Added for incident [913]").
        incidente: Número curto do incidente, quando veio de um.
    """

    plano: str
    lista: str
    permitido: bool
    valor: str
    origem: str = "manual"
    descricao: str = ""
    incidente: int | None = None

    @property
    def risco(self) -> str:
        """Por que a permissão é arriscada (vazio se não é ou se é bloqueio)."""
        if not self.permitido:
            return ""
        nome = self.valor.replace("/", "\\").rstrip("\\").rsplit("\\", 1)[-1].lower()
        if nome in _BINARIOS_ARRISCADOS:
            return "binário do sistema ou interpretador: libera qualquer script que ele rodar"
        if _PASTA_USUARIO.search(self.valor.replace("/", "\\")):
            return "pasta inteira de usuário fora da proteção"
        return ""


class PainelPermissoes(BaseModel):
    """O que a aba Permissões e o bloco de categorias mostram."""

    atualizado_em: datetime
    itens: list[ItemLista] = []
    planos: list[str] = []
    categorias: list[tuple[str, str, int]] = []  # (chave, rótulo, incidentes)
    incidentes_abertos: int = 0
    maquinas_com_incidente: int = 0
    erro_incidentes: bool = False

    @property
    def permitidos(self) -> list[ItemLista]:
        """Itens de permissão."""
        return [i for i in self.itens if i.permitido]

    @property
    def arriscados(self) -> list[ItemLista]:
        """Permissões arriscadas (ver ``ItemLista.risco``)."""
        return [i for i in self.itens if i.risco]

    @property
    def bloqueados(self) -> list[ItemLista]:
        """Itens de bloqueio."""
        return [i for i in self.itens if not i.permitido]


def configurado() -> bool:
    """Há base e credenciais do Acronis?"""
    return acronis_cyber.configurado()


def origem_da_descricao(descricao: str) -> tuple[str, int | None]:
    """De onde veio o item: ``("incidente", 913)``, ``("quarentena", None)`` ou ``("manual", None)``."""
    if m := _INCIDENTE.search(descricao or ""):
        return "incidente", int(m.group(1))
    if _QUARENTENA.search(descricao or ""):
        return "quarentena", None
    return "manual", None


def itens_do_plano(plano: str, settings: dict[str, Any]) -> list[ItemLista]:
    """Itens das listas conhecidas nas configurações de um módulo do plano.

    Usa ``<Lista>Details`` quando existe (traz a descrição); senão, a lista
    simples. Os hashes só vêm em ``TrustedHashesDetails``/``BlockedHashesDetails``.

    Args:
        plano: Nome do plano.
        settings: ``settings`` do módulo (as listas podem estar em ``Exclusions``).

    Returns:
        Itens sem repetição dentro da mesma lista.
    """
    fontes = [settings, settings.get("Exclusions") or {}]
    vistos: set[tuple[str, str]] = set()
    saida: list[ItemLista] = []
    for fonte in fontes:
        if not isinstance(fonte, dict):
            continue
        for chave, (rotulo, permitido) in LISTAS.items():
            detalhes = fonte.get(f"{chave}Details")
            pares: list[tuple[str, str]] = []
            if isinstance(detalhes, list):
                for d in detalhes:
                    if isinstance(d, dict):
                        pares += [(str(v), str(d.get("Description") or "")) for v in d.get("Values") or []]
            simples = fonte.get(chave)
            if isinstance(simples, list):
                pares += [(str(v), "") for v in simples if isinstance(v, str)]
            for valor, descricao in pares:
                if not valor.strip() or (rotulo, valor) in vistos:
                    continue
                vistos.add((rotulo, valor))
                origem, incidente = origem_da_descricao(descricao)
                saida.append(
                    ItemLista(
                        plano=plano, lista=rotulo, permitido=permitido, valor=valor,
                        origem=origem, descricao=descricao, incidente=incidente,
                    )
                )  # fmt: skip
    return saida


def itens_das_politicas(politicas: list[dict[str, Any]]) -> tuple[list[ItemLista], list[str]]:
    """Itens de todos os planos e os nomes dos planos.

    Cada item de ``/policies`` é um plano: uma lista ``policy`` com o módulo
    ``policy.protection.total`` (que dá o nome) e os módulos de segurança.
    """
    itens: list[ItemLista] = []
    planos: list[str] = []
    for p in politicas:
        modulos = [m for m in p.get("policy") or [] if isinstance(m, dict)]
        total = next((m for m in modulos if m.get("type") == "policy.protection.total"), {})
        nome = str(total.get("name") or "")
        do_plano = [
            i
            for m in modulos
            if str(m.get("type") or "").startswith("policy.security.")
            for i in itens_do_plano(nome or "Plano sem nome", m.get("settings") or {})
        ]
        if nome or do_plano:
            planos.append(nome or "Plano sem nome")
        itens += do_plano
    return itens, sorted(set(planos), key=str.lower)


def contar_categorias(incidentes: list[acronis_edr.IncidenteEdr]) -> list[tuple[str, str, int]]:
    """Incidentes por categoria (um incidente com duas categorias conta nas duas)."""
    conta = Counter(c for i in incidentes for c in (i.categorias or ["SEM_CATEGORIA"]))
    rotulos = {**CATEGORIAS, "SEM_CATEGORIA": "Sem categoria"}
    return [(c, rotulos.get(c, c.replace("_", " ").capitalize()), n) for c, n in conta.most_common()]


def _politicas() -> list[dict[str, Any]]:
    base = os.getenv("ACRONIS_BASE_URL", "").rstrip("/")
    client_id, secret = acronis_cyber._creds()
    with httpx.Client(timeout=_TIMEOUT) as c:
        token = c.post(f"{base}/api/2/idp/token", auth=(client_id, secret), data={"grant_type": "client_credentials"})
        token.raise_for_status()
        c.headers["Authorization"] = f"Bearer {token.json()['access_token']}"
        resp = c.get(f"{base}/api/policy_management/v4/policies", params={"limit": 500, "include_settings": "true"})
        resp.raise_for_status()
        return json.loads(resp.content.decode("utf-8", "replace")).get("items", [])


def _carregar() -> PainelPermissoes | None:
    try:
        itens, planos = itens_das_politicas(_politicas())
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        log.warning("acronis_permissoes.falhou", erro=type(exc).__name__, detalhe=str(exc)[:200])
        return None
    painel = PainelPermissoes(atualizado_em=datetime.now(UTC), itens=itens, planos=planos)
    try:
        abertos = acronis_edr.listar_abertos()
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        log.warning("acronis_permissoes.incidentes_falhou", erro=type(exc).__name__, detalhe=str(exc)[:200])
        painel.erro_incidentes = True
        return painel
    painel.categorias = contar_categorias(abertos)
    painel.incidentes_abertos = len(abertos)
    painel.maquinas_com_incidente = len({i.maquina for i in abertos if i.maquina})
    return painel


_cache: CacheSWR[PainelPermissoes | None] = CacheSWR(
    "acronis.permissoes", ttl=3600, valido=lambda v: v is not None, ttl_falha=600
)


def obter() -> PainelPermissoes | None:
    """Permissões e categorias (cache de 1 h; a primeira leitura roda em segundo plano).

    Returns:
        O painel; None sem Acronis, durante a primeira leitura ou se falhou.
    """
    if not configurado():
        return None
    if not _cache.carregado:
        _cache.aquecer(_carregar)
        return None
    return _cache.get(_carregar)


def carregando() -> bool:
    """A primeira leitura ainda não terminou?"""
    return configurado() and not _cache.carregado
