"""Logs de segurança dos FortiGate para o agente SIEM do módulo Cyber.

Os FortiGate não têm disco nem FortiAnalyzer: o log fica só na memória, que é
pequena e rotativa (na Sede, 1000 linhas cobrem ~10 minutos). Por isso o
agente lê a memória a cada poucos minutos, paginando até alcançar o último
evento já guardado (``ler_novos``).

Funções puras (testáveis sem rede):

- ``normalizar``: linha crua do FortiOS → ``EventoSiem``;
- ``relevante``: o que vale guardar (o resto é rotina: SD-WAN ok, DHCP...);
- ``resumir``: contagens objetivas por FortiGate (falhas de VPN por IP e por
  usuário, logins de admin, mudanças de configuração, bloqueios web, IPS);
- ``achados_por_regra``: achados que não dependem da IA (força bruta na VPN,
  admin falhando, FortiGuard fora...). A IA parte deles e pode acrescentar.

Tudo aqui é leitura: nenhuma chamada altera o FortiGate.
"""

from __future__ import annotations

import hashlib
import ipaddress
from collections import Counter
from datetime import UTC, datetime
from typing import Any

import structlog
from pydantic import BaseModel

from itgov.services import fortigate_api

log = structlog.get_logger(__name__)

# Tipos lidos da memória. No FortiOS 7 o subtipo de ``event`` é ignorado
# (``event/system`` devolve todos os eventos); ``traffic`` gira em segundos e
# só faz sentido por syslog.
TIPOS = ("event/system", "webfilter", "ips", "virus", "anomaly", "app-ctrl")
LINHAS_POR_PAGINA = 1000
MAX_PAGINAS = 10

_NIVEIS_RELEVANTES = frozenset({"warning", "error", "alert", "critical", "emergency"})
_DESCRICOES_RELEVANTES = (
    "login",
    "admin",
    "configured",
    "ssl vpn tunnel up",
    "ssl vpn new connection",
    "object attribute",
)
_ACOES_BLOQUEIO = frozenset({"blocked", "block", "dropped", "reset", "deny", "detected"})

SEVERIDADES = ("critica", "alta", "media", "baixa", "info")


class EventoSiem(BaseModel):
    """Um evento de log do FortiGate, só com os campos que o agente usa.

    Attributes:
        chave: Identificador estável (deduplica leituras repetidas da memória).
        fortigate: Nome do FortiGate (``Sede``, ``Shopping``...).
        quando: Momento do evento (UTC).
        eventtime: ``eventtime`` cru do FortiOS (nanossegundos), para paginar.
        tipo: ``event``, ``utm``...
        subtipo: ``vpn``, ``system``, ``webfilter``, ``ips``...
        nivel: ``notice``, ``warning``, ``error``, ``alert``...
        descricao: ``logdesc`` (ex.: ``SSL VPN login fail``).
        origem_ip: IP remoto/origem.
        usuario: Usuário envolvido, quando há.
        acao: ``action`` (``blocked``, ``ssl-login-fail``...).
        motivo: ``reason``.
        mensagem: ``msg``/URL/ataque, resumido.
    """

    chave: str
    fortigate: str
    quando: datetime
    eventtime: int
    tipo: str = ""
    subtipo: str = ""
    nivel: str = ""
    descricao: str = ""
    origem_ip: str = ""
    usuario: str = ""
    acao: str = ""
    motivo: str = ""
    mensagem: str = ""


def _texto(valor: Any, limite: int) -> str:
    return str(valor or "").strip()[:limite]


