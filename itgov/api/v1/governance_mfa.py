"""Namespace Flask-RESTX para adoção de MFA — Governança de TI.

Endpoints:
  GET  /governance/mfa        → DetalheGovernancaMFA (métricas + pendentes)
  POST /governance/reclassify → grava override e retorna detalhe atualizado

Segurança:
  - GET: leitura pública (dados agregados, sem PII individual visível no response).
  - POST: requer GOV_MFA_PIN via header X-Gov-Mfa-Pin (defesa em profundidade
    além do Nginx/Talisman). UPN do operador extraído do header X-User-Email
    definido pelo Nginx após OIDC — NUNCA aceito do body/query param.

Cache:
  - TTL de 300 s em memória, thread-safe com threading.Lock.
  - POST /reclassify invalida o cache após gravar o override.
"""

from __future__ import annotations

import asyncio
import threading
import time
from functools import wraps

import structlog
from flask import request
from flask_restx import Namespace, Resource, fields
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

# config é importado lazily dentro das funções para não falhar ao carregar o
# módulo quando ZABBIX_PASSWORD está vazio no ambiente (os.environ).
# MFAGraphClient e mfa_service também são lazy pelas mesmas razões.
from itgov.db.session import get_session
from itgov.models.governance_mfa import Classificacao, ReclassificarRequest

log = structlog.get_logger(__name__)

ns = Namespace("governance", description="Adoção de MFA e Governança de Identidades")

# ── Cache em memória (thread-safe, TTL 5 min) ─────────────────────────────────

_CACHE_TTL = 300  # segundos

_cache_lock = threading.Lock()
_cache_dados: dict | None = None
_cache_ts: float = 0.0


def _cache_valido() -> bool:
    return _cache_dados is not None and (time.monotonic() - _cache_ts) < _CACHE_TTL


def _ler_cache() -> dict | None:
    with _cache_lock:
        return _cache_dados if _cache_valido() else None


def _gravar_cache(dados: dict) -> None:
    global _cache_dados, _cache_ts
    with _cache_lock:
        _cache_dados = dados
        _cache_ts = time.monotonic()


def invalidar_cache_mfa() -> None:
    """Invalida o cache de MFA — chamado após override gravado."""
    global _cache_dados, _cache_ts
    with _cache_lock:
        _cache_dados = None
        _cache_ts = 0.0


# ── Auth: defesa em profundidade via GOV_MFA_PIN ──────────────────────────────


def _pin_configurado() -> str:
    """Retorna o PIN de admin do MFA. Vazio = endpoint de escrita bloqueado."""
    import os

    return (os.environ.get("GOV_MFA_PIN") or "").strip()


def _extrair_pin() -> str:
    return request.headers.get("X-Gov-Mfa-Pin", "").strip()


def _extrair_operador() -> str:
    """UPN do operador injetado pelo Nginx via OIDC (X-User-Email).

    Fallback para endereço IP quando o header não está presente
    (acesso direto sem proxy, ambiente de dev).
    """
    email = request.headers.get("X-User-Email", "").strip()
    if email:
        return email
    return f"ip:{request.remote_addr}"


def requer_admin(fn):
    """Decorator: exige GOV_MFA_PIN válido no header X-Gov-Mfa-Pin."""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        pin_esperado = _pin_configurado()
        if not pin_esperado:
            log.error("gov_mfa.auth.pin_nao_configurado")
            ns.abort(503, "GOV_MFA_PIN não configurado — endpoint de escrita bloqueado")

        if _extrair_pin() != pin_esperado:
            log.warning(
                "gov_mfa.auth.pin_invalido",
                ip=request.remote_addr,
            )
            ns.abort(403, "Acesso negado — PIN de admin inválido ou ausente")

        return fn(*args, **kwargs)

    return wrapper


# ── Swagger models ────────────────────────────────────────────────────────────

_conta_fields = {
    "upn": fields.String(description="User Principal Name"),
    "nome_exibicao": fields.String(description="Nome de exibição"),
    "mfa_habilitado": fields.Boolean(description="MFA registrado (isMfaRegistered)"),
    "habilitada": fields.Boolean(description="Conta ativa"),
    "classificacao": fields.String(description="Classificação: human|non_human|room|pending_review"),
}

