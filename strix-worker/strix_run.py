"""Lógica pura do worker de pentest (sem Docker, sem banco) — fácil de testar.

Reúne o que não depende de efeito colateral: ler a allowlist, revalidar o alvo,
classificar o código de saída do Strix e resumir os achados de um diretório de
run. O loop com Docker/banco fica em :mod:`worker`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

# Códigos de saída do Strix headless: 0 limpo, 2 achou vulnerabilidades, 1 erro.
_EXIT_OK = {0, 2}

# Normalização de severidade (inglês/português → chave única usada na tela).
_SEVERIDADE = {
    "critical": "critica",
    "critica": "critica",
    "crítica": "critica",
    "high": "alta",
    "alta": "alta",
    "medium": "media",
    "media": "media",
    "média": "media",
    "low": "baixa",
    "baixa": "baixa",
    "informational": "info",
    "info": "info",
}
_ORDEM = ("critica", "alta", "media", "baixa", "info")


def allowlist(env: dict[str, str] | None = None) -> dict[str, dict[str, str]]:
    """Allowlist de alvos a partir de ``PENTEST_ALVOS`` (JSON). Igual à do app.

    Args:
        env: Ambiente a usar (padrão: ``os.environ``).

    Returns:
        Mapa ``chave → {"label", "url"}``, só com entradas bem-formadas.
    """
    bruto = (env or os.environ).get("PENTEST_ALVOS", "{}")
    try:
        dados = json.loads(bruto or "{}")
    except ValueError:
        return {}
    if not isinstance(dados, dict):
        return {}
    saida: dict[str, dict[str, str]] = {}
    for chave, info in dados.items():
        if isinstance(info, dict) and str(info.get("url", "")).strip():
            url = str(info["url"]).strip()
            saida[str(chave)] = {"label": str(info.get("label", chave)).strip() or str(chave), "url": url}
    return saida


def url_do_alvo(chave: str, url_esperada: str, env: dict[str, str] | None = None) -> str | None:
    """Revalida o alvo pela allowlist antes de rodar (defesa em profundidade).

    Só devolve a URL se a chave existe na allowlist **e** bate com a URL gravada no
    run (o run não pode ter uma URL que a allowlist não reconhece hoje).

    Returns:
        A URL a varrer, ou ``None`` se o alvo não é mais válido.
    """
    alvo = allowlist(env).get(chave)
    if alvo is None:
        return None
    if url_esperada and url_esperada != alvo["url"]:
        return None
    return alvo["url"]


def status_do_exit(code: int | None) -> str:
    """``concluido`` para saída limpa/com achados (0/2); ``falhou`` para o resto."""
    return "concluido" if code in _EXIT_OK else "falhou"


def _normalizar(sev: object) -> str | None:
    return _SEVERIDADE.get(str(sev).strip().lower()) if sev is not None else None


def resumir_achados(run_dir: str | Path) -> tuple[dict[str, int], str]:
    """Conta os achados por severidade num diretório de run do Strix.

    Varre os ``*.json`` do diretório procurando listas de achados com um campo de
    severidade (``severity``/``severidade``), tolerante a variações de esquema
    entre versões do Strix. Não acha nada → contagem vazia.

    Args:
        run_dir: Diretório do run (ex.: ``strix_runs/<nome>``).

    Returns:
        ``(contagem_por_severidade, caminho_relatorio)``. ``caminho_relatorio`` é o
        primeiro relatório legível encontrado (``.md`` preferido, senão o ``.json``).
    """
    base = Path(run_dir)
    contagem: dict[str, int] = {}
    relatorio = ""
    if not base.is_dir():
        return contagem, relatorio

    md = sorted(base.rglob("*.md"))
    if md:
        relatorio = str(md[0])

    for arquivo in sorted(base.rglob("*.json")):
        try:
            dados = json.loads(arquivo.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        if not relatorio:
            relatorio = str(arquivo)
        for item in _achados_em(dados):
            chave = _normalizar(item.get("severity") or item.get("severidade"))
            if chave:
                contagem[chave] = contagem.get(chave, 0) + 1
    # Reordena para a ordem canônica de severidade.
    ordenada = {k: contagem[k] for k in _ORDEM if k in contagem}
    return ordenada, relatorio


def _achados_em(dados: object) -> list[dict[str, object]]:
    """Extrai a lista de achados de um JSON de formato incerto."""
    if isinstance(dados, list):
        return [x for x in dados if isinstance(x, dict)]
    if isinstance(dados, dict):
        for chave in ("vulnerabilities", "findings", "achados", "results", "issues"):
            valor = dados.get(chave)
            if isinstance(valor, list):
                return [x for x in valor if isinstance(x, dict)]
    return []