def normalizar(fortigate: str, raw: dict[str, Any]) -> EventoSiem | None:
    """Converte uma linha crua de ``/api/v2/log/memory/...``.

    Args:
        fortigate: Nome do FortiGate de origem.
        raw: Linha como o FortiOS devolve.

    Returns:
        O evento, ou None se a linha não tem ``eventtime`` (não dá para ordenar).
    """
    try:
        eventtime = int(raw.get("eventtime") or 0)
    except (TypeError, ValueError):
        return None
    if eventtime <= 0:
        return None
    # eventtime vem em ns nas versões atuais; versões antigas mandavam segundos
    segundos = eventtime / 1e9 if eventtime > 10**12 else float(eventtime)
    origem = raw.get("remip") or raw.get("srcip") or ""
    usuario = raw.get("user") or raw.get("unauthuser") or ""
    mensagem = raw.get("msg") or raw.get("url") or raw.get("attack") or raw.get("virus") or ""
    if raw.get("hostname") and raw.get("subtype") == "webfilter":
        mensagem = f"{raw.get('hostname')} — {raw.get('catdesc') or mensagem}"
    bruto = "|".join(str(x) for x in (fortigate, eventtime, raw.get("logid"), origem, usuario, raw.get("sessionid")))
    return EventoSiem(
        chave=hashlib.sha1(bruto.encode(), usedforsecurity=False).hexdigest()[:32],
        fortigate=fortigate,
        quando=datetime.fromtimestamp(segundos, tz=UTC),
        eventtime=eventtime,
        tipo=_texto(raw.get("type"), 20),
        subtipo=_texto(raw.get("subtype"), 30),
        nivel=_texto(raw.get("level"), 20),
        descricao=_texto(raw.get("logdesc") or raw.get("eventtype"), 120),
        origem_ip=_texto(origem, 45),
        usuario=_texto(usuario, 120),
        acao=_texto(raw.get("action"), 40),
        motivo=_texto(raw.get("reason"), 80),
        mensagem=_texto(mensagem, 300),
    )


def relevante(ev: EventoSiem) -> bool:
    """O evento interessa à segurança? Rotina (SD-WAN ok, DHCP, progresso) fica de fora."""
    if ev.nivel in _NIVEIS_RELEVANTES:
        return True
    descricao = ev.descricao.lower()
    if any(trecho in descricao for trecho in _DESCRICOES_RELEVANTES):
        return True
    return ev.tipo == "utm" and ev.acao.lower() in _ACOES_BLOQUEIO


def ler_novos(fw: dict[str, str], tipo: str, desde_eventtime: int) -> list[EventoSiem]:
    """Lê da memória do FortiGate os eventos mais novos que ``desde_eventtime``.

    A memória devolve do mais novo para o mais antigo; pagina até passar do
    último evento já guardado ou até ``MAX_PAGINAS``.

    Args:
        fw: FortiGate de ``fortigate_api.configurados()``.
        tipo: Um dos ``TIPOS``.
        desde_eventtime: ``eventtime`` do último evento guardado (0 = sem histórico).

    Returns:
        Eventos novos, normalizados.

    Raises:
        requests.RequestException, ValueError: Falha ao falar com o FortiGate.
    """
    novos: list[EventoSiem] = []
    for pagina in range(MAX_PAGINAS):
        linhas = fortigate_api._get(
            fw, f"log/memory/{tipo}?rows={LINHAS_POR_PAGINA}&start={pagina * LINHAS_POR_PAGINA}"
        )
        if not linhas:
            break
        alcancou = False
        for raw in linhas:
            ev = normalizar(fw["nome"], raw)
            if ev is None:
                continue
            if ev.eventtime <= desde_eventtime:
                alcancou = True
                continue
            novos.append(ev)
        if alcancou or len(linhas) < LINHAS_POR_PAGINA:
            break
    else:
        log.warning("siem_fortigate.memoria_alem_do_limite", fortigate=fw["nome"], tipo=tipo, paginas=MAX_PAGINAS)
    return novos


# ── Resumo objetivo e regras ─────────────────────────────────────────────────


def _privado(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip.split("(")[-1].rstrip(")")).is_private
    except ValueError:
        return False


def _top(contador: Counter[str], n: int) -> list[list[Any]]:
    return [[k, v] for k, v in contador.most_common(n) if k]


