"""Isolar qualquer máquina a pedido, pelo módulo Cyber.

Alguém digita o nome (``NT0307261`` ou ``NT0307261.grupogadens.com.br``) ou o
IP da máquina. O isolamento usa o melhor mecanismo disponível:

1. **Acronis**: se a máquina tem incidente aberto no EDR que oferece
   ``WORKLOAD_ISOLATE``, isola pelo agente (corta a rede da máquina inteira).
   A API do Acronis só isola a partir de um incidente; sem incidente, não dá.
2. **FortiGate**: senão, coloca o IP em quarentena no FortiGate cuja rede
   interna contém o IP. Corta internet e tráfego entre VLANs; vizinhos na
   mesma VLAN continuam alcançando a máquina.

**Nada é feito sem aprovação**: ``isolar`` e ``liberar`` só rodam a partir das
rotas com ``@requer_aprovacao`` (super admin direto, os demais pela fila).

Proteções: gateways dos FortiGates, IPs de ``ISOLAR_IPS_PROTEGIDOS`` (vírgula)
e IPs públicos nunca vão para quarentena; a mesma máquina não é isolada duas
vezes.

As mensagens para a tela são montadas aqui, nunca a partir do texto de uma
exceção (o detalhe vai só para o log).
"""

from __future__ import annotations

import ipaddress
import os
import re
import socket
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog

from app.models.isolamento import ACRONIS, FALHOU, FORTIGATE, ISOLADA, LIBERADA
from itgov.services import acronis_edr, fortigate_api
from itgov.services.acronis_edr import (
    DESISOLAR,
    ISOLAMENTO_NAO_APLICADO,
    ISOLAR,
    STATUS_ACAO_FINAIS,
    STATUS_ACAO_OK,
    IncidenteEdr,
    IncidenteNaoEncontradoError,
)
from itgov.services.fortigate_api import QuarentenaError, QuarentenaRedeError, QuarentenaTokenError

log = structlog.get_logger(__name__)

_ALVO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
_INCIDENTES_A_OLHAR = 5


def _agora() -> datetime:
    return datetime.now(UTC)


def nome_curto(nome: str) -> str:
    """``NT0307261.grupogadens.com.br`` → ``NT0307261`` (para comparar nomes)."""
    return nome.strip().split(".")[0].upper()


def alvo_valido(alvo: str) -> bool:
    """Nome ou IP só com letras, números, ponto, hífen e _."""
    return bool(_ALVO.match((alvo or "").strip()))


def _eh_ip(texto: str) -> bool:
    try:
        ipaddress.ip_address(texto)
    except ValueError:
        return False
    return True


def resolver(alvo: str) -> tuple[str, str]:
    """Nome e IP da máquina a partir do que foi digitado (já validado por ``alvo_valido``).

    O IP vem dos clientes vistos pelos FortiGates (DHCP/ARP) e, se não achar,
    do DNS. O nome de um IP digitado vem do DHCP (pode ficar vazio).

    Returns:
        ``(maquina, ip)``; ``ip`` vazio se não deu para descobrir.
    """
    alvo = alvo.strip()
    clientes = fortigate_api.get_cached_clientes()
    if _eh_ip(alvo):
        achado = next((c for c in clientes if c.get("ip") == alvo), None)
        return (achado or {}).get("hostname", ""), alvo
    curto = nome_curto(alvo)
    achado = next((c for c in clientes if c.get("hostname") and nome_curto(c["hostname"]) == curto), None)
    if achado:
        return alvo, achado["ip"]
    try:
        return alvo, socket.gethostbyname(alvo)
    except (socket.gaierror, UnicodeError):
        return alvo, ""


def incidente_isolavel(maquina: str) -> IncidenteEdr | None:
    """Incidente aberto da máquina no EDR que oferece isolar (o mais recente).

    Raises:
        httpx.HTTPError: Falha ao falar com o Acronis.
    """
    curto = nome_curto(maquina)
    da_maquina = [i for i in acronis_edr.listar_abertos() if nome_curto(i.maquina) == curto]
    for base in da_maquina[:_INCIDENTES_A_OLHAR]:
        try:
            inc = acronis_edr.detalhe(base.incident_id, base.customer_id)
        except IncidenteNaoEncontradoError:
            continue
        if ISOLAR in inc.acoes:
            return inc
    return None


def _ips_protegidos() -> set[str]:
    return {ip.strip() for ip in os.getenv("ISOLAR_IPS_PROTEGIDOS", "").split(",") if ip.strip()}


