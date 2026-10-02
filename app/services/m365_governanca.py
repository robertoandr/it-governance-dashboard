"""Painel /gov/m365 com dados reais: planos, 6 pilares e checklist.

Antes o painel afirmava "Business Premium" para o tenant todo e os pilares e o
checklist eram texto fixo. Aqui tudo sai do que já é coletado: licenças
atribuídas (Graph subscribedSkus), resumo do Entra, controles do Secure Score,
rótulos do Purview, inventário de dispositivos e DNS do domínio.

Um recurso ligado no tenant não protege quem não tem licença dele — por isso
cada pilar mostra quantas licenças cobrem o recurso, além do estado da config.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

import structlog
from pydantic import BaseModel

log = structlog.get_logger(__name__)

Estado = Literal["ok", "pendente", "sem_dado"]
StatusPilar = Literal["ativo", "parcial", "pendente", "sem_dados"]

# Planos-base (o "plano" de cada usuário), na ordem em que aparecem no banner.
PLANOS_BASE: dict[str, str] = {
    "SPB": "Business Premium",
    "O365_BUSINESS_PREMIUM": "Business Standard",
    "O365_BUSINESS_ESSENTIALS": "Business Basic",
    "O365_BUSINESS": "Apps for Business",
    "EXCHANGEENTERPRISE": "Exchange Online P2",
    "SPE_E3": "Microsoft 365 E3",
    "SPE_E5": "Microsoft 365 E5",
}

# Licenças que dão cada recurso ao usuário (Business Premium e equivalentes).
COBERTURA: dict[str, frozenset[str]] = {
    "entra_p1": frozenset({"SPB", "AAD_PREMIUM", "AAD_PREMIUM_P2", "EMS", "EMSPREMIUM", "SPE_E3", "SPE_E5"}),
    "intune": frozenset({"SPB", "INTUNE_A", "EMS", "EMSPREMIUM", "SPE_E3", "SPE_E5"}),
    "defender_endpoint": frozenset({"SPB", "MDE_SMB", "DEFENDER_ENDPOINT_P1", "WIN_DEF_ATP", "SPE_E5"}),
    "defender_office": frozenset({"SPB", "ATP_ENTERPRISE", "THREAT_INTELLIGENCE", "SPE_E5"}),
    "purview_labels": frozenset({"SPB", "AIP_PREMIUM_P1", "EMS", "EMSPREMIUM", "SPE_E3", "SPE_E5"}),
}


class Item(BaseModel):
    """Uma verificação de um pilar (ou do checklist) com seu estado real."""

    texto: str
    estado: Estado
    detalhe: str = ""


class Pilar(BaseModel):
    """Um dos 6 pilares de segurança do M365."""

    num: str
    titulo: str
    produto: str
    cor: str
    icon: str
    status: StatusPilar
    resumo: str
    itens: list[Item]
    link: str


class Tarefa(BaseModel):
    """Linha do checklist de implementação."""

    prioridade: Literal["ALTA", "MEDIA", "BAIXA"]
    tarefa: str
    onde: str
    estado: Estado
    detalhe: str = ""


class Plano(BaseModel):
    """Plano-base com quantidade atribuída."""

    nome: str
    atribuidas: int


class PainelM365(BaseModel):
    """Tudo que o template precisa além dos KPIs simples."""

    planos: list[Plano]
    usuarios_internos: int
    pilares: list[Pilar]
    checklist: list[Tarefa]


def _controle(controles: dict[str, dict], nome: str, texto: str) -> Item:
    """Item a partir de um controle do Secure Score (on/off)."""
    c = controles.get(nome)
    if c is None:
        return Item(texto=texto, estado="sem_dado", detalhe="Controle não aparece no Secure Score")
    return Item(texto=texto, estado="ok" if c.get("on") else "pendente")


def _licencas(licencas: list[dict], recurso: str) -> int:
    """Quantas licenças atribuídas dão o recurso."""
    skus = COBERTURA[recurso]
    return sum(int(s.get("consumed") or 0) for s in licencas if s.get("sku_name") in skus)


def _item_cobertura(licencas: list[dict], recurso: str, produto: str, usuarios: int) -> Item:
    """Item 'quantos usuários têm licença do recurso' (ok a partir de 90%)."""
    n = _licencas(licencas, recurso)
    pct = (n / usuarios * 100) if usuarios else 0.0
    return Item(
        texto=f"Licença {produto} atribuída",
        estado="ok" if usuarios and pct >= 90 else "pendente",
        detalhe=f"{n} de {usuarios} usuários ({pct:.0f}%)".replace(".", ","),
    )


def _status(itens: list[Item]) -> StatusPilar:
    """Ativo = tudo ok; parcial = algo ok; pendente = nada ok; sem_dados = nada medido."""
    medidos = [i for i in itens if i.estado != "sem_dado"]
    if not medidos:
        return "sem_dados"
    ok = sum(1 for i in medidos if i.estado == "ok")
    if ok == len(medidos):
        return "ativo"
    return "parcial" if ok else "pendente"


def _pct(v: object) -> str:
    return f"{float(v):.1f}%".replace(".", ",")  # type: ignore[arg-type]


def montar_painel(
    licencas: list[dict],
    entra: dict,
    controles: dict[str, dict],
    total_labels: int | None,
    dispositivos: dict | None,
    dns: dict | None,
    url_for: Callable[[str], str],
) -> PainelM365:
    """Monta planos, pilares e checklist (função pura — fontes vêm de fora)."""
    usuarios = max(int(entra.get("total_users") or 0) - int(entra.get("guest_users") or 0), 0)
    mfa = entra.get("mfa_enabled_pct")
    ca_total = entra.get("ca_total")
    ca_on = int(entra.get("ca_enabled") or 0)
    ca_rel = int(entra.get("ca_report_only") or 0)

    por_sku = {s.get("sku_name"): int(s.get("consumed") or 0) for s in licencas}
    planos = [Plano(nome=nome, atribuidas=por_sku[sku]) for sku, nome in PLANOS_BASE.items() if por_sku.get(sku)]
    planos.sort(key=lambda p: p.atribuidas, reverse=True)

    # ── 4.1 Identidade ───────────────────────────────────────────────
    mfa_item = (
        Item(texto="MFA dos usuários", estado="ok" if float(mfa) >= 90 else "pendente", detalhe=f"{_pct(mfa)} com MFA")
        if mfa is not None
        else Item(texto="MFA dos usuários", estado="sem_dado")
    )
    ca_item = (
        Item(
            texto="Acesso Condicional aplicado",
            estado="ok" if ca_on else "pendente",
            detalhe=f"{ca_on} de {ca_total} políticas aplicadas · {ca_rel} só em modo relatório",
        )
        if ca_total is not None
        else Item(texto="Acesso Condicional aplicado", estado="sem_dado")
    )
    identidade = [
        mfa_item,
        _controle(controles, "AdminMFAV2", "MFA obrigatório para administradores"),
        ca_item,
        _controle(controles, "BlockLegacyAuthentication", "Autenticação legada bloqueada"),
        _controle(controles, "SigninRiskPolicy", "Política de risco de entrada"),
        _item_cobertura(licencas, "entra_p1", "Entra ID P1", usuarios),
    ]

    # ── 4.2 Endpoint ─────────────────────────────────────────────────
    endpoint = [
        _item_cobertura(licencas, "defender_endpoint", "Defender for Business", usuarios),
        Item(
            texto="Dispositivos integrados ao Defender",
            estado="sem_dado",
            detalhe="Não coletado — conferir no portal do Defender",
        ),
    ]

    # ── 4.3 Dispositivos ─────────────────────────────────────────────
    if dispositivos:
        total_disp = int(dispositivos.get("total_devices") or 0)
        gerenciados_pct = dispositivos.get("managed_pct")
        inativos = int(dispositivos.get("stale_45d") or 0)
        intune_item = Item(
            texto="Dispositivos gerenciados pelo Intune",
            estado="ok" if gerenciados_pct is not None and float(gerenciados_pct) >= 80 else "pendente",
            detalhe=(
                f"{_pct(gerenciados_pct)} gerenciados"
                if gerenciados_pct is not None
                else f"Nenhum dos {total_disp} dispositivos registrados está no Intune"
            ),
        )
        inativos_item = Item(
            texto="Dispositivos inativos limpos",
            estado="ok" if total_disp and inativos / total_disp <= 0.1 else "pendente",
            detalhe=f"{inativos} de {total_disp} sem uso há 45+ dias",
        )
    else:
        intune_item = Item(texto="Dispositivos gerenciados pelo Intune", estado="sem_dado")
        inativos_item = Item(texto="Dispositivos inativos limpos", estado="sem_dado")
    dispositivos_itens = [intune_item, inativos_item, _item_cobertura(licencas, "intune", "Intune", usuarios)]

    # ── 4.4 E-mail ───────────────────────────────────────────────────
    if dns:
        dmarc = dns.get("dmarc") or {}
        politica = (dmarc.get("policy") or "").lower()
        dmarc_item = Item(
            texto="DMARC com política de bloqueio",
            estado="ok" if politica in ("quarantine", "reject") else "pendente",
            detalhe=(
                f"p={politica}"
                if politica
                else ("Registro sem política (p=)" if dmarc.get("found") else "Registro DMARC ausente")
            ),
        )
        spf_item = Item(texto="SPF publicado", estado="ok" if (dns.get("spf") or {}).get("found") else "pendente")
    else:
        dmarc_item = Item(texto="DMARC com política de bloqueio", estado="sem_dado", detalhe="Domínio não configurado")
        spf_item = Item(texto="SPF publicado", estado="sem_dado", detalhe="Domínio não configurado")
    email = [
        _controle(controles, "mdo_safelinksforemail", "Safe Links no e-mail"),
        _controle(controles, "mdo_safeattachments", "Safe Attachments"),
        _controle(controles, "mdo_antiphishingpolicies", "Política anti-phishing"),
        _controle(controles, "mdo_commonattachmentsfilter", "Filtro de anexos perigosos"),
        spf_item,
        dmarc_item,
        _item_cobertura(licencas, "defender_office", "Defender for Office 365", usuarios),
    ]

    # ── 4.5 Dados ────────────────────────────────────────────────────
    labels_item = (
        Item(
            texto="Rótulos de sensibilidade publicados",
            estado="ok" if total_labels >= 3 else "pendente",
            detalhe=f"{total_labels} rótulo(s) — o mínimo usual é Público / Interno / Confidencial",
        )
        if total_labels is not None
        else Item(texto="Rótulos de sensibilidade publicados", estado="sem_dado")
    )
    dados = [
        labels_item,
        _controle(controles, "mip_sensitivitylabelspolicies", "Política de rótulos para os usuários"),
        _controle(controles, "dlp_datalossprevention", "Prevenção de perda de dados (DLP)"),
        _controle(controles, "mip_autosensitivitylabelspolicies", "Rotulagem automática"),
        _item_cobertura(licencas, "purview_labels", "Purview (rótulos)", usuarios),
    ]

    # ── 4.6 Auditoria ────────────────────────────────────────────────
    auditoria = [
        _controle(controles, "mip_search_auditlog", "Log de auditoria unificado"),
        _controle(controles, "exo_mailboxaudit", "Auditoria das caixas de correio"),
        _controle(controles, "CustomerLockBoxEnabled", "Customer Lockbox"),
    ]

    def _pilar(num: str, titulo: str, produto: str, cor: str, icon: str, itens: list[Item], link: str) -> Pilar:
        medidos = [i for i in itens if i.estado != "sem_dado"]
        ok = sum(1 for i in medidos if i.estado == "ok")
        resumo = f"{ok} de {len(medidos)} verificações ok" if medidos else "Sem dados coletados"
        return Pilar(
            num=num, titulo=titulo, produto=produto, cor=cor, icon=icon,
            status=_status(itens), resumo=resumo, itens=itens, link=link,
        )  # fmt: skip

    pilares = [
        _pilar("4.1", "Identidade", "Entra ID", "blue",
               "M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z",
               identidade, url_for("dashboards.governance_compliance")),
        _pilar("4.2", "Endpoint", "Defender for Business", "red",
               "M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z",
               endpoint, "https://security.microsoft.com"),
        _pilar("4.3", "Dispositivos", "Intune", "green",
               "M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z",
               dispositivos_itens, url_for("dashboards.governance_devices")),
        _pilar("4.4", "E-mail", "Exchange Online Protection + Defender for Office 365", "yellow",
               "M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z",
               email, "https://security.microsoft.com"),
        _pilar("4.5", "Dados", "Purview", "purple",
               "M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4",
               dados, url_for("dashboards.governance_data")),
        _pilar("4.6", "Auditoria", "Purview Audit", "slate",
               "M9 17v-2m3 2v-4m3 4v-6m2 10H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z",
               auditoria, "https://purview.microsoft.com"),
    ]  # fmt: skip

    # ── Checklist: cada tarefa reaproveita o item medido ─────────────
    def _tarefa(pri: Literal["ALTA", "MEDIA", "BAIXA"], tarefa: str, onde: str, item: Item) -> Tarefa:
        return Tarefa(prioridade=pri, tarefa=tarefa, onde=onde, estado=item.estado, detalhe=item.detalhe)

    stale = entra.get("stale_accounts_90d")
    stale_item = (
        Item(
            texto="",
            estado="ok" if int(stale) == 0 else "pendente",
            detalhe=f"{int(stale)} conta(s) sem login há 90+ dias",
        )
        if stale is not None
        else Item(texto="", estado="sem_dado")
    )
    admin_sem_mfa = entra.get("admin_sem_mfa")
    admin_item = (
        Item(
            texto="",
            estado="ok" if int(admin_sem_mfa) == 0 else "pendente",
            detalhe=f"{int(admin_sem_mfa)} de {int(entra.get('admin_total') or 0)} admins sem MFA",
        )
        if admin_sem_mfa is not None
        else Item(texto="", estado="sem_dado")
    )
    checklist = [
        _tarefa("ALTA", "MFA para todos os usuários", "Entra ID › Métodos de autenticação", mfa_item),
        _tarefa("ALTA", "Aplicar Acesso Condicional (sair do modo relatório)", "Entra ID › Proteção › Acesso condicional", ca_item),
        _tarefa("ALTA", "Admins com MFA", "Entra ID › Funções e administradores", admin_item),
        _tarefa("ALTA", "Licenciar Defender for Business nos usuários", "Admin Center › Cobrança › Licenças", endpoint[0]),
        _tarefa("ALTA", "Inscrever dispositivos no Intune", "Intune › Dispositivos › Inscrição", intune_item),
        _tarefa("ALTA", "Safe Links no e-mail", "Defender › Políticas › Safe Links", email[0]),
        _tarefa("ALTA", "DMARC com p=quarantine ou p=reject", "DNS do domínio", dmarc_item),
        _tarefa("MEDIA", "Publicar rótulos de sensibilidade", "Purview › Proteção de informações › Rótulos", labels_item),
        _tarefa("MEDIA", "Log de auditoria unificado", "Purview › Auditoria", auditoria[0]),
        _tarefa("MEDIA", "Desativar contas inativas (90+ dias)", "Entra ID › Usuários › último login", stale_item),
        _tarefa("BAIXA", "Limpar dispositivos inativos", "Entra ID › Dispositivos", inativos_item),
        _tarefa("BAIXA", "Rotulagem automática", "Purview › Rotulagem automática", dados[3]),
    ]  # fmt: skip

    return PainelM365(planos=planos, usuarios_internos=usuarios, pilares=pilares, checklist=checklist)


def _controles_secure_score() -> dict[str, dict]:
    """Último estado (on/off) de cada controle do Secure Score, do InfluxDB."""
    from app.services.influxdb_provider import InfluxDBMetricsProvider

    p = InfluxDBMetricsProvider()
    rows = p._query(f"""
