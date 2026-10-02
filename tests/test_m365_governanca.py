"""Painel /gov/m365 com dados reais: planos, pilares e checklist."""

from __future__ import annotations

from unittest.mock import patch

from app.services import m365_governanca as mg
from app.services.m365_governanca import montar_painel


def _url(endpoint: str) -> str:
    return "/" + endpoint


LICENCAS = [
    {"sku_name": "O365_BUSINESS_ESSENTIALS", "consumed": 268},
    {"sku_name": "O365_BUSINESS", "consumed": 79},
    {"sku_name": "O365_BUSINESS_PREMIUM", "consumed": 32},
    {"sku_name": "SPB", "consumed": 1},
    {"sku_name": "THREAT_INTELLIGENCE", "consumed": 1},
    {"sku_name": "FLOW_FREE", "consumed": 114},
]

ENTRA = {
    "total_users": 518,
    "guest_users": 99,
    "mfa_enabled_pct": 61.78,
    "ca_total": 6,
    "ca_enabled": 0,
    "ca_report_only": 4,
    "ca_disabled": 2,
    "admin_total": 7,
    "admin_sem_mfa": 0,
    "stale_accounts_90d": 50,
}

CONTROLES = {
    "AdminMFAV2": {"on": True},
    "BlockLegacyAuthentication": {"on": True},
    "SigninRiskPolicy": {"on": True},
    "mdo_safelinksforemail": {"on": True},
    "mdo_safeattachments": {"on": True},
    "mdo_antiphishingpolicies": {"on": False},
    "mdo_commonattachmentsfilter": {"on": False},
    "mip_search_auditlog": {"on": True},
    "exo_mailboxaudit": {"on": False},
    "CustomerLockBoxEnabled": {"on": False},
    "mip_sensitivitylabelspolicies": {"on": False},
    "dlp_datalossprevention": {"on": True},
    "mip_autosensitivitylabelspolicies": {"on": False},
}

DNS = {"spf": {"found": True}, "dmarc": {"found": True, "policy": None}, "dkim": {"found": True}}


def _painel(**kw: object) -> mg.PainelM365:
    args: dict = {
        "licencas": LICENCAS,
        "entra": ENTRA,
        "controles": CONTROLES,
        "total_labels": 1,
        "dispositivos": {"total_devices": 501, "stale_45d": 212, "managed_pct": None},
        "dns": DNS,
        "url_for": _url,
    }
    args.update(kw)
    return montar_painel(**args)


def _pilar(painel: mg.PainelM365, titulo: str) -> mg.Pilar:
    return next(p for p in painel.pilares if p.titulo == titulo)


def _item(pilar: mg.Pilar, texto: str) -> mg.Item:
    return next(i for i in pilar.itens if i.texto == texto)


def test_banner_mostra_os_planos_reais_e_nao_business_premium_para_todos() -> None:
    painel = _painel()
    assert [(p.nome, p.atribuidas) for p in painel.planos] == [
        ("Business Basic", 268),
        ("Apps for Business", 79),
        ("Business Standard", 32),
        ("Business Premium", 1),
    ]
    assert painel.usuarios_internos == 518 - 99


def test_acesso_condicional_em_modo_relatorio_nao_conta_como_aplicado() -> None:
    ca = _item(_pilar(_painel(), "Identidade"), "Acesso Condicional aplicado")
    assert ca.estado == "pendente"
    assert "0 de 6" in ca.detalhe and "4 só em modo relatório" in ca.detalhe


def test_cobertura_de_licenca_conta_so_quem_tem_o_recurso() -> None:
    painel = _painel()
    endpoint = _item(_pilar(painel, "Endpoint"), "Licença Defender for Business atribuída")
    assert endpoint.estado == "pendente"
    assert endpoint.detalhe.startswith("1 de 419")
    # Defender for Office: SPB + THREAT_INTELLIGENCE
    office = _item(_pilar(painel, "E-mail"), "Licença Defender for Office 365 atribuída")
    assert office.detalhe.startswith("2 de 419")


