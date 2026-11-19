"""Último usuário logado de uma máquina, para mostrar antes de isolar.

Cruza o nome (ou o IP) da máquina com o inventário do Acronis
(``last_logged_in_user``) e, quando dá, com a conta do M365 (nome e e-mail).
Serve para quem aprova um isolamento ver de quem é o computador (diretor,
servidor de produção...) antes de tirar a máquina da rede.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from pydantic import BaseModel

from itgov.services import acronis_inventario, m365_pessoas
from itgov.services.acronis_inventario import Computador
from itgov.services.m365_pessoas import PessoasM365


class UsuarioMaquina(BaseModel):
    """Quem usou a máquina por último."""

    login: str
    nome: str = ""
    email: str = ""
    ativa: bool | None = None
    em: str | None = None

    @property
    def rotulo(self) -> str:
        """Nome da pessoa (ou o login, sem conta no M365) para texto corrido."""
        return self.nome or self.login


def nome_curto(maquina: str) -> str:
    """``"DESKTOP-3H767H5.grupogadens.com.br"`` → ``"desktop-3h767h5"``."""
    return (maquina or "").strip().split(".")[0].lower()


def _parece_ip(texto: str) -> bool:
    partes = texto.split(".")
    return len(partes) == 4 and all(p.isdigit() for p in partes)


def montar_busca(
    computadores: Iterable[Computador], pessoas: PessoasM365 | None
) -> Callable[[str, str], UsuarioMaquina | None]:
    """Função que acha o último usuário pelo nome ou IP da máquina.

    Args:
        computadores: Computadores do inventário do Acronis.
        pessoas: Contas do M365 (None se o M365 não estiver disponível).

    Returns:
        ``buscar(maquina, ip)`` → ``UsuarioMaquina`` ou None.
    """
    lista = list(computadores)
    por_nome = {nome_curto(c.nome): c for c in lista if c.nome}
    _, por_ip = acronis_inventario.indices(lista)

    def buscar(maquina: str, ip: str = "") -> UsuarioMaquina | None:
        alvo = (maquina or "").strip()
        c = None
        if alvo and not _parece_ip(alvo):
            c = por_nome.get(nome_curto(alvo))
        if c is None:
            c = por_ip.get((ip or "").strip()) or (por_ip.get(alvo) if _parece_ip(alvo) else None)
        if c is None or not c.usuario:
            return None
        pessoa = pessoas.de(c.usuario) if pessoas else None
        return UsuarioMaquina(
            login=c.usuario,
            nome=pessoa.nome if pessoa else "",
            email=pessoa.email if pessoa else "",
            ativa=pessoa.ativa if pessoa else None,
            em=c.usuario_em.isoformat() if c.usuario_em else None,
        )

    return buscar


def busca_atual() -> Callable[[str, str], UsuarioMaquina | None]:
    """``montar_busca`` com o inventário e o M365 do cache (sem esperar coleta)."""
    inventario = acronis_inventario.obter_inventario()
    return montar_busca(inventario.computadores if inventario else [], m365_pessoas.obter())


def anotar(
    itens: list[dict[str, Any]],
    buscar: Callable[[str, str], UsuarioMaquina | None],
    chave_maquina: str = "maquina",
    chave_ip: str = "ip",
) -> list[dict[str, Any]]:
    """Acrescenta ``"usuario"`` (dict ou None) em cada item, pelo nome/IP da máquina.

    Args:
        itens: Linhas para a tela (propostas, isolamentos...).
        buscar: Resultado de ``montar_busca``/``busca_atual``.
        chave_maquina: Campo com o nome da máquina.
        chave_ip: Campo com o IP (pode não existir).

    Returns:
        A mesma lista, com ``usuario`` em cada item.
    """
    for item in itens:
        u = buscar(str(item.get(chave_maquina) or ""), str(item.get(chave_ip) or ""))
        item["usuario"] = u.model_dump() if u else None
    return itens
