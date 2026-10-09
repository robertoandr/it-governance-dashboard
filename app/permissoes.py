"""Permissões por usuário: o que cada pessoa vê e altera, página a página.

Cada página do menu tem três níveis: ``nenhum`` (não vê), ``ver`` (só vê) e
``alterar``. Sem ajuste, vale o perfil (visualizador, operador, gestor ou
admin) — exatamente as regras que as rotas já tinham em ``require_role``. O
ajuste fica em ``User.permissoes`` (só as páginas que fogem do perfil) e vence
o perfil nas duas direções: libera o que o perfil não dava e tira o que dava.

O super admin sempre vê e altera tudo. Usuários e Aprovações não entram aqui:
continuam presos ao perfil admin.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

NENHUM = "nenhum"
VER = "ver"
ALTERAR = "alterar"
NIVEIS = (NENHUM, VER, ALTERAR)
_ORDEM = {NENHUM: 0, VER: 1, ALTERAR: 2}

ROTULO_NIVEL = {NENHUM: "Não vê", VER: "Só vê", ALTERAR: "Altera"}

_AGV = ("admin", "gestor", "visualizador")
_AG = ("admin", "gestor")
_AGO = ("admin", "gestor", "operador")


@dataclass(frozen=True)
class Pagina:
    """Uma página do menu e o que cada perfil pode nela por padrão.

    Attributes:
        chave: Identificador guardado em ``User.permissoes``.
        endpoint: Rota Flask da página.
        nome: Como aparece no menu.
        grupo: Grupo do menu.
        ver: Perfis que veem a página sem ajuste.
        alterar: Perfis que alteram sem ajuste (vazio: página só de leitura).
    """

    chave: str
    endpoint: str
    nome: str
    grupo: str
    ver: tuple[str, ...]
    alterar: tuple[str, ...] = ()

    @property
    def alteravel(self) -> bool:
        """A página tem alguma ação de alteração?"""
        return bool(self.alterar)

    def padrao(self, role: str) -> str:
        """Nível que o perfil tem na página sem ajuste.

        Args:
            role: Perfil do usuário.

        Returns:
            ``nenhum``, ``ver`` ou ``alterar``.
        """
        if role in self.alterar:
            return ALTERAR
        return VER if role in self.ver else NENHUM


PAGINAS: tuple[Pagina, ...] = (
    Pagina("visao_geral", "dashboards.overview", "Visão Geral", "Governança", _AGV),
    Pagina("pilares", "dashboards.pillars", "Pilares COBIT", "Governança", _AGV),
    Pagina("m365", "dashboards.m365_overview", "Governança M365", "Microsoft 365", _AG),
    Pagina("licencas", "dashboards.m365_licenses", "Licenças", "Microsoft 365", _AG, _AG),
    Pagina("dispositivos", "dashboards.governance_devices", "Dispositivos", "Microsoft 365", _AG),
    Pagina("aplicativos", "dashboards.governance_apps", "Aplicativos", "Microsoft 365", _AG),
    Pagina("compliance", "dashboards.governance_compliance", "Compliance", "Microsoft 365", _AG),
    Pagina("dados", "dashboards.governance_data", "Dados / Rótulos", "Microsoft 365", _AG),
    Pagina("alertas_defender", "dashboards.governance_security_alerts", "Alertas Defender", "Microsoft 365", _AG),
    Pagina("zendesk", "dashboards.zendesk_mttr", "Zendesk", "Suporte", _AG),
    Pagina("sla", "dashboards.sla_chamados", "SLA / Chamados", "Suporte", _AG),
    Pagina("zabbix", "dashboards.zabbix_monitoring", "Zabbix", "Monitoramento", _AGO),
    Pagina("infra", "dashboards.infra_monitoring", "Infraestrutura", "Monitoramento", _AGO),
    # Renomear, SNMP e duplicados são só admin; vincular gravador à unidade, gestor também
    Pagina("cftv", "dashboards.cftv_monitoring", "CFTV", "Monitoramento", _AGO, ("admin",)),
    Pagina("rede", "dashboards.rede_monitoring", "Rede", "Monitoramento", _AGO, _AG),
    Pagina("ativos_rede", "dashboards.ativos_rede", "Ativos de rede", "Monitoramento", _AGO, _AG),
    Pagina("triggers", "dashboards.zabbix_triggers", "Triggers / Alertas", "Monitoramento", _AGO, _AGO),
    Pagina("links", "dashboards.links_manager", "Links WAN", "Monitoramento", _AGO, _AG),
    Pagina("unidades", "dashboards.unidades_list", "Unidades", "Monitoramento", _AGO, _AG),
    Pagina("mapa_cameras", "dashboards.mapa_cameras", "Mapa de Câmeras", "Segurança", _AG),
    Pagina("ciberseguranca", "dashboards.acronis_backup", "Cibersegurança", "Segurança", _AG),
    Pagina("pmo", "dashboards.pmo_dashboard", "PMO", "Gestão", _AGV, _AG),
    Pagina("relatorios", "dashboards.relatorios", "Relatórios", "Gestão", _AG),
)

POR_CHAVE: dict[str, Pagina] = {p.chave: p for p in PAGINAS}


def grupos() -> list[tuple[str, list[Pagina]]]:
    """Páginas agrupadas na ordem do menu."""
    saida: list[tuple[str, list[Pagina]]] = []
    for pagina in PAGINAS:
        if not saida or saida[-1][0] != pagina.grupo:
            saida.append((pagina.grupo, []))
        saida[-1][1].append(pagina)
    return saida


# Tarefas não entra no catálogo: todo perfil vê (regras próprias em services/tarefas)
_SEMPRE = "tarefas.workspaces"


def primeira_pagina(user: Any) -> str:
    """Endpoint da primeira página do menu que o usuário pode ver.

    Args:
        user: Usuário autenticado.

    Returns:
        Endpoint Flask (Tarefas, se não vê nenhuma página do catálogo).
    """
    for pagina in PAGINAS:
        if user.pode(pagina.chave):
            return pagina.endpoint
    return _SEMPRE


def basta(tem: str, precisa: str) -> bool:
    """O nível ``tem`` cobre o nível ``precisa``?"""
    return _ORDEM.get(tem, 0) >= _ORDEM[precisa]


def limpar(ajustes: dict[str, str], role: str) -> dict[str, str]:
    """Só os ajustes válidos que fogem do perfil (o resto é o padrão).

    Args:
        ajustes: ``{chave: nível}`` vindos do formulário ou do banco.
        role: Perfil do usuário.

    Returns:
        Ajustes válidos e diferentes do padrão do perfil.
    """
    saida: dict[str, str] = {}
    for chave, nivel in ajustes.items():
        pagina = POR_CHAVE.get(chave)
        if pagina is None or nivel not in NIVEIS:
            continue
        if nivel == ALTERAR and not pagina.alteravel:
            nivel = VER
        if nivel != pagina.padrao(role):
            saida[chave] = nivel
    return saida
