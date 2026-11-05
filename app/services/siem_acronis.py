"""Alertas do Acronis no agente SIEM (item 3.5 do plano de implantação do Acronis).

O plano previa ler o syslog do Acronis, mas o encaminhamento por syslog (item
1.5) ainda não existe no console. Enquanto isso, como foi feito com os
FortiGate, a fonte é a API: os alertas ativos de
``/api/alert_manager/v1/alerts``. A cada análise do SIEM entram os alertas
**criados na janela** analisada, classificados como no agente de triggers
(``acronis_triggers.categoria_do_alerta``).

Funções puras (testáveis sem rede):

- ``resumir``: contagens objetivas da janela, para a IA e para as regras;
- ``achados_por_regra``: malware, EDR crítico, proteção com falha e programa de
  acesso remoto instalado. O "FortiGate" desses achados é ``Acronis``.

Achados do Acronis ficam só no dashboard (decisão de 05/11/2026: alertas do
Acronis sem WhatsApp); o agente SIEM não os manda para o item de graves.
Tudo aqui é leitura.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any

from app.services import acronis_triggers
from itgov.services.acronis_programas import TIPO_ALERTA as TIPO_SOFTWARE
from itgov.services.acronis_programas import Instalacao, _data
from itgov.services.siem_fortigate import Achado

FONTE = "Acronis"
_MAX_MAQUINAS = 10


def _novos(alertas: list[dict[str, Any]], inicio: datetime, fim: datetime) -> list[dict[str, Any]]:
    """Alertas criados dentro da janela ``(inicio, fim]``."""
    novos = []
    for a in alertas:
        quando = _data(a.get("createdAt") or a.get("receivedAt"))
        if quando and inicio < quando <= fim:
            novos.append(a)
    return novos


def _lista(maquinas: Counter[str]) -> list[str]:
    return [f"{m} ({n})" if n > 1 else m for m, n in maquinas.most_common(_MAX_MAQUINAS)]


def resumir(alertas: list[dict[str, Any]], inicio: datetime, fim: datetime) -> dict[str, Any]:
    """Contagens dos alertas do Acronis criados na janela.

    Args:
        alertas: Itens crus de ``/api/alert_manager/v1/alerts``.
        inicio: Começo da janela (exclusivo).
        fim: Fim da janela (inclusivo).

    Returns:
        ``{"ativos", "novos", "por_tipo", "por_categoria", "maquinas_mais_alertas",
        "acesso_remoto_instalado"}``; ``por_categoria`` = categoria → máquinas (com
        a quantidade quando passa de 1).
    """
    novos = _novos(alertas, inicio, fim)
    por_cat: dict[str, Counter[str]] = {c.chave: Counter() for c in acronis_triggers.CATEGORIAS}
    remoto: list[str] = []
    for a in novos:
        maquina = acronis_triggers._maquina(a)
        cat = acronis_triggers.categoria_do_alerta(a)
        if cat:
            por_cat[cat][maquina] += 1
        if a.get("type") == TIPO_SOFTWARE:
            d = a.get("details") or {}
            inst = Instalacao(
                maquina=maquina,
                programa=str(d.get("software_name") or "").strip(),
                versao=str(d.get("software_version") or "").strip(),
            )
            if inst.programa and inst.acesso_remoto:
                remoto.append(f"{inst.maquina}: {inst.programa} {inst.versao}".strip())
    return {
        "ativos": len(alertas),
        "novos": len(novos),
        "por_tipo": [[t, n] for t, n in Counter(str(a.get("type") or "?") for a in novos).most_common(12)],
        "por_categoria": {cat: _lista(c) for cat, c in por_cat.items() if c},
        "maquinas_mais_alertas": _lista(Counter(acronis_triggers._maquina(a) for a in novos)),
        "acesso_remoto_instalado": sorted(set(remoto))[:_MAX_MAQUINAS],
    }


# categoria → (assinatura, severidade, título, recomendação)
_REGRAS: dict[str, tuple[str, str, str, str]] = {
    "malware": (
        "acronis-malware",
        "alta",
        "Malware ou ameaça detectada pelo Acronis",
        "Abrir o incidente no Acronis, confirmar se a ameaça foi contida (quarentena/bloqueio) "
        "e isolar a máquina se não foi.",
    ),
    "edr_critico": (
        "acronis-edr-critico",
        "alta",
        "Incidente EDR crítico não mitigado",
        "Investigar o incidente no Acronis (cadeia de processos) e decidir a ação; "
        "a proposta do agente de incidentes passa pela fila de aprovação.",
    ),
    "protecao": (
        "acronis-protecao-falha",
        "media",
        "Proteção do Acronis com falha em máquinas",
        "Conferir o agente nas máquinas listadas: serviço parado, driver ausente, conflito "
        "com outro antivírus ou definições desatualizadas.",
    ),
}


def achados_por_regra(resumo: dict[str, Any]) -> list[Achado]:
    """Achados objetivos dos alertas novos do Acronis, que valem mesmo sem a IA.

    Args:
        resumo: Saída de ``resumir``.

    Returns:
        Um achado por regra que disparou, com ``fortigate`` = ``Acronis``.
    """
    achados: list[Achado] = []
    for cat, (assinatura, severidade, titulo, recomendacao) in _REGRAS.items():
        maquinas = (resumo.get("por_categoria") or {}).get(cat) or []
        if not maquinas:
            continue
        achados.append(
            Achado(
                assinatura=assinatura,
                fortigate=FONTE,
                severidade=severidade,
                titulo=titulo,
                resumo=f"{len(maquinas)} máquina(s) com alerta novo nesta janela.",
                evidencias=maquinas,
                recomendacao=recomendacao,
            )
        )
    remoto = resumo.get("acesso_remoto_instalado") or []
    if remoto:
        achados.append(
            Achado(
                assinatura="acronis-acesso-remoto-instalado",
                fortigate=FONTE,
                severidade="media",
                titulo="Programa de acesso remoto instalado",
                resumo=f"{len(remoto)} instalação(ões) de programa que dá controle da máquina a quem está de fora.",
                evidencias=remoto,
                recomendacao="Confirmar com o usuário se a instalação foi autorizada; se não, desinstalar "
                "e revisar o acesso à máquina.",
            )
        )
    return achados