def problema_ip(ip: str) -> str:
    """Por que o IP não pode ir para quarentena ("" se pode): público, gateway ou protegido."""
    if not ipaddress.ip_address(ip).is_private:
        return f"{ip} não é IP da rede interna."
    if ip in fortigate_api.gateways():
        return f"{ip} é gateway de um FortiGate: não pode ir para quarentena."
    if ip in _ips_protegidos():
        return f"{ip} está na lista de IPs protegidos (ISOLAR_IPS_PROTEGIDOS)."
    return ""


def ativo(maquina: str, ip: str) -> Any:
    """Isolamento ainda ativo da mesma máquina (pelo nome curto ou IP), ou None. Requer app context."""
    from app.models.isolamento import IsolamentoMaquina

    for r in IsolamentoMaquina.query.filter_by(status=ISOLADA).all():
        if (ip and r.ip == ip) or (maquina and r.maquina and nome_curto(r.maquina) == nome_curto(maquina)):
            return r
    return None


def motivo_quarentena(exc: QuarentenaError, fortigate: str) -> str:
    """Mensagem fixa para a tela conforme o tipo de falha do FortiGate."""
    if isinstance(exc, QuarentenaTokenError):
        return (
            f"O FortiGate {fortigate} recusou o token (sem permissão de escrita): "
            f"configure {fortigate_api.variavel_token_escrita(fortigate)}."
        )
    if isinstance(exc, QuarentenaRedeError):
        return f"Falha de rede ao falar com o FortiGate {fortigate}."
    return f"O FortiGate {fortigate} não aceitou; detalhes no log do app."


def _comentario(texto: str, usuario: str, motivo: str) -> str:
    return f"[Dashboard Governança TI] {texto}, aprovado por {usuario}. {motivo}".strip()[:500]


def isolar(alvo: str, motivo: str, usuario: str) -> tuple[Any, str]:
    """Isola a máquina (Acronis se der, senão quarentena no FortiGate) e registra. Requer app context.

    Só roda a partir da rota de aprovação.

    Returns:
        ``(registro, "")`` com o ``IsolamentoMaquina`` gravado (``status`` isolada
        ou falhou), ou ``(None, mensagem)`` se o pedido é inválido, a máquina já
        está isolada ou não há como isolar (nada é gravado).
    """
    from app.extensions import db
    from app.models.isolamento import IsolamentoMaquina

    if not alvo_valido(alvo):
        return None, "Informe o nome ou o IP da máquina (só letras, números, ponto, hífen e _)."
    motivo = (motivo or "").strip()[:500]
    maquina, ip = resolver(alvo)
    problema = problema_ip(ip) if ip else ""
    if problema:
        return None, problema
    if ativo(maquina, ip) is not None:
        return None, f"{maquina or ip} já está isolada."

    inc: IncidenteEdr | None = None
    aviso = ""
    if maquina and acronis_edr.configurado():
        try:
            inc = incidente_isolavel(maquina)
        except httpx.HTTPError as exc:
            aviso = "Acronis fora do ar; "
            log.warning("isolamento.acronis_falhou", maquina=maquina, erro=f"{type(exc).__name__}: {exc}"[:300])

    r = IsolamentoMaquina(alvo=alvo.strip()[:200], maquina=maquina[:200], ip=ip, motivo=motivo, pedido_por=usuario)
    if inc is not None:
        r.mecanismo, r.incident_id, r.customer_id, r.link = ACRONIS, inc.incident_id, inc.customer_id, inc.link[:500]
        r.maquina = inc.maquina[:200] or r.maquina
        try:
            r.activity_id = acronis_edr.executar_acao(inc, ISOLAR, _comentario(f"Isolar {r.maquina}", usuario, motivo))
            r.acao_status, r.status = "QUEUED", ISOLADA
            r.resultado = "Isolamento disparado no Acronis."
        except (httpx.HTTPError, ValueError) as exc:
            r.status, r.resultado = FALHOU, "O Acronis não aceitou isolar; detalhes no log do app."
            log.warning(
                "isolamento.acronis_isolar_falhou", maquina=r.maquina, erro=f"{type(exc).__name__}: {exc}"[:300]
            )
    else:
        if not ip:
            return None, (
                f"Não achei o IP de {maquina} (nem nos FortiGates, nem no DNS) e ela não tem incidente "
                "no Acronis que permita isolar. Informe o IP."
            )
        rede = fortigate_api.fortigate_da_rede(ip)
        fw = fortigate_api.por_nome(rede[0]) if rede else None
        if rede is None or fw is None:
            return None, f"Nenhum FortiGate configurado tem a rede de {ip}."
        r.mecanismo, r.fortigate = FORTIGATE, fw["nome"]
        try:
            fortigate_api.quarentenar(fw, ip)
            r.status = ISOLADA
            r.resultado = f"{aviso}IP em quarentena no FortiGate {fw['nome']} (rede {rede[1]})."
        except QuarentenaError as exc:
            r.status, r.resultado = FALHOU, motivo_quarentena(exc, fw["nome"])
    db.session.add(r)
    db.session.commit()
    log.info(
        "isolamento.isolar", id=r.id, maquina=r.maquina, ip=ip, mecanismo=r.mecanismo, status=r.status, por=usuario
    )
    return r, ""