def resumir(eventos: list[EventoSiem]) -> dict[str, dict[str, Any]]:
    """Contagens objetivas por FortiGate, para a IA e para as regras.

    Args:
        eventos: Eventos relevantes de uma janela.

    Returns:
        Nome do FortiGate → resumo (volumes, VPN, admin, configuração, web, IPS).
    """
    por_fw: dict[str, list[EventoSiem]] = {}
    for ev in eventos:
        por_fw.setdefault(ev.fortigate, []).append(ev)
    resumo: dict[str, dict[str, Any]] = {}
    for fw, evs in sorted(por_fw.items()):
        desc = [e.descricao.lower() for e in evs]
        falhas = [e for e, d in zip(evs, desc, strict=True) if d == "ssl vpn login fail"]
        sucessos = [e for e, d in zip(evs, desc, strict=True) if d in ("ssl vpn tunnel up", "ssl vpn new connection")]
        admin_falha = [e for e, d in zip(evs, desc, strict=True) if "admin login failed" in d]
        admin_ok = [e for e, d in zip(evs, desc, strict=True) if "admin login successful" in d]
        config = [e for e, d in zip(evs, desc, strict=True) if "configured" in d or "object attribute" in d]
        ipsec = [e for e, d in zip(evs, desc, strict=True) if d == "ipsec phase 1 error"]
        web = [e for e in evs if e.subtipo == "webfilter"]
        ameacas = [e for e in evs if e.subtipo in ("ips", "virus", "anomaly")]
        resumo[fw] = {
            "eventos": len(evs),
            "por_tipo": _top(Counter(f"{e.subtipo}/{e.nivel}/{e.descricao}" for e in evs), 12),
            "vpn_ssl_falhas": {
                "total": len(falhas),
                "ips_distintos": len({e.origem_ip for e in falhas}),
                "usuarios_distintos": len({e.usuario for e in falhas}),
                "top_ips": _top(Counter(e.origem_ip for e in falhas), 8),
                "top_usuarios": _top(Counter(e.usuario for e in falhas), 8),
                "motivos": _top(Counter(e.motivo for e in falhas), 3),
            },
            "vpn_ssl_sucessos": {
                "total": len(sucessos),
                "usuarios": _top(Counter(e.usuario for e in sucessos), 10),
            },
            "admin_login_falha": _top(Counter(f"{e.usuario} de {e.origem_ip}" for e in admin_falha), 8),
            "admin_login_ok": _top(Counter(f"{e.usuario} de {e.origem_ip}" for e in admin_ok), 8),
            "admin_login_ok_externo": sorted(
                {f"{e.usuario} de {e.origem_ip}" for e in admin_ok if e.origem_ip and not _privado(e.origem_ip)}
            )[:8],
            "config_alterada": [f"{e.usuario}: {e.mensagem[:140]}" for e in config[:10]],
            "ipsec_falhas_por_peer": _top(Counter(e.origem_ip for e in ipsec), 6),
            "web_erros_fortiguard": sum(
                1 for e in web if "rating error" in e.mensagem.lower() or e.descricao == "ftgd_err"
            ),
            "web_bloqueios": _top(
                Counter(e.mensagem.split(" — ")[-1] for e in web if e.acao.lower() in _ACOES_BLOQUEIO), 6
            ),
            "ameacas": _top(Counter(f"{e.subtipo}: {e.mensagem[:80]}" for e in ameacas), 8),
        }
    return resumo


class Achado(BaseModel):
    """Um achado do agente: algo que merece atenção, com evidência e recomendação.

    Attributes:
        assinatura: Slug estável do tipo de achado (junta repetições entre análises).
        fortigate: FortiGate afetado, ou ``geral``.
        severidade: Uma de ``SEVERIDADES``.
        titulo: Frase curta.
        resumo: O que está acontecendo, em poucas linhas.
        evidencias: Números e exemplos que sustentam o achado.
        recomendacao: O que fazer (o agente não age sozinho).
    """

    assinatura: str
    fortigate: str
    severidade: str
    titulo: str
    resumo: str
    evidencias: list[str]
    recomendacao: str