def test_rotulos_vem_do_purview_e_nao_de_texto_fixo() -> None:
    labels = _item(_pilar(_painel(total_labels=1), "Dados"), "Rótulos de sensibilidade publicados")
    assert labels.estado == "pendente" and labels.detalhe.startswith("1 rótulo")
    labels = _item(_pilar(_painel(total_labels=3), "Dados"), "Rótulos de sensibilidade publicados")
    assert labels.estado == "ok"


def test_dmarc_sem_politica_fica_pendente() -> None:
    dmarc = _item(_pilar(_painel(), "E-mail"), "DMARC com política de bloqueio")
    assert dmarc.estado == "pendente" and "sem política" in dmarc.detalhe
    dns_ok = {**DNS, "dmarc": {"found": True, "policy": "reject"}}
    assert _item(_pilar(_painel(dns=dns_ok), "E-mail"), "DMARC com política de bloqueio").estado == "ok"


def test_status_do_pilar_segue_as_verificacoes() -> None:
    painel = _painel()
    assert _pilar(painel, "Auditoria").status == "parcial"  # 1 de 3
    assert _pilar(painel, "Endpoint").status == "pendente"  # licença pendente, onboarding sem dado
    sem_nada = _painel(controles={})
    assert _pilar(sem_nada, "Auditoria").status == "sem_dados"


def test_controle_ausente_vira_sem_dado_e_nao_conta_no_status() -> None:
    painel = _painel(controles={"mip_search_auditlog": {"on": True}})
    auditoria = _pilar(painel, "Auditoria")
    assert auditoria.status == "ativo"
    assert auditoria.resumo == "1 de 1 verificações ok"


def test_checklist_e_calculado_dos_dados() -> None:
    estados = {t.tarefa: t.estado for t in _painel().checklist}
    assert estados["MFA para todos os usuários"] == "pendente"
    assert estados["Admins com MFA"] == "ok"
    assert estados["Safe Links no e-mail"] == "ok"
    assert estados["Inscrever dispositivos no Intune"] == "pendente"
    assert estados["Desativar contas inativas (90+ dias)"] == "pendente"


def test_sem_fontes_tudo_vira_sem_dado() -> None:
    painel = montar_painel([], {}, {}, None, None, None, _url)
    assert painel.planos == []
    assert all(t.estado in ("sem_dado", "pendente") for t in painel.checklist)
    assert _pilar(painel, "Dados").itens[0].estado == "sem_dado"


def test_fonte_fora_do_ar_nao_derruba_o_painel() -> None:
    def quebra() -> dict:
        raise RuntimeError("graph fora")

    with (
        patch.object(mg, "_controles_secure_score", quebra),
        patch("itgov.api.v1.governance_data.get_cached_data_summary", quebra),
        patch("itgov.api.v1.governance_devices.get_cached_device_summary", quebra),
    ):
        painel = mg.obter_painel(LICENCAS, ENTRA, DNS, _url)
    assert len(painel.pilares) == 6
    assert _pilar(painel, "Auditoria").status == "sem_dados"


def test_pagina_m365_renderiza_pilares_e_checklist_sem_x_data_inline(authed_client) -> None:
    painel = _painel()
    with (
        patch("app.services.m365_governanca.obter_painel", return_value=painel),
        patch(
            "itgov.api.v1.m365_licenses.get_licenses_summary",
            return_value={"has_data": False, "summary": {"uso_pct": 0}, "licenses": []},
        ),
        patch("itgov.api.v1.zabbix_triggers.get_cached_triggers", return_value={}),
        patch("itgov.api.v1.governance_security_alerts.get_cached_security_alerts_summary", return_value={}),
        patch("app.services.influxdb_provider.InfluxDBMetricsProvider._query", return_value=[]),
    ):
        resp = authed_client.get("/gov/m365")
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "Plano ativo: Microsoft 365 Business Premium" not in html
    assert "Business Basic: <strong" in html
    assert 'x-data="{' not in html  # Alpine CSP: só componentes registrados
    assert 'x-data="pilarCard"' in html and 'x-data="checklistM365"' in html
    assert "Aplicar Acesso Condicional" in html
