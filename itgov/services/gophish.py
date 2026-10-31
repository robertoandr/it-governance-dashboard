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

import email.utils
import os
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog
from pydantic import BaseModel

from itgov.services import phishing_material

log = structlog.get_logger(__name__)

_TIMEOUT = httpx.Timeout(20.0)


class PhishingError(ValueError):
    """Dado inválido para montar ou disparar uma campanha."""


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


# ── Escrita: montar e disparar campanhas (gerenciado pelo dashboard) ─────────


def _existe(cliente: httpx.Client, caminho: str, nome: str) -> bool:
    """Diz se já há um objeto com esse ``name`` no GoPhish (idempotência por nome)."""
    resp = cliente.get(caminho)
    resp.raise_for_status()
    return any(item.get("name") == nome for item in resp.json() or [])


def listar_smtp() -> list[str]:
    """Nomes dos perfis de envio (Sending Profiles) cadastrados no GoPhish.

    Raises:
        httpx.HTTPError: Falha de rede ou HTTP.
    """
    with _cliente() as cliente:
        resp = cliente.get("/api/smtp/")
        resp.raise_for_status()
        return [str(p.get("name", "")) for p in resp.json() or [] if p.get("name")]


def _sanitizar_email_remetente(remetente: str) -> str:
    """Extrai apenas o e-mail puro (sem display name, 'De:' ou < >).

    O GoPhish exige estritamente um endereço de e-mail puro (ex.:
    'avisos@empresa.com') no campo ``from_address``. Display names RFC 5322
    geram erro 500 no daemon do GoPhish.
    """
    _, addr = email.utils.parseaddr(remetente.strip())
    limpo = addr.strip() if addr else remetente.strip()
    return limpo.strip("<>").strip()


def criar_smtp(nome: str, host: str, usuario: str, senha: str, remetente: str) -> None:
    """Cria um perfil de envio (SMTP autenticado, ex.: M365).

    Args:
        nome: Nome do perfil no GoPhish.
        host: ``host:porta`` do SMTP (ex.: ``smtp.office365.com:587``).
        usuario: Usuário do SMTP.
        senha: Senha (ou senha de app) do SMTP.
        remetente: Endereço "De" (ex.: ``avisos-ti@empresa.com``). Formatos com
            display name (ex.: ``Avisos TI <avisos-ti@empresa.com>``) são
            sanitizados para o e-mail puro, exigido pelo GoPhish.

    Raises:
        PhishingError: Campo obrigatório vazio ou e-mail inválido.
        httpx.HTTPError: Falha de rede ou HTTP.
    """
    remetente_limpo = _sanitizar_email_remetente(remetente)
    if not (nome and host and usuario and remetente_limpo):
        raise PhishingError("Nome, host, usuário e remetente são obrigatórios.")
    if "@" not in remetente_limpo:
        raise PhishingError("Endereço de e-mail do remetente inválido.")
    with _cliente() as cliente:
        resp = cliente.post(
            "/api/smtp/",
            json={
                "name": nome,
                "interface_type": "SMTP",
                "host": host,
                "username": usuario,
                "password": senha,
                "from_address": remetente_limpo,
                "ignore_cert_errors": True,
            },
        )
        resp.raise_for_status()
    log.info("gophish.smtp_criado", nome=nome)


def garantir_material(isca_id: str, url_educativa: str) -> None:
    """Garante que o template de e-mail e a landing da isca existem no GoPhish.

    A landing **nunca guarda a senha** (``capture_passwords=False``) e, quando a
    isca tem ``captura_credenciais=False``, também não guarda nenhum outro campo
    digitado — só registra que a pessoa enviou o formulário. Depois do envio, a
    pessoa é mandada para a página educativa.

    Args:
        isca_id: Id da isca (ver :mod:`itgov.services.phishing_material`).
        url_educativa: Para onde redirecionar após o envio do formulário.

    Raises:
        PhishingError: Isca desconhecida.
        httpx.HTTPError: Falha de rede ou HTTP.
    """
    isca = phishing_material.obter(isca_id)
    if isca is None:
        raise PhishingError("Isca de phishing desconhecida.")
    with _cliente() as cliente:
        if not _existe(cliente, "/api/templates/", isca.template_nome):
            cliente.post(
                "/api/templates/",
                json={
                    "name": isca.template_nome,
                    "subject": isca.assunto,
                    "html": isca.email_html,
                    "text": isca.email_text,
                },
            ).raise_for_status()
            log.info("gophish.template_criado", nome=isca.template_nome)
        if not _existe(cliente, "/api/pages/", isca.landing_nome):
            cliente.post(
                "/api/pages/",
                json={
                    "name": isca.landing_nome,
                    "html": isca.landing_html,
                    "capture_credentials": isca.captura_credenciais,
                    "capture_passwords": False,
                    "redirect_url": url_educativa,
                },
            ).raise_for_status()
            log.info("gophish.landing_criada", nome=isca.landing_nome)


def criar_grupo(nome: str, emails: list[str]) -> None:
    """Cria (ou recria) o grupo de alvos com os e-mails dados.

    Args:
        nome: Nome do grupo.
        emails: Endereços dos alvos.

    Raises:
        PhishingError: Sem nome ou sem nenhum e-mail válido.
        httpx.HTTPError: Falha de rede ou HTTP.
    """
    alvos = [{"email": e.strip(), "first_name": "", "last_name": "", "position": ""} for e in emails if "@" in e]
    if not nome or not alvos:
        raise PhishingError("Informe um nome e ao menos um e-mail válido.")
    with _cliente() as cliente:
        resp = cliente.post("/api/groups/", json={"name": nome, "targets": alvos})
        resp.raise_for_status()
    log.info("gophish.grupo_criado", nome=nome, alvos=len(alvos))


def disparar_campanha(nome: str, grupo: str, smtp: str, url: str, isca_id: str) -> int:
    """Cria a campanha no GoPhish e já a lança, usando o material da isca.

    O template e a landing da isca devem existir (``garantir_material`` deve ter
    rodado antes com a mesma isca).

    Args:
        nome: Nome da campanha.
        grupo: Nome do grupo de alvos.
        smtp: Nome do perfil de envio.
        url: Endereço público onde a landing é servida (vai no link do e-mail).
        isca_id: Id da isca (ver :mod:`itgov.services.phishing_material`).

    Returns:
        Id da campanha criada.

    Raises:
        PhishingError: Campo obrigatório vazio ou isca desconhecida.
        httpx.HTTPError: Falha de rede ou HTTP.
    """
    if not (nome and grupo and smtp and url):
        raise PhishingError("Nome, grupo, perfil de envio e URL são obrigatórios.")
    isca = phishing_material.obter(isca_id)
    if isca is None:
        raise PhishingError("Isca de phishing desconhecida.")
    with _cliente() as cliente:
        resp = cliente.post(
            "/api/campaigns/",
            json={
                "name": nome,
                "template": {"name": isca.template_nome},
                "page": {"name": isca.landing_nome},
                "smtp": {"name": smtp},
                "url": url.rstrip("/"),
                "groups": [{"name": grupo}],
            },
        )
        resp.raise_for_status()
        cid = int(resp.json().get("id", 0))
    log.info("gophish.campanha_disparada", nome=nome, id=cid, grupo=grupo, isca=isca.id)
    return cid
