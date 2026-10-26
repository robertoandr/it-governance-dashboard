"""Leitura das campanhas de phishing simulado do GoPhish para o módulo Cyber.

O GoPhish roda num container à parte: o painel admin fica preso no loopback
(acesso só por túnel SSH) e guarda os próprios dados. Aqui o dashboard só lê a
API dele (``/api/campaigns/summary``) para mostrar, na aba Cyber, quantos
clicaram e quantos reportaram cada campanha.

Config (env):

- ``GOPHISH_API_URL``: base da API, ex. ``https://127.0.0.1:3333``;
- ``GOPHISH_API_KEY``: chave da API do GoPhish;
- ``GOPHISH_VERIFY_TLS``: ``1`` para validar o certificado (padrão: não, pois
  o admin no loopback usa certificado autoassinado).

Tudo aqui é leitura: nenhuma chamada altera o GoPhish.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog
from pydantic import BaseModel

log = structlog.get_logger(__name__)

_TIMEOUT = httpx.Timeout(20.0)


class CampanhaPhish(BaseModel):
    """Resumo de uma campanha de phishing simulado.

    Attributes:
        id: Identificador da campanha no GoPhish.
        nome: Nome da campanha.
        status: Situação (``In progress``, ``Completed``...).
        criada_em: Quando a campanha foi criada (ISO do GoPhish).
        lancada_em: Quando foi lançada, quando houver.
        alvos: Total de destinatários.
        enviados: E-mails enviados.
        abriram: Quantos abriram o e-mail.
        clicaram: Quantos clicaram no link.
        enviaram_dados: Quantos enviaram dados na landing page (caíram).
        reportaram: Quantos reportaram o e-mail como suspeito.
        erros: Envios com erro.
    """

    id: int
    nome: str
    status: str
    criada_em: str
    lancada_em: str | None
    alvos: int
    enviados: int
    abriram: int
    clicaram: int
    enviaram_dados: int
    reportaram: int
    erros: int

    @property
    def taxa_clique(self) -> float:
        """Fração dos alvos que clicaram (0 a 1)."""
        return self.clicaram / self.alvos if self.alvos else 0.0

    @property
    def taxa_reporte(self) -> float:
        """Fração dos alvos que reportaram o e-mail (0 a 1)."""
        return self.reportaram / self.alvos if self.alvos else 0.0


def configurado() -> bool:
    """Diz se há URL e chave da API do GoPhish no ambiente."""
    return bool(os.getenv("GOPHISH_API_URL") and os.getenv("GOPHISH_API_KEY"))


def _cliente() -> httpx.Client:
    verificar = os.getenv("GOPHISH_VERIFY_TLS", "0") == "1"
    return httpx.Client(
        base_url=os.getenv("GOPHISH_API_URL", "").rstrip("/"),
        headers={"Authorization": os.getenv("GOPHISH_API_KEY", "")},
        timeout=_TIMEOUT,
        verify=verificar,
    )


def _campanha(dado: dict[str, Any]) -> CampanhaPhish:
    stats = dado.get("stats") or {}
    lancada = dado.get("launch_date") or ""
    return CampanhaPhish(
        id=int(dado.get("id", 0)),
        nome=str(dado.get("name", "")),
        status=str(dado.get("status", "")),
        criada_em=str(dado.get("created_date", "")),
        lancada_em=lancada or None,
        alvos=int(stats.get("total", 0)),
        enviados=int(stats.get("sent", 0)),
        abriram=int(stats.get("opened", 0)),
        clicaram=int(stats.get("clicked", 0)),
        enviaram_dados=int(stats.get("submitted_data", 0)),
        reportaram=int(stats.get("email_reported", 0)),
        erros=int(stats.get("error", 0)),
    )


def listar_campanhas() -> list[CampanhaPhish]:
    """Lista as campanhas com seus números, da mais nova para a mais antiga.

    Raises:
        httpx.HTTPError: Falha de rede ou HTTP ao falar com o GoPhish.
    """
    with _cliente() as cliente:
        resp = cliente.get("/api/campaigns/summary")
        resp.raise_for_status()
        campanhas = resp.json().get("campaigns") or []
    return sorted(
        (_campanha(c) for c in campanhas),
        key=lambda c: c.criada_em,
        reverse=True,
    )


def painel() -> dict[str, Any]:
    """Dados da aba Cyber: campanhas e um resumo geral. Nunca levanta exceção.

    Returns:
        ``{"configurado", "erro", "campanhas", "resumo"}``. Em falha, ``erro``
        traz uma mensagem curta e as listas vêm vazias.
    """
    if not configurado():
        return {"configurado": False, "erro": "", "campanhas": [], "resumo": {}}
    try:
        campanhas = listar_campanhas()
    except httpx.HTTPError as exc:
        log.warning("gophish.painel_falhou", erro=f"{type(exc).__name__}: {exc}"[:200])
        return {"configurado": True, "erro": "Não deu para falar com o GoPhish agora.", "campanhas": [], "resumo": {}}

    alvos = sum(c.alvos for c in campanhas)
    clicaram = sum(c.clicaram for c in campanhas)
    reportaram = sum(c.reportaram for c in campanhas)
    resumo = {
        "campanhas": len(campanhas),
        "alvos": alvos,
        "clicaram": clicaram,
        "reportaram": reportaram,
        "caíram": sum(c.enviaram_dados for c in campanhas),
        "taxa_clique": clicaram / alvos if alvos else 0.0,
        "taxa_reporte": reportaram / alvos if alvos else 0.0,
        "atualizado_em": datetime.now(UTC).isoformat(),
    }
    return {
        "configurado": True,
        "erro": "",
        "campanhas": [
            c.model_dump() | {"taxa_clique": c.taxa_clique, "taxa_reporte": c.taxa_reporte} for c in campanhas
        ],
        "resumo": resumo,
    }