def liberando(r: Any) -> bool:
    """Já há um desisolar disparado no Acronis ainda sem resultado?"""
    return bool(r.liberar_status) and r.liberar_status not in STATUS_ACAO_FINAIS


def liberar(r: Any, usuario: str) -> str:
    """Devolve a rede à máquina isolada. Requer app context; só a partir da rota de aprovação.

    Returns:
        "" se o pedido foi aceito (o resultado fica em ``r.resultado``), ou a
        mensagem de por que não dá (máquina não isolada ou já liberando).
    """
    from app.extensions import db

    if r.status != ISOLADA or liberando(r):
        return "Esta máquina não está isolada ou já está sendo liberada."
    if r.mecanismo == FORTIGATE:
        fw = fortigate_api.por_nome(r.fortigate)
        if fw is None:
            r.resultado = f"O FortiGate {r.fortigate} não está mais configurado; libere no FortiGate."
        else:
            try:
                fortigate_api.liberar_quarentena(fw, r.ip)
                r.status, r.resultado = LIBERADA, f"IP liberado da quarentena do FortiGate {r.fortigate}."
                r.liberado_por, r.liberado_em = usuario, _agora()
            except QuarentenaError as exc:
                r.resultado = f"{motivo_quarentena(exc, r.fortigate)} Se precisar, libere no FortiGate."
    else:
        try:
            inc = acronis_edr.detalhe(r.incident_id, r.customer_id)
            r.liberar_activity_id = acronis_edr.executar_acao(
                inc, DESISOLAR, _comentario(f"Desisolar {r.maquina}", usuario, "")
            )
            r.liberar_status, r.resultado = "QUEUED", "Desisolar disparado no Acronis."
            r.liberado_por, r.liberado_em = usuario, _agora()
        except (httpx.HTTPError, IncidenteNaoEncontradoError, ValueError) as exc:
            r.liberar_status = "FAILED"
            r.resultado = "O Acronis não aceitou desisolar; desisole pelo console do Acronis."
            log.warning("isolamento.desisolar_falhou", id=r.id, erro=f"{type(exc).__name__}: {exc}"[:300])
    db.session.commit()
    log.info("isolamento.liberar", id=r.id, maquina=r.maquina, status=r.status, por=usuario)
    return ""


def acompanhar() -> int:
    """Atualiza isolar/desisolar do Acronis que ainda não terminaram. Requer app context.

    Returns:
        Quantas ações foram consultadas.
    """
    from app.extensions import db
    from app.models.isolamento import IsolamentoMaquina

    consultadas = 0
    for r in IsolamentoMaquina.query.filter_by(mecanismo=ACRONIS, status=ISOLADA).all():
        try:
            if r.activity_id and r.acao_status not in STATUS_ACAO_FINAIS:
                consultadas += 1
                r.acao_status = acronis_edr.status_acao(r.incident_id, r.customer_id, r.activity_id)[:40]
                if r.acao_status in ISOLAMENTO_NAO_APLICADO:
                    r.status = FALHOU
                    r.resultado = f"O Acronis não isolou ({r.acao_status})."
            if r.liberar_activity_id and liberando(r):
                consultadas += 1
                r.liberar_status = acronis_edr.status_acao(r.incident_id, r.customer_id, r.liberar_activity_id)[:40]
                if r.liberar_status in STATUS_ACAO_OK:
                    r.status, r.resultado = LIBERADA, "Máquina desisolada no Acronis."
                elif r.liberar_status in STATUS_ACAO_FINAIS:
                    r.resultado = f"O Acronis não desisolou ({r.liberar_status}). Tente de novo ou use o console."
        except httpx.HTTPError as exc:
            log.warning("isolamento.status_falhou", id=r.id, erro=str(exc)[:200])
    db.session.commit()
    return consultadas


def painel() -> dict[str, Any]:
    """Isoladas agora e as últimas liberadas/falhas. Requer app context."""
    from app.models.isolamento import IsolamentoMaquina

    ativas = IsolamentoMaquina.query.filter_by(status=ISOLADA).order_by(IsolamentoMaquina.isolado_em.desc()).all()
    historico = (
        IsolamentoMaquina.query.filter(IsolamentoMaquina.status != ISOLADA)
        .order_by(IsolamentoMaquina.isolado_em.desc())
        .limit(20)
        .all()
    )
    return {
        "ativas": [r.para_dict() | {"liberando": liberando(r)} for r in ativas],
        "historico": [r.para_dict() for r in historico],
    }
