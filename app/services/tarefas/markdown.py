"""Renderização de Markdown do módulo Tarefas (documento e comentários).

Duas camadas contra XSS:
1. ``markdown-it-py`` com ``html=False``: HTML cru digitado no texto vira
   texto escapado, e links ``javascript:``/``data:`` são recusados.
2. ``nh3`` (Rust/ammonia) sobre o HTML gerado: só passam as tags e os
   atributos da lista branca abaixo, e links só com http/https/mailto.

O HTML resultante é o único conteúdo que o front insere com innerHTML.
"""

from __future__ import annotations

import re

import nh3
from markdown_it import MarkdownIt

_MD = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False}).enable(
    ["table", "strikethrough"]
)

_TAGS: set[str] = {
    "a",
    "blockquote",
    "br",
    "code",
    "del",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "li",
    "ol",
    "p",
    "pre",
    "s",
    "strong",
    "table",
    "tbody",
    "td",
    "th",
    "thead",
    "tr",
    "ul",
}
_ATRIBUTOS: dict[str, set[str]] = {
    "a": {"href", "title"},
    "code": {"class"},  # "language-python" do bloco cercado
    "th": {"style"},
    "td": {"style"},
}
_ESQUEMAS: set[str] = {"http", "https", "mailto"}

# Checklist no estilo GitHub ("- [ ] item"). O commonmark não tem; trocamos o
# marcador por um símbolo depois da sanitização (só texto, sem HTML novo).
# Listas "soltas" (linha em branco entre itens) saem como "<li>\n<p>[ ] ...".
_CHECKLIST = re.compile(r"<li>(\s*<p>)?\[( |x|X)\] ")


def _marcar_checklist(html: str) -> str:
    def trocar(m: re.Match[str]) -> str:
        simbolo = "☐" if m.group(2) == " " else "☑"
        return f"<li>{m.group(1) or ''}{simbolo} "

    return _CHECKLIST.sub(trocar, html)


def renderizar(texto: str) -> str:
    """Converte Markdown em HTML seguro para exibir no navegador.

    Args:
        texto: Markdown digitado pelo usuário.

    Returns:
        HTML sanitizado (lista branca de tags, links com rel seguro).
    """
    if not texto.strip():
        return ""
    bruto = _MD.render(texto)
    limpo = nh3.clean(
        bruto,
        tags=_TAGS,
        attributes=_ATRIBUTOS,
        url_schemes=_ESQUEMAS,
        link_rel="noopener noreferrer nofollow",
        filter_style_properties={"text-align"},
    )
    return _marcar_checklist(limpo)