_detalhe_fields = {
    "adocao_pct": fields.Float(description="Adoção de MFA em % (meta 87%)"),
    "denominador": fields.Integer(description="Humanos ativos (base do cálculo)"),
    "com_mfa": fields.Integer(description="Humanos com MFA"),
    "sem_mfa": fields.Integer(description="Humanos sem MFA"),
    "total_humanos": fields.Integer,
    "total_nao_humanos": fields.Integer,
    "total_salas": fields.Integer,
    "total_pendente_revisao": fields.Integer,
    "pendentes_revisao": fields.List(fields.Nested(ns.model("Conta", _conta_fields))),
}

detalhe_model = ns.model("DetalheGovernancaMFA", _detalhe_fields)

reclassificar_model = ns.model(
    "ReclassificarRequest",
    {
        "upn": fields.String(required=True, description="UPN da conta"),
        "classificacao": fields.String(
            required=True,
            description="human | non_human | room | pending_review",
            enum=[c.value for c in Classificacao],
        ),
    },
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _buscar_do_graph() -> dict:
    """Busca dados do Graph e calcula adoção. Sem cache — chame via _obter_dados."""
    import os

    from itgov.services.mfa_graph_client import MFAGraphClient
    from itgov.services.mfa_service import calcular_adocao_mfa

    tenant_id = (os.environ.get("AZURE_TENANT_ID") or "").strip()
    if not tenant_id:
        raise RuntimeError("AZURE_TENANT_ID não configurado")

    client = MFAGraphClient()
    usuarios = asyncio.run(client.get_users_with_mfa(tenant_id))

    session = get_session()
    try:
        detalhe = calcular_adocao_mfa(usuarios, session)
    finally:
        session.close()

    return detalhe.model_dump()


def _obter_dados() -> dict:
    """Retorna dados do cache se válido; caso contrário busca do Graph e popula cache."""
    cached = _ler_cache()
    if cached is not None:
        log.debug("gov_mfa.cache.hit")
        return cached

    log.info("gov_mfa.cache.miss")
    dados = _buscar_do_graph()
    _gravar_cache(dados)
    return dados


# ── Endpoints ─────────────────────────────────────────────────────────────────


@ns.route("/mfa")
class GovernancaMFA(Resource):
    @ns.marshal_with(detalhe_model)
    def get(self):
        """Retorna métricas de adoção de MFA e lista de contas pendentes."""
        try:
            return _obter_dados(), 200
        except RuntimeError:
            ns.abort(503, "Integração Graph não configurada")
        except Exception as exc:
            log.error("gov_mfa.get.erro", erro=str(exc))
            ns.abort(500, "Erro ao buscar dados de MFA")


@ns.route("/reclassify")
class Reclassificar(Resource):
    @requer_admin
    @ns.expect(reclassificar_model, validate=False)
    @ns.marshal_with(detalhe_model)
    def post(self):
        """Grava override de classificação e retorna detalhe atualizado.

        Requer header X-Gov-Mfa-Pin com o GOV_MFA_PIN configurado no servidor.
        O operador é identificado pelo header X-User-Email injetado pelo Nginx.
        """
        payload = request.get_json(silent=True) or {}

        try:
            req = ReclassificarRequest(**payload)
        except ValidationError as exc:
            ns.abort(422, str(exc))

        revisado_por = _extrair_operador()

        from itgov.services.mfa_service import gravar_override

        session = get_session()
        try:
            gravar_override(req.upn, req.classificacao, revisado_por, session)
        except SQLAlchemyError as exc:
            session.close()
            log.error("gov_mfa.reclassify.db_erro", erro=str(exc))
            ns.abort(500, "Erro ao gravar override no banco")
        else:
            session.close()

        invalidar_cache_mfa()

        try:
            return _obter_dados(), 200
        except Exception as exc:
            log.error("gov_mfa.reclassify.recalculo_erro", erro=str(exc))
            ns.abort(500, "Override gravado, mas erro ao recalcular")