from(bucket: "{p._bucket_raw}")
  |> range(start: -2d)
  |> filter(fn: (r) => r._measurement == "m365_secure_score_controls")
  |> last()
  |> keep(columns: ["control_name", "_field", "_value"])
""")
    out: dict[str, dict] = {}
    for r in rows:
        if r.get("control_name"):
            out.setdefault(r["control_name"], {})[r["_field"]] = r["_value"]
    return out


def _seguro(nome: str, fn: Callable[[], object], padrao: object) -> object:
    """Fonte fora do ar vira 'sem dado' em vez de derrubar a página."""
    try:
        return fn()
    except Exception as exc:  # qualquer falha de fonte externa vira "sem dado"
        log.warning("m365_governanca.fonte_falhou", fonte=nome, erro=str(exc))
        return padrao


def obter_painel(licencas: list[dict], entra: dict, dns: dict | None, url_for: Callable[[str], str]) -> PainelM365:
    """Busca controles, rótulos e dispositivos (com cache das próprias fontes) e monta o painel."""
    from itgov.api.v1.governance_data import get_cached_data_summary
    from itgov.api.v1.governance_devices import get_cached_device_summary

    controles = _seguro("secure_score_controls", _controles_secure_score, {})
    dados = _seguro("purview_labels", get_cached_data_summary, None)
    dispositivos = _seguro("dispositivos", get_cached_device_summary, None)
    total_labels = dados.get("total_labels") if isinstance(dados, dict) else None
    return montar_painel(
        licencas=licencas,
        entra=entra,
        controles=controles,  # type: ignore[arg-type]
        total_labels=total_labels,
        dispositivos=dispositivos if isinstance(dispositivos, dict) else None,
        dns=dns or None,
        url_for=url_for,
    )
