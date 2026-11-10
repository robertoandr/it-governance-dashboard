"""Quem é o último usuário logado de cada computador: login do Acronis → conta do M365.

O Acronis informa o último login como ``nome.sobrenome (GRUPOGADENS)`` (conta
do AD). A conta do M365 é achada pelo ``onPremisesSamAccountName`` (contas
sincronizadas do AD) ou, sem ele, pelo começo do UPN (``nome.sobrenome@...``).
Com ela, a tela mostra o nome da pessoa, o e-mail, se a conta ainda está ativa
e o link para os chamados dela no Zendesk.

Conta desativada no M365 com login recente num computador é sinal de máquina
usada por quem já saiu (ou por uma conta compartilhada): vale conferir.
"""

from __future__ import annotations

import asyncio
import os
import re
from urllib.parse import quote

import httpx
import structlog
from pydantic import BaseModel

from itgov.services.graph_client import GraphAuthError, _fetch_token, _get_credentials
from itgov.utils.cache_swr import CacheSWR

log = structlog.get_logger(__name__)

_URL_USUARIOS = (
    "https://graph.microsoft.com/v1.0/users"
    "?$select=userPrincipalName,displayName,accountEnabled,onPremisesSamAccountName,mail&$top=999"
)
# "nome.sobrenome (DOMINIO)" ou "nome@dominio (DOMINIO)"; sem parênteses, o login puro
_LOGIN = re.compile(r"^\s*(?:[^\\]+\\)?([^@\s(]+)")


class Pessoa(BaseModel):
    """Conta do M365 de quem usou o computador por último."""

    nome: str
    email: str
    ativa: bool

    @property
    def chamados_url(self) -> str:
        """Busca no Zendesk pelos chamados abertos por essa pessoa ("" sem Zendesk configurado)."""
        sub = os.getenv("ZENDESK_SUBDOMAIN", "").strip()
        if not sub or not self.email:
            return ""
        return f"https://{sub}.zendesk.com/agent/search/1?q={quote(f'requester:{self.email}')}"


class PessoasM365(BaseModel):
    """Contas do M365 indexadas pelo login do AD."""

    por_login: dict[str, Pessoa]

    def de(self, usuario_acronis: str) -> Pessoa | None:
        """Conta do M365 do último usuário informado pelo Acronis, ou None se não achar."""
        login = login_do_acronis(usuario_acronis)
        return self.por_login.get(login) if login else None


def login_do_acronis(usuario: str) -> str:
    """``"Fulano.Silva (GRUPOGADENS)"`` → ``"fulano.silva"`` ("" se vazio)."""
    achado = _LOGIN.match(usuario or "")
    return achado.group(1).lower() if achado else ""


def indexar(usuarios: list[dict[str, object]]) -> PessoasM365:
    """Monta o índice login → pessoa a partir de ``/users`` do Graph.

    O ``onPremisesSamAccountName`` vale mais que o começo do UPN: um UPN
    ``fulano@`` pode ser de outra pessoa que o ``sAMAccountName`` ``fulano``.

    Args:
        usuarios: Itens de ``/users`` (campos de ``_URL_USUARIOS``).

    Returns:
        O índice.
    """
    por_upn: dict[str, Pessoa] = {}
    por_sam: dict[str, Pessoa] = {}
    for u in usuarios:
        upn = str(u.get("userPrincipalName") or "")
        if not upn:
            continue
        pessoa = Pessoa(
            nome=str(u.get("displayName") or upn),
            email=str(u.get("mail") or upn),
            ativa=bool(u.get("accountEnabled")),
        )
        por_upn.setdefault(upn.split("@")[0].lower(), pessoa)
        sam = str(u.get("onPremisesSamAccountName") or "").lower()
        if sam:
            por_sam[sam] = pessoa
    return PessoasM365(por_login={**por_upn, **por_sam})


async def _buscar() -> PessoasM365:
    async with httpx.AsyncClient(timeout=60) as client:
        token = await _fetch_token(client)
        usuarios: list[dict[str, object]] = []
        url: str | None = _URL_USUARIOS
        while url:
            resp = await client.get(url, headers={"Authorization": f"Bearer {token}"})
            resp.raise_for_status()
            pagina = resp.json()
            usuarios += pagina.get("value", [])
            url = pagina.get("@odata.nextLink")
    log.info("m365_pessoas.lidas", contas=len(usuarios))
    return indexar(usuarios)


def _carregar() -> PessoasM365 | None:
    try:
        return asyncio.run(_buscar())
    except (httpx.HTTPError, GraphAuthError, RuntimeError, ValueError, KeyError) as exc:
        log.warning("m365_pessoas.falhou", erro=type(exc).__name__, detalhe=str(exc)[:200])
        return None


_cache: CacheSWR[PessoasM365 | None] = CacheSWR(
    "m365.pessoas", ttl=6 * 3600, valido=lambda v: v is not None, ttl_falha=600
)


def configurado() -> bool:
    """Há credencial do Graph no ambiente?"""
    return all(_get_credentials())


def obter() -> PessoasM365 | None:
    """Índice das contas (cache de 6 h). Não espera a primeira leitura: devolve None enquanto ela roda."""
    if not configurado():
        return None
    if not _cache.carregado:
        aquecer()
        return None
    return _cache.get(_carregar)


def aquecer() -> None:
    """Carrega o cache em segundo plano na subida do worker."""
    if configurado():
        _cache.aquecer(_carregar)
