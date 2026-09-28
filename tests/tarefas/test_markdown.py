"""Renderização de Markdown: recursos suportados e neutralização de XSS (critério da Sprint 3)."""

from __future__ import annotations

from html.parser import HTMLParser

import pytest

from app.services.tarefas.markdown import renderizar

PAYLOADS_XSS = [
    "<script>alert(1)</script>",
    '<img src=x onerror="alert(1)">',
    "<svg onload=alert(1)>",
    '<iframe src="https://evil.example"></iframe>',
    "[clique](javascript:alert(1))",
    "[clique](JaVaScRiPt:alert(1))",
    "[clique](data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==)",
    "[clique](vbscript:msgbox(1))",
    '<a href="javascript:alert(1)">x</a>',
    "![img](javascript:alert(1))",
    '<div style="background:url(javascript:alert(1))">x</div>',
    "<details open ontoggle=alert(1)>",
    '[x](https://ok.example "title" onmouseover="alert(1)")',
]


class _Tags(HTMLParser):
    """Coleta as tags e atributos que o navegador de fato interpretaria."""

    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, list[tuple[str, str | None]]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, attrs))


TAGS_PERMITIDAS = {
    "p",
    "a",
    "strong",
    "em",
    "code",
    "pre",
    "s",
    "del",
    "ul",
    "ol",
    "li",
    "blockquote",
    "hr",
    "br",
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
}


@pytest.mark.parametrize("payload", PAYLOADS_XSS)
def test_payloads_de_xss_sao_neutralizados(payload: str) -> None:
    coletor = _Tags()
    coletor.feed(renderizar(payload))
    for tag, attrs in coletor.tags:
        assert tag in TAGS_PERMITIDAS, (payload, tag)
        for nome, valor in attrs:
            assert not nome.lower().startswith("on"), (payload, nome)
            if nome == "href":
                assert (valor or "").lower().startswith(("http://", "https://", "mailto:")), (payload, valor)
            if nome == "style":
                assert "url(" not in (valor or "").lower(), (payload, valor)


def test_html_cru_vira_texto_visivel() -> None:
    assert renderizar("<b>negrito?</b>") == "<p>&lt;b&gt;negrito?&lt;/b&gt;</p>\n"


def test_recursos_suportados() -> None:
    html = renderizar(
        "## Decisão\n\nUsar **Redis**, *talvez* ~~Memcached~~, com `cache`.\n\n"
        "- [ ] pendente\n- [x] feito\n\n1. um\n2. dois\n\n> citação\n\n"
        "| a | b |\n|:-|-:|\n| 1 | 2 |\n\n```python\nprint(1)\n```\n\n---\n\n"
        "[site](https://grupogadens.com.br) e [e-mail](mailto:ti@grupogadens.com.br)"
    )
    for trecho in (
        "<h2>Decisão</h2>",
        "<strong>Redis</strong>",
        "<em>talvez</em>",
        "<s>Memcached</s>",
        "<code>cache</code>",
        "<li>☐ pendente</li>",
        "<li>☑ feito</li>",
        "<ol>",
        "<blockquote>",
        '<th style="text-align:left">a</th>',
        '<td style="text-align:right">2</td>',
        '<code class="language-python">',
        "<hr>",
        '<a href="https://grupogadens.com.br" rel="noopener noreferrer nofollow">site</a>',
        '<a href="mailto:ti@grupogadens.com.br" rel="noopener noreferrer nofollow">e-mail</a>',
    ):
        assert trecho in html, trecho


def test_texto_vazio_ou_so_espacos() -> None:
    assert renderizar("") == ""
    assert renderizar("   \n\t") == ""


def test_checklist_no_texto_fora_de_lista_nao_e_trocado() -> None:
    assert "[ ]" in renderizar("texto [ ] no meio")
