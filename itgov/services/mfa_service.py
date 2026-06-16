"""Serviço de adoção de MFA: classificação automática e cálculo de adoção.

Régua de classificação (em ordem de prioridade):
  1. room     → GUID no displayName  OU  UPN local começa com "sala_" (underscore)
  2. non_human → UPN local começa com prefixo de conta de serviço
  3. pending_review → conta desabilitada sem classificação óbvia
  4. human    → todo o resto (incluindo externos makeit/triunfo)

Override do banco de dados é aplicado POR CIMA da régua automática.
"""

from __future__ import annotations

import re

import structlog
from sqlalchemy.orm import Session

from itgov.models.db.account_classification import AccountClassificationOverride
from itgov.models.governance_mfa import Classificacao, Conta, DetalheGovernancaMFA

log = structlog.get_logger(__name__)

_GUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)

_PREFIXOS_NAO_HUMANO = (
    "svc_",
    "srv_",
    "bot_",
    "app_",
    "noreply",
    "no-reply",
    "mailbox_",
    "shared_",
    "adm_",
    "admin_",
    "service_",
    "sys_",
    "system_",
    "func_",
    "api_",
    "relay_",
    "smtp_",
)


def classificar_conta(upn: str, nome_exibicao: str, habilitada: bool) -> Classificacao:
    """Aplica a régua automática de classificação.

    Não consulta o banco — os overrides são aplicados pelo chamador.
    """
    local = upn.split("@")[0].lower()

    # 1. Sala: GUID no displayName OU local começa com "sala_"
    if _GUID_RE.search(nome_exibicao):
        return Classificacao.sala
    if local.startswith("sala_"):
        return Classificacao.sala

    # 2. Não-humano: prefixo de conta de serviço
    for prefixo in _PREFIXOS_NAO_HUMANO:
        if local.startswith(prefixo):
            return Classificacao.nao_humano

    # 3. Pendente revisão: conta desabilitada sem classificação clara
    if not habilitada:
        return Classificacao.pendente_revisao

    # 4. Humano (default — inclui externos makeit, triunfo, etc.)
    return Classificacao.humano


def _carregar_overrides(session: Session) -> dict[str, Classificacao]:
    """Lê todos os overrides do banco e retorna {upn → Classificacao}."""
    rows = session.query(AccountClassificationOverride).all()
    resultado: dict[str, Classificacao] = {}
    for row in rows:
        try:
            resultado[row.upn] = Classificacao(row.classificacao)
        except ValueError:
            log.warning("mfa_service.override_invalido", upn=row.upn, valor=row.classificacao)
    return resultado


def calcular_adocao_mfa(
    usuarios_graph: list[dict],
    session: Session,
) -> DetalheGovernancaMFA:
    """Calcula o detalhe de adoção de MFA a partir dos dados do Graph.

    Args:
        usuarios_graph: Lista de dicts com keys: userPrincipalName, displayName,
                        accountEnabled, mfa_enabled (enriquecido pelo MFAGraphClient).
        session: Sessão SQLAlchemy para leitura de overrides.

    Returns:
        DetalheGovernancaMFA com métricas e lista de pendentes.
    """
    overrides = _carregar_overrides(session)

    contas: list[Conta] = []
    for u in usuarios_graph:
        upn = (u.get("userPrincipalName") or "").strip().lower()
        if not upn:
            continue

        nome = u.get("displayName") or upn
        mfa = bool(u.get("mfa_enabled", False))
        ativo = bool(u.get("accountEnabled", False))

        classificacao = overrides.get(upn) or classificar_conta(upn, nome, ativo)

        contas.append(
            Conta(
                upn=upn,
                nome_exibicao=nome,
                mfa_habilitado=mfa,
                habilitada=ativo,
                classificacao=classificacao,
            )
        )

    # Contagem por bucket
    humanos = [c for c in contas if c.classificacao == Classificacao.humano]
    nao_humanos = [c for c in contas if c.classificacao == Classificacao.nao_humano]
    salas = [c for c in contas if c.classificacao == Classificacao.sala]
    pendentes = [c for c in contas if c.classificacao == Classificacao.pendente_revisao]

    # Denominador: humanos ativos habilitados
    humanos_ativos = [c for c in humanos if c.habilitada]
    com_mfa = [c for c in humanos_ativos if c.mfa_habilitado]
    sem_mfa = [c for c in humanos_ativos if not c.mfa_habilitado]

    denominador = len(humanos_ativos)
    adocao = round(len(com_mfa) / denominador * 100, 1) if denominador > 0 else 0.0

    log.info(
        "mfa_service.adocao_calculada",
        adocao_pct=adocao,
        denominador=denominador,
        com_mfa=len(com_mfa),
        sem_mfa=len(sem_mfa),
        pendentes=len(pendentes),
    )

    return DetalheGovernancaMFA(
        adocao_pct=adocao,
        denominador=denominador,
        com_mfa=len(com_mfa),
        sem_mfa=len(sem_mfa),
        total_humanos=len(humanos),
        total_nao_humanos=len(nao_humanos),
        total_salas=len(salas),
        total_pendente_revisao=len(pendentes),
        pendentes_revisao=pendentes,
    )


def gravar_override(
    upn: str,
    classificacao: Classificacao,
    revisado_por: str,
    session: Session,
) -> None:
    """Persiste ou atualiza um override manual de classificação."""
    from datetime import UTC, datetime

    upn = upn.strip().lower()
    row = session.get(AccountClassificationOverride, upn)
    if row is None:
        row = AccountClassificationOverride(upn=upn)
        session.add(row)

    row.classificacao = classificacao.value
    row.revisado_por = revisado_por
    row.revisado_em = datetime.now(UTC)
    session.commit()
    log.info("mfa_service.override_gravado", upn=upn, classificacao=classificacao.value, por=revisado_por)
