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

Além dos alertas entram as **atividades** concluídas na janela
(``/api/task_manager/v2/activities``): o encaminhamento por syslog só existe
com gravador Linux e não temos agente Linux, então o rastro de quem mexeu no
console (plano editado ou revogado, agente removido, sessão remota) e os
patches que falharam vêm pela API (``resumir_atividades`` e
``achados_de_atividades``).

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


# ── Atividades (o que o syslog traria como "atividades e tarefas") ──────────

_FALHAS = ("error", "timedout")
_TIPOS_PATCH = ("SystemPatchInstallation", "UserPatchInstallation")
_TIPOS_ATUALIZACAO = ("UpdateAgent", "DownloadInstaller", "Download", "Rollback")
_SESSAO_REMOTA = "rds_ai_assistant"


def _curto(nome: Any) -> str:
    return str(nome or "").split(".")[0].strip()


def _maquina_da_atividade(a: dict[str, Any]) -> str:
    """Máquina da atividade: o agente que executou, ou o que vier no contexto."""
    ctx = a.get("context") or {}
    return _curto(a.get("executorHostname")) or _curto(ctx.get("computerName")) or _curto(ctx.get("machineAddress"))


def _humano(usuario: str) -> bool:
    """Conta de pessoa (e-mail); ``BY_SCHEDULE`` e o nome do tenant são do próprio Acronis."""
    return "@" in usuario


def resumir_atividades(atividades: list[dict[str, Any]], inicio: datetime, fim: datetime) -> dict[str, Any]:
    """O que foi feito no Acronis na janela: mudanças de proteção, sessões remotas e falhas.

    Args:
        atividades: Itens crus de ``/api/task_manager/v2/activities``.
        inicio: Começo da janela (exclusivo).
        fim: Fim da janela (inclusivo).

    Returns:
        ``{"total", "relevantes", "por_tipo", "protecao_removida", "planos_editados",
        "planos_editados_automatico", "sessoes_remotas", "patches_falhos",
        "atualizacao_agente_falha"}``; as listas trazem textos prontos para evidência.
    """
    revogados: Counter[tuple[str, str]] = Counter()
    removidos: list[str] = []
    editados: Counter[tuple[str, str]] = Counter()
    automaticos = 0
    sessoes: dict[str, set[str]] = {}
    patches: list[str] = []
    atualizacoes: Counter[str] = Counter()
    por_tipo: Counter[str] = Counter()
    total = 0
    for a in atividades:
        quando = _data(a.get("completedAt") or a.get("updatedAt"))
        if not quando or not inicio < quando <= fim:
            continue
        total += 1
        tipo = str(a.get("type") or "?")
        codigo = str((a.get("result") or {}).get("code") or "?")
        ctx = a.get("context") or {}
        titulo = str(ctx.get("title") or "")
        usuario = str(a.get("startedByUser") or "").strip() or "?"
        plano = str(ctx.get("PlanName") or (a.get("policy") or {}).get("name") or "?")
        if titulo.startswith("Revoking plan"):
            revogados[(usuario, plano)] += 1
            tipo = "plano revogado"
        elif titulo.startswith("Removing agent"):
            removidos.append(f"{usuario}: agente {_curto((ctx.get('Persistent') or {}).get('Name'))} removido")
            tipo = "agente removido"
        elif titulo.startswith("Editing plan"):
            if _humano(usuario):
                editados[(usuario, plano)] += 1
            else:
                automaticos += 1
            tipo = "plano editado"
        elif _SESSAO_REMOTA in tipo:
            sessoes.setdefault(usuario, set()).add(str(ctx.get("session_id") or a.get("uuid") or ""))
            tipo = "sessão remota"
        elif tipo in _TIPOS_PATCH and codigo in _FALHAS:
            aviso = ((a.get("result") or {}).get("warnings") or [{}])[0].get("context") or {}
            erro = f" (erro {aviso['errCode']})" if aviso.get("errCode") else ""
            patches.append(f"{_maquina_da_atividade(a) or '?'}: {ctx.get('appName') or '?'}{erro}")
        elif tipo in _TIPOS_ATUALIZACAO and codigo in _FALHAS:
            atualizacoes[_maquina_da_atividade(a) or "?"] += 1
        por_tipo[f"{tipo} ({codigo})"] += 1
    removida = [f"{u}: plano '{p}' revogado de {n} máquina(s)" for (u, p), n in revogados.most_common()] + removidos
    editadas = [f"{u}: plano '{p}' editado {n}x" for (u, p), n in editados.most_common()]
    remotas = [f"{u}: {len(ids)} sessão(ões)" for u, ids in sorted(sessoes.items())]
    patches = sorted(set(patches))
    return {
        "total": total,
        "relevantes": len(removida) + len(editadas) + len(remotas) + len(patches) + len(atualizacoes),
        "por_tipo": [[t, n] for t, n in por_tipo.most_common(12)],
        "protecao_removida": removida[:_MAX_MAQUINAS],
        "planos_editados": editadas[:_MAX_MAQUINAS],
        "planos_editados_automatico": automaticos,
        "sessoes_remotas": remotas[:_MAX_MAQUINAS],
        "patches_falhos": patches[:_MAX_MAQUINAS],
        "patches_falhos_total": len(patches),
        "atualizacao_agente_falha": _lista(atualizacoes),
    }


# chave do resumo → (assinatura, severidade, título, resumo, recomendação)
_REGRAS_ATIVIDADES: dict[str, tuple[str, str, str, str, str]] = {
    "protecao_removida": (
        "acronis-protecao-removida",
        "media",
        "Proteção do Acronis retirada de máquinas",
        "Plano de proteção revogado ou agente removido do console nesta janela.",
        "Confirmar com quem fez se a máquina saiu de uso; se não saiu, reaplicar o plano de proteção.",
    ),
    "planos_editados": (
        "acronis-plano-alterado",
        "baixa",
        "Plano de proteção do Acronis alterado",
        "Alguém editou um plano de proteção no console nesta janela.",
        "Conferir o que mudou no plano (proteções desligadas, exclusões novas) e se a mudança foi combinada.",
    ),
    "patches_falhos": (
        "acronis-patch-falhou",
        "baixa",
        "Instalação de patch falhou pelo Acronis",
        "Atualizações de programa que o Acronis tentou instalar e deram erro nesta janela.",
        "Repetir a instalação ou atualizar o programa à mão nas máquinas listadas.",
    ),
}


def achados_de_atividades(resumo: dict[str, Any]) -> list[Achado]:
    """Achados objetivos das atividades do Acronis, que valem mesmo sem a IA.

    Args:
        resumo: Saída de ``resumir_atividades``.

    Returns:
        Um achado por regra que disparou, com ``fortigate`` = ``Acronis``.
    """
    achados: list[Achado] = []
    for chave, (assinatura, severidade, titulo, texto, recomendacao) in _REGRAS_ATIVIDADES.items():
        evidencias = resumo.get(chave) or []
        if evidencias:
            achados.append(
                Achado(
                    assinatura=assinatura,
                    fortigate=FONTE,
                    severidade=severidade,
                    titulo=titulo,
                    resumo=texto,
                    evidencias=evidencias,
                    recomendacao=recomendacao,
                )
            )
    return achados