def achados_por_regra(resumo: dict[str, dict[str, Any]], minutos: float) -> list[Achado]:
    """Achados objetivos, que valem mesmo sem a IA.

    Args:
        resumo: Saída de ``resumir``.
        minutos: Tamanho da janela analisada (para as taxas).

    Returns:
        Achados encontrados, do mais grave ao menos grave.
    """
    achados: list[Achado] = []
    por_hora = 60 / max(minutos, 1)
    for fw, r in resumo.items():
        falhas = r["vpn_ssl_falhas"]
        if falhas["total"] >= 30 and falhas["usuarios_distintos"] >= 15:
            taxa = round(falhas["total"] * por_hora)
            achados.append(
                Achado(
                    assinatura="vpn-ssl-forca-bruta",
                    fortigate=fw,
                    severidade="alta",
                    titulo=f"Força bruta distribuída no SSL VPN ({taxa}/h)",
                    resumo=(
                        f"{falhas['total']} logins recusados em {minutos:.0f} min, "
                        f"{falhas['usuarios_distintos']} usuários e {falhas['ips_distintos']} IPs diferentes."
                    ),
                    evidencias=[f"IP {ip}: {n}" for ip, n in falhas["top_ips"][:5]]
                    + [f"usuário {u}: {n}" for u, n in falhas["top_usuarios"][:5]],
                    recomendacao=(
                        "Restringir o SSL VPN a IPs do Brasil (local-in por geolocalização), exigir FortiToken "
                        "e ligar login-attempt-limit/login-block-time."
                    ),
                )
            )
        else:
            for ip, n in falhas["top_ips"]:
                if n >= 20:
                    achados.append(
                        Achado(
                            assinatura=f"vpn-ssl-falhas-ip-{ip}",
                            fortigate=fw,
                            severidade="media",
                            titulo=f"{n} logins de VPN recusados vindos de {ip}",
                            resumo=f"Um único IP errou a senha do SSL VPN {n} vezes em {minutos:.0f} min.",
                            evidencias=[f"usuários tentados: {falhas['usuarios_distintos']}"],
                            recomendacao="Bloquear o IP no FortiGate se não for de um usuário conhecido.",
                        )
                    )
        if r["admin_login_falha"]:
            total = sum(n for _, n in r["admin_login_falha"])
            achados.append(
                Achado(
                    assinatura="admin-login-falha",
                    fortigate=fw,
                    severidade="alta" if total >= 5 else "media",
                    titulo=f"{total} tentativa(s) de login de administrador recusada(s)",
                    resumo="Alguém errou a senha da administração do FortiGate.",
                    evidencias=[f"{q}: {n}" for q, n in r["admin_login_falha"]],
                    recomendacao="Conferir quem tentou; administração do FortiGate só deve abrir pela rede interna.",
                )
            )
        if r["admin_login_ok_externo"]:
            achados.append(
                Achado(
                    assinatura="admin-login-externo",
                    fortigate=fw,
                    severidade="critica",
                    titulo="Login de administrador vindo de IP da internet",
                    resumo="A administração do FortiGate aceitou login de um IP público.",
                    evidencias=list(r["admin_login_ok_externo"]),
                    recomendacao="Confirmar com o administrador; se não reconhecer, trocar a senha e fechar a interface de gerência para a internet.",
                )
            )
        if r["config_alterada"]:
            achados.append(
                Achado(
                    assinatura="config-alterada",
                    fortigate=fw,
                    severidade="info",
                    titulo=f"{len(r['config_alterada'])} alteração(ões) de configuração",
                    resumo="Mudanças de configuração registradas no período.",
                    evidencias=list(r["config_alterada"][:6]),
                    recomendacao="Conferir se as mudanças eram esperadas.",
                )
            )
        for peer, n in r["ipsec_falhas_por_peer"]:
            if n * por_hora >= 200:
                achados.append(
                    Achado(
                        assinatura=f"ipsec-falhando-{peer}",
                        fortigate=fw,
                        severidade="baixa",
                        titulo=f"Túnel IPsec com {peer} falhando sem parar",
                        resumo=f"{n} erros de fase 1 em {minutos:.0f} min: provável peer com configuração diferente.",
                        evidencias=[f"{peer}: {n} erros"],
                        recomendacao="Conferir chave e propostas do túnel com o outro lado, ou remover o túnel se não é mais usado.",
                    )
                )
        if r["web_erros_fortiguard"] >= 20:
            achados.append(
                Achado(
                    assinatura="fortiguard-sem-resposta",
                    fortigate=fw,
                    severidade="media",
                    titulo="Filtro web sem resposta do FortiGuard",
                    resumo=f"{r['web_erros_fortiguard']} erros de classificação: sites podem passar sem filtro.",
                    evidencias=[f"erros de rating: {r['web_erros_fortiguard']}"],
                    recomendacao="Conferir licença e conectividade do FortiGuard (diagnose debug rating).",
                )
            )
        if r["ameacas"]:
            achados.append(
                Achado(
                    assinatura="ameaca-detectada",
                    fortigate=fw,
                    severidade="alta",
                    titulo="IPS/antivírus do FortiGate detectou ameaça",
                    resumo="Assinaturas de ataque ou vírus apareceram no tráfego.",
                    evidencias=[f"{a}: {n}" for a, n in r["ameacas"]],
                    recomendacao="Ver a máquina de origem e cruzar com o Acronis.",
                )
            )
    return sorted(achados, key=lambda a: SEVERIDADES.index(a.severidade))
