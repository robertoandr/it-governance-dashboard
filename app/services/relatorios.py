"""Catálogo de relatórios de governança de TI (Conferência V2.0 rl1).

Cada relatório é uma lista de seções; cada seção lê dados que o dashboard já
mantém em cache (Zabbix, FortiGate, CFTV, Zendesk, Microsoft 365, Acronis) e
devolve indicadores e tabelas num formato único. A mesma estrutura vira página
para imprimir/PDF e CSV, e o relatório personalizado é só uma escolha livre de
seções.

Seção que falha não derruba o relatório: aparece com o motivo no lugar dos dados.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Callable
from datetime import date, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

import httpx
import requests
import structlog
from pydantic import BaseModel

log = structlog.get_logger(__name__)

TZ = ZoneInfo("America/Sao_Paulo")
Tom = Literal["ok", "alerta", "ruim", "neutro"]


class Kpi(BaseModel):
    """Indicador de destaque no topo da seção."""

    rotulo: str
    valor: str
    tom: Tom = "neutro"
    detalhe: str = ""


class Tabela(BaseModel):
    """Tabela de uma seção (também é o que vai para o CSV)."""

    titulo: str
    colunas: list[str]
    linhas: list[list[str]]
    nota: str = ""


class Secao(BaseModel):
    """Bloco de um relatório."""

    chave: str
    titulo: str
    fonte: str
    kpis: list[Kpi] = []
    tabelas: list[Tabela] = []
    erro: str | None = None


class Relatorio(BaseModel):
    """Relatório pronto para a página ou para o CSV."""

    chave: str
    titulo: str
    descricao: str
    gerado_em: datetime
    secoes: list[Secao]


# ── Formatação ────────────────────────────────────────────────────────────────


def _num(valor: Any) -> float | None:
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def _pct(valor: Any) -> str:
    n = _num(valor)
    return "—" if n is None else f"{n:.1f}%".replace(".", ",")


def _int(valor: Any) -> str:
    n = _num(valor)
    return "—" if n is None else f"{n:,.0f}".replace(",", ".")


def _brl(valor: Any) -> str:
    n = _num(valor)
    if n is None:
        return "—"
    return "R$ " + f"{n:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _ms(valor: Any) -> str:
    n = _num(valor)
    return "—" if n is None else f"{n:.1f} ms".replace(".", ",")


def _tom_pct(valor: Any, bom: float, ruim: float) -> Tom:
    """Tom de um percentual em que maior é melhor."""
    n = _num(valor)
    if n is None:
        return "neutro"
    if n >= bom:
        return "ok"
    return "ruim" if n < ruim else "alerta"


def _tom_qtd(valor: Any) -> Tom:
    """Tom de uma contagem de problemas (zero é bom)."""
    n = _num(valor)
    return "neutro" if n is None else ("ok" if n == 0 else "ruim")


# ── Seções: infraestrutura ────────────────────────────────────────────────────


def secao_disponibilidade() -> Secao:
    """Disponibilidade dos hosts monitorados e problemas abertos no Zabbix."""
    from itgov.api.v1.zabbix_monitoring import get_cached_zabbix_summary
    from itgov.api.v1.zabbix_triggers import get_cached_triggers

    resumo = get_cached_zabbix_summary() or {}
    triggers = get_cached_triggers() or {}
    contagem = triggers.get("counts") or {}
    problemas = sorted(triggers.get("problems") or [], key=lambda p: -int(p.get("severity") or 0))
    return Secao(
        chave="disponibilidade",
        titulo="Disponibilidade e incidentes",
        fonte="Zabbix — situação atual dos hosts e problemas em aberto",
        kpis=[
            Kpi(
                rotulo="Disponibilidade",
                valor=_pct(resumo.get("uptime_pct")),
                tom=_tom_pct(resumo.get("uptime_pct"), 99, 95),
            ),
            Kpi(rotulo="Hosts monitorados", valor=_int(resumo.get("total_monitorado"))),
            Kpi(rotulo="Hosts fora", valor=_int(resumo.get("hosts_down")), tom=_tom_qtd(resumo.get("hosts_down"))),
            Kpi(
                rotulo="Problemas em aberto",
                valor=_int(triggers.get("total")),
                tom=_tom_qtd(triggers.get("total")),
                detalhe=", ".join(f"{k}: {v}" for k, v in contagem.items() if v),
            ),
        ],  # fmt: skip
        tabelas=[
            Tabela(
                titulo="Problemas em aberto",
                colunas=["Severidade", "Host", "Problema", "Desde", "Reconhecido"],
                linhas=[
                    [
                        p.get("severity_label", ""),
                        p.get("host", ""),
                        p.get("name", ""),
                        p.get("since", ""),
                        "Sim" if p.get("acknowledged") else "Não",
                    ]
                    for p in problemas
                ],  # fmt: skip
            )
        ],
    )


def secao_links() -> Secao:
    """WANs, SLAs de internet e túneis medidos pelos FortiGates."""
    from itgov.services.fortigate_api import get_cached_fortigates

    fortigates = get_cached_fortigates() or []
    wans: list[list[str]] = []
    slas: list[list[str]] = []
    fora = 0
    for fw in fortigates:
        for w in fw.get("wans", []):
            fora += 0 if w.get("link") else 1
            wans.append([fw.get("unidade", ""), w.get("operadora", ""), w.get("iface", ""),
                         "Ativo" if w.get("link") else "Sem link", _int(w.get("speed_mbps")), _int(w.get("erros"))])  # fmt: skip
        for sla in fw.get("sdwan", []):
            for m in sla.get("members", []):
                slas.append([fw.get("unidade", ""), sla.get("sla", ""), m.get("label", ""), _ms(m.get("latency_ms")),
                             _ms(m.get("jitter_ms")), _pct(m.get("loss_pct")), m.get("status", "")])  # fmt: skip
    caidos = sum(1 for s in slas if s[-1] == "down")
    return Secao(
        chave="links",
        titulo="Links WAN e túneis",
        fonte="API dos FortiGates — leitura atual",
        kpis=[
            Kpi(
                rotulo="FortiGates lidos", valor=f"{sum(1 for f in fortigates if f.get('api_up'))} de {len(fortigates)}"
            ),
            Kpi(rotulo="WANs sem link", valor=str(fora), tom=_tom_qtd(fora)),
            Kpi(rotulo="Caminhos SD-WAN fora", valor=str(caidos), tom=_tom_qtd(caidos)),
        ],
        tabelas=[
            Tabela(
                titulo="WANs",
                colunas=["Unidade", "Operadora", "Interface", "Link", "Velocidade (Mbps)", "Erros"],
                linhas=wans,
            ),
            Tabela(
                titulo="SLAs do SD-WAN",
                colunas=["Unidade", "SLA", "Caminho", "Latência", "Jitter", "Perda", "Status"],
                linhas=slas,
            ),
        ],  # fmt: skip
    )


def secao_cftv() -> Secao:
    """Câmeras e gravadores: quem está fora e quem mais caiu."""
    from itgov.api.v1.cftv_monitoring import get_cached_cftv_summary, get_cached_historico_quedas

    dispositivos = (get_cached_cftv_summary() or {}).get("devices") or []
    quedas = get_cached_historico_quedas() or {}
    offline = [d for d in dispositivos if d.get("status") != "up"]
    nomes = {d.get("host"): d for d in dispositivos}
    ranking = sorted(
        quedas.items(), key=lambda kv: (-int(kv[1].get("quedas") or 0), -float(kv[1].get("segundos") or 0))
    )
    pct_on = (len(dispositivos) - len(offline)) / len(dispositivos) * 100 if dispositivos else None
    return Secao(
        chave="cftv",
        titulo="CFTV",
        fonte="Zabbix — ping das câmeras e gravadores; quedas dos últimos dias",
        kpis=[
            Kpi(rotulo="Dispositivos", valor=_int(len(dispositivos))),
            Kpi(rotulo="Online", valor=_pct(pct_on), tom=_tom_pct(pct_on, 98, 90)),
            Kpi(rotulo="Offline agora", valor=str(len(offline)), tom=_tom_qtd(len(offline))),
        ],
        tabelas=[
            Tabela(
                titulo="Offline agora",
                colunas=["Dispositivo", "Local", "IP", "Gravador"],
                linhas=[
                    [d.get("name", ""), d.get("andar") or d.get("loja", ""), d.get("ip", ""), d.get("gravador", "")]
                    for d in offline
                ],
            ),  # fmt: skip
            Tabela(
                titulo="Mais quedas",
                colunas=["Dispositivo", "Quedas", "Tempo offline", "Última queda", "Ainda fora"],
                linhas=[
                    [
                        (nomes.get(h) or {}).get("name", h),
                        _int(q.get("quedas")),
                        q.get("tempo", ""),
                        q.get("ultima", ""),
                        "Sim" if q.get("em_aberto") else "Não",
                    ]
                    for h, q in ranking[:20]
                ],
            ),  # fmt: skip
        ],
    )


# ── Seções: service desk ──────────────────────────────────────────────────────


def secao_sla() -> Secao:
    """Cumprimento de SLA, tempos e chamados mais antigos (Zendesk)."""
    from itgov.api.v1.zendesk import get_cached_sla_detail

    sla = get_cached_sla_detail() or {}
    periodo = sla.get("period") or {}
    prioridades = {"urgent": "Urgente", "high": "Alta", "normal": "Normal", "low": "Baixa"}
    idades = sla.get("age_buckets") or {}
    return Secao(
        chave="sla",
        titulo="SLA de atendimento",
        fonte=f"Zendesk — política {sla.get('sla_policy') or 'de SLA'}, últimos {periodo.get('window_days', 30)} dias",
        kpis=[
            Kpi(
                rotulo="SLA cumprido",
                valor=_pct(periodo.get("compliance_pct")),
                tom=_tom_pct(periodo.get("compliance_pct"), 90, 70),
            ),
            Kpi(
                rotulo="1ª resposta no prazo",
                valor=_pct(periodo.get("first_reply_compliance_pct")),
                tom=_tom_pct(periodo.get("first_reply_compliance_pct"), 90, 70),
            ),
            Kpi(
                rotulo="Resolução no prazo",
                valor=_pct(periodo.get("resolution_compliance_pct")),
                tom=_tom_pct(periodo.get("resolution_compliance_pct"), 90, 70),
            ),
            Kpi(rotulo="Resolução média", valor=f"{_num(periodo.get('avg_resolution_hours')) or 0:.0f} h"),
            Kpi(
                rotulo="Em aberto",
                valor=_int(sla.get("total_open")),
                detalhe=f"{_int(sla.get('backlog_breached'))} com SLA vencido",
            ),
        ],  # fmt: skip
        tabelas=[
            Tabela(
                titulo="Por prioridade",
                colunas=[
                    "Prioridade",
                    "Chamados",
                    "No prazo",
                    "Fora do prazo",
                    "Cumprimento",
                    "1ª resposta (h)",
                    "Resolução (h)",
                ],
                linhas=[
                    [
                        nome,
                        _int(p.get("count")),
                        _int(p.get("ok")),
                        _int(p.get("breached")),
                        _pct(p.get("compliance_pct")),
                        _int(p.get("first_reply_h")),
                        _int(p.get("resolution_h")),
                    ]
                    for chave, nome in prioridades.items()
                    if (p := (sla.get("by_priority") or {}).get(chave))
                ],
            ),  # fmt: skip
            Tabela(
                titulo="Idade do backlog",
                colunas=["Faixa", "Chamados"],
                linhas=[[faixa, _int(qtd)] for faixa, qtd in idades.items()],
            ),  # fmt: skip
            Tabela(
                titulo="Chamados abertos mais antigos",
                colunas=["#", "Assunto", "Prioridade", "Aberto em", "Idade", "SLA vencido"],
                linhas=[
                    [
                        str(o.get("id", "")),
                        o.get("subject", ""),
                        o.get("priority", ""),
                        o.get("created_fmt", ""),
                        o.get("age_str", ""),
                        "Sim" if o.get("breached") else "Não",
                    ]
                    for o in (sla.get("oldest") or [])[:15]
                ],
            ),  # fmt: skip
        ],
    )


def secao_volume() -> Secao:
    """Volume de chamados por mês, por responsável e por solicitante (Zendesk)."""
    from itgov.api.v1.zendesk import get_cached_historico

    hist = get_cached_historico() or {}
    return Secao(
        chave="volume",
        titulo="Volume de chamados",
        fonte=f"Zendesk — grupo {hist.get('grupo') or 'TI'}, desde {hist.get('desde') or 'o início'}",
        kpis=[
            Kpi(rotulo="Chamados no período", valor=_int(hist.get("total"))),
            Kpi(rotulo="Média mensal", valor=_int(hist.get("media_mensal"))),
        ],
        tabelas=[
            Tabela(
                titulo="Por mês",
                colunas=["Mês", "Abertos", "Resolvidos"],
                linhas=[
                    [m.get("rotulo", ""), _int(m.get("abertos")), _int(m.get("resolvidos"))]
                    for m in hist.get("meses") or []
                ],
            ),
            Tabela(
                titulo="Por responsável",
                colunas=["Responsável", "Total", "Em aberto", "Resolvidos", "Últimos 30 dias"],
                linhas=[
                    [
                        r.get("nome", ""),
                        _int(r.get("total")),
                        _int(r.get("em_aberto")),
                        _int(r.get("resolvidos")),
                        _int(r.get("ultimos_30d")),
                    ]
                    for r in hist.get("por_responsavel") or []
                ],
            ),
            Tabela(
                titulo="Por solicitante (quem mais abre)",
                colunas=["Solicitante", "Total", "Em aberto", "Resolvidos", "Últimos 30 dias"],
                linhas=[
                    [
                        r.get("nome", ""),
                        _int(r.get("total")),
                        _int(r.get("em_aberto")),
                        _int(r.get("resolvidos")),
                        _int(r.get("ultimos_30d")),
                    ]
                    for r in hist.get("por_solicitante") or []
                ],
            ),
        ],  # fmt: skip
    )


# ── Seções: segurança e conformidade ──────────────────────────────────────────


def secao_secure_score() -> Secao:
    """Microsoft Secure Score e as ações que mais aumentam a nota."""
    from itgov.api.v1.governance_compliance import get_cached_compliance_summary

    sc = get_cached_compliance_summary() or {}
    variacao = _num(sc.get("variacao_30d"))
    return Secao(
        chave="secure-score",
        titulo="Secure Score (Microsoft 365)",
        fonte="Microsoft Graph — Secure Score e controles recomendados",
        kpis=[
            Kpi(
                rotulo="Secure Score",
                valor=_pct(sc.get("pct")),
                tom=_tom_pct(sc.get("pct"), 70, 50),
                detalhe=f"{_int(sc.get('current_score'))} de {_int(sc.get('max_score'))} pontos",
            ),
            Kpi(
                rotulo="Variação em 30 dias",
                valor="—" if variacao is None else f"{variacao:+.1f}".replace(".", ",") + " p.p.",
                tom="neutro" if variacao is None else ("ok" if variacao >= 0 else "alerta"),
            ),
            Kpi(rotulo="Empresas parecidas", valor=_pct(sc.get("comparative_pct"))),
        ],  # fmt: skip
        tabelas=[
            Tabela(
                titulo="Por categoria",
                colunas=["Categoria", "Score"],
                linhas=[[c, _pct(v)] for c, v in (sc.get("category_breakdown") or {}).items()],
            ),
            Tabela(
                titulo="Próximas ações recomendadas",
                colunas=["Ação", "Categoria", "O que fazer", "Cumprido"],
                linhas=[
                    [
                        r.get("titulo") or r.get("control_name", ""),
                        r.get("categoria", ""),
                        r.get("descricao", ""),
                        _pct(r.get("score_pct")),
                    ]
                    for r in sc.get("recomendacoes") or []
                ],
            ),
        ],  # fmt: skip
    )


def secao_ameacas() -> Secao:
    """Alertas do Defender e proteção dos endpoints (Acronis)."""
    from itgov.api.v1.acronis_backup import get_cached_acronis_summary
    from itgov.api.v1.governance_security_alerts import get_cached_security_alerts_summary

    alertas = get_cached_security_alerts_summary() or {}
    ac = get_cached_acronis_summary() or {}
    return Secao(
        chave="ameacas",
        titulo="Ameaças e proteção de endpoints",
        fonte="Microsoft Defender (alertas em aberto) e Acronis Cyber Protect",
        kpis=[
            Kpi(
                rotulo="Alertas Defender em aberto",
                valor=_int(alertas.get("total_open")),
                tom=_tom_qtd(alertas.get("total_open")),
                detalhe=f"{_int(alertas.get('older_than_24h'))} há mais de 24 h",
            ),
            Kpi(
                rotulo="Endpoints protegidos",
                valor=_pct(ac.get("protected_pct")),
                tom=_tom_pct(ac.get("protected_pct"), 98, 90),
                detalhe=f"{_int(ac.get('protected'))} de {_int(ac.get('total_agents'))}",
            ),
            Kpi(
                rotulo="Incidentes não mitigados",
                valor=_int(ac.get("incidents_not_mitigated")),
                tom=_tom_qtd(ac.get("incidents_not_mitigated")),
            ),
            Kpi(
                rotulo="Offline há +30 dias",
                valor=_int(ac.get("offline_gt_30d_count")),
                tom=_tom_qtd(ac.get("offline_gt_30d_count")),
            ),
            Kpi(
                rotulo="Patches críticos pendentes",
                valor=_int(ac.get("patches_critical")),
                tom=_tom_qtd(ac.get("patches_critical")),
            ),
        ],  # fmt: skip
        tabelas=[
            Tabela(
                titulo="Alertas do Defender por severidade",
                colunas=["Severidade", "Em aberto"],
                linhas=[
                    ["Alta", _int(alertas.get("high"))],
                    ["Média", _int(alertas.get("medium"))],
                    ["Baixa", _int(alertas.get("low"))],
                ],
            ),
            Tabela(
                titulo="Incidentes recentes (Acronis)",
                colunas=["Equipamento", "Tipo", "Severidade", "Situação", "Quando"],
                linhas=[
                    [
                        i.get("resource_name", ""),
                        i.get("alert_type", ""),
                        i.get("severity", ""),
                        i.get("mitigation", ""),
                        _data_hora(i.get("time")),
                    ]
                    for i in ac.get("incidentes") or []
                ],
            ),
            Tabela(
                titulo="Equipamentos offline há mais de 30 dias",
                colunas=["Equipamento", "Dias offline", "Incidentes abertos"],
                linhas=[
                    [o.get("name", ""), _int(o.get("offline_days")), _int(o.get("open_incidents"))]
                    for o in ac.get("offline_gt30") or []
                ],
            ),
            Tabela(
                titulo="Equipamentos sem plano de proteção",
                colunas=["Equipamento"],
                linhas=[[s.get("name", "")] for s in ac.get("sem_plano") or []],
            ),
        ],  # fmt: skip
    )


def secao_identidade_email() -> Secao:
    """MFA dos usuários e proteção do domínio de e-mail (SPF, DKIM, DMARC)."""
    from itgov.api.v1.governance_mfa import _obter_dados as obter_mfa
    from itgov.services.dns_check_service import _get_domain, get_email_security_summary

    mfa = obter_mfa() or {}
    dominio = _get_domain()
    dns = get_email_security_summary(dominio) if dominio else {}

    def _registro(nome: str) -> list[str]:
        r = dns.get(nome) or {}
        detalhe = (
            (r.get("policy")
            and f"política {r['policy']}")
            or r.get("record")
            or (f"seletor {r['selector']}" if r.get("selector") else "")
        )
        return [nome.upper(), "Configurado" if r.get("found") else "Ausente", str(detalhe or "")]

    return Secao(
        chave="identidade-email",
        titulo="Identidade e e-mail",
        fonte="Microsoft Entra (MFA) e DNS do domínio de e-mail",
        kpis=[
            Kpi(
                rotulo="Usuários com MFA",
                valor=_pct(mfa.get("mfa_enabled_pct")),
                tom=_tom_pct(mfa.get("mfa_enabled_pct"), 95, 80),
            ),
            Kpi(rotulo="Domínio", valor=dominio or "não configurado"),
        ],  # fmt: skip
        tabelas=[
            Tabela(
                titulo="Proteção do domínio",
                colunas=["Registro", "Situação", "Valor"],
                linhas=[_registro(n) for n in ("spf", "dkim", "dmarc")] if dns else [],
            )
        ],  # fmt: skip
    )


# ── Seções: licenças e custos ─────────────────────────────────────────────────


def secao_licencas() -> Secao:
    """Licenças M365: uso, custo mensal, desperdício e renovações."""
    from itgov.api.v1.m365_licenses import get_licenses_summary

    dados = get_licenses_summary() or {}
    resumo = dados.get("summary") or {}
    pagas = [lic for lic in dados.get("licenses") or [] if not lic.get("is_free")]
    pagas.sort(key=lambda lic: -(_num(lic.get("custo_mensal_brl")) or 0))
    renovando = sorted((lic for lic in pagas if (_num(lic.get("renovacao_dias")) or 9999) <= 90),
                       key=lambda lic: _num(lic.get("renovacao_dias")) or 0)  # fmt: skip
    return Secao(
        chave="licencas",
        titulo="Licenças e custos Microsoft 365",
        fonte="Microsoft Graph (assinaturas) + custos informados no dashboard (sem custo informado = preço de lista)",
        kpis=[
            Kpi(rotulo="Custo mensal", valor=_brl(resumo.get("custo_mensal_total_brl"))),
            Kpi(
                rotulo="Desperdício mensal",
                valor=_brl(resumo.get("desperdicio_total_brl")),
                tom="ok" if not _num(resumo.get("desperdicio_total_brl")) else "alerta",
                detalhe="licenças pagas sem uso",
            ),
            Kpi(
                rotulo="Uso das licenças",
                valor=_pct(resumo.get("uso_pct")),
                detalhe=f"{_int(resumo.get('total_consumed'))} de {_int(resumo.get('total_seats'))}",
            ),
            Kpi(rotulo="Renovam em 90 dias", valor=str(len(renovando)), tom="alerta" if renovando else "ok"),
        ],  # fmt: skip
        tabelas=[
            Tabela(
                titulo="Licenças pagas",
                colunas=[
                    "Licença",
                    "Contratadas",
                    "Em uso",
                    "Livres",
                    "Custo unitário",
                    "Custo mensal",
                    "Desperdício",
                    "Renovação",
                ],
                linhas=[
                    [
                        lic.get("friendly_name") or lic.get("sku_name", ""),
                        _int(lic.get("total")),
                        _int(lic.get("consumed")),
                        _int(lic.get("available")),
                        _brl(lic.get("cost_per_unit_brl")),
                        _brl(lic.get("custo_mensal_brl")),
                        _brl(lic.get("desperdicio_brl")),
                        _data(lic.get("renewal_date")),
                    ]
                    for lic in pagas
                ],
            ),  # fmt: skip
            Tabela(
                titulo="Renovações nos próximos 90 dias",
                colunas=["Licença", "Renova em", "Dias", "Custo mensal"],
                linhas=[
                    [
                        lic.get("friendly_name") or lic.get("sku_name", ""),
                        _data(lic.get("renewal_date")),
                        _int(lic.get("renovacao_dias")),
                        _brl(lic.get("custo_mensal_brl")),
                    ]
                    for lic in renovando
                ],
            ),
        ],  # fmt: skip
    )


def secao_uso_apps() -> Secao:
    """Adoção dos apps e serviços do Microsoft 365 (últimos 30 dias e 6 meses)."""
    from itgov.services.m365_uso import obter_uso

    uso = obter_uso()
    if uso is None:
        raise RuntimeError("os relatórios de uso do Microsoft 365 não responderam agora")
    return Secao(
        chave="uso-apps",
        titulo="Uso dos apps Microsoft 365",
        fonte="Relatórios de uso do Microsoft 365 — últimos 30 dias",
        kpis=[
            Kpi(
                rotulo="Contas ativas",
                valor=_pct(uso.pct_ativas),
                tom=_tom_pct(uso.pct_ativas, 80, 60),
                detalhe=f"{uso.ativas} de {uso.contas} contas",
            )
        ],  # fmt: skip
        tabelas=[
            Tabela(
                titulo="Serviços",
                colunas=["Serviço", "Usuários", "Uso"],
                linhas=[[i.nome, _int(i.usuarios), _pct(i.pct)] for i in uso.servicos],
            ),
            Tabela(
                titulo="Apps",
                colunas=["App", "Usuários", "Uso"],
                linhas=[[i.nome, _int(i.usuarios), _pct(i.pct)] for i in uso.apps],
            ),
            Tabela(
                titulo="Média diária de usuários por mês",
                colunas=["Mês", *uso.colunas_historico],
                linhas=[[m.rotulo, *(_int(m.medias.get(c)) for c in uso.colunas_historico)] for m in uso.meses],
            ),
        ],  # fmt: skip
    )


def _data(valor: Any) -> str:
    if not valor:
        return "—"
    try:
        return date.fromisoformat(str(valor)[:10]).strftime("%d/%m/%Y")
    except ValueError:
        return str(valor)


def _data_hora(valor: Any) -> str:
    if isinstance(valor, datetime):
        return valor.astimezone(TZ).strftime("%d/%m/%Y %H:%M")
    return str(valor or "—")


# ── Catálogo ──────────────────────────────────────────────────────────────────

SECOES: dict[str, tuple[str, Callable[[], Secao]]] = {
    "disponibilidade": ("Disponibilidade e incidentes", secao_disponibilidade),
    "links": ("Links WAN e túneis", secao_links),
    "cftv": ("CFTV", secao_cftv),
    "sla": ("SLA de atendimento", secao_sla),
    "volume": ("Volume de chamados", secao_volume),
    "secure-score": ("Secure Score (Microsoft 365)", secao_secure_score),
    "ameacas": ("Ameaças e proteção de endpoints", secao_ameacas),
    "identidade-email": ("Identidade e e-mail", secao_identidade_email),
    "licencas": ("Licenças e custos Microsoft 365", secao_licencas),
    "uso-apps": ("Uso dos apps Microsoft 365", secao_uso_apps),
}

CATALOGO: dict[str, tuple[str, str, list[str]]] = {
    "infraestrutura": (
        "Infraestrutura e disponibilidade",
        "Disponibilidade dos hosts, incidentes em aberto, links WAN, túneis e CFTV.",
        ["disponibilidade", "links", "cftv"],
    ),
    "service-desk": (
        "Service desk e SLA",
        "Cumprimento de SLA, backlog, chamados antigos e volume por responsável e solicitante.",
        ["sla", "volume"],
    ),
    "seguranca": (
        "Segurança e conformidade",
        "Secure Score, alertas do Defender, proteção de endpoints, MFA e e-mail.",
        ["secure-score", "ameacas", "identidade-email"],
    ),
    "licencas": (
        "Licenças e custos M365",
        "Custo mensal, desperdício, renovações e adoção dos apps do Microsoft 365.",
        ["licencas", "uso-apps"],
    ),
}

# O que uma seção pode lançar quando a origem está fora ou mudou de formato
_FALHAS = (RuntimeError, ValueError, KeyError, TypeError, AttributeError, httpx.HTTPError, requests.RequestException)


def montar_secao(chave: str) -> Secao:
    """Monta uma seção; se a origem falhar, a seção vem com o motivo em ``erro``.

    Args:
        chave: Chave em ``SECOES``.

    Returns:
        A seção preenchida ou com ``erro``.
    """
    titulo, construir = SECOES[chave]
    try:
        return construir()
    except _FALHAS as exc:
        log.warning("relatorios.secao_falhou", secao=chave, erro=type(exc).__name__, detalhe=str(exc)[:200])
        return Secao(chave=chave, titulo=titulo, fonte="", erro=f"Dados indisponíveis agora ({exc})")


def montar_relatorio(chave: str, secoes: list[str] | None = None) -> Relatorio:
    """Monta um relatório do catálogo ou o personalizado.

    Args:
        chave: Chave em ``CATALOGO`` ou ``"personalizado"``.
        secoes: Seções escolhidas (só para o personalizado; ignora desconhecidas).

    Returns:
        O relatório com as seções na ordem do catálogo.

    Raises:
        KeyError: Relatório desconhecido.
    """
    if chave == "personalizado":
        escolhidas = [s for s in SECOES if s in set(secoes or [])]
        titulo, descricao = (
            "Relatório personalizado",
            "Seções escolhidas: " + ", ".join(SECOES[s][0] for s in escolhidas),
        )
    else:
        titulo, descricao, escolhidas = CATALOGO[chave]
    return Relatorio(
        chave=chave,
        titulo=titulo,
        descricao=descricao,
        gerado_em=datetime.now(TZ),
        secoes=[montar_secao(s) for s in escolhidas],
    )


def gerar_csv(relatorio: Relatorio) -> str:
    """CSV (separador ``;``, para abrir direto no Excel em português) com indicadores e tabelas.

    Args:
        relatorio: Relatório montado.

    Returns:
        Texto CSV com um bloco por seção.
    """
    saida = io.StringIO()
    w = csv.writer(saida, delimiter=";")
    w.writerow([relatorio.titulo, relatorio.gerado_em.strftime("%d/%m/%Y %H:%M")])
    for s in relatorio.secoes:
        w.writerow([])
        w.writerow([s.titulo, s.fonte])
        if s.erro:
            w.writerow([s.erro])
            continue
        for k in s.kpis:
            w.writerow([k.rotulo, k.valor, k.detalhe])
        for t in s.tabelas:
            w.writerow([])
            w.writerow([f"{s.titulo} — {t.titulo}"])
            w.writerow(t.colunas)
            w.writerows(t.linhas)
    return saida.getvalue()
