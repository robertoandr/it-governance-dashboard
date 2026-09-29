"""Fluxo de ponta a ponta do módulo Tarefas (critério de aceite da Sprint 4).

login → criar workspace → criar card → mover pelo teclado → documento →
comentário → link direto do card; e o visualizador vendo o mesmo board
sem conseguir editar.
"""

from __future__ import annotations

import re
import uuid

from playwright.sync_api import Browser, Page, expect

from e2e.usuarios import Usuario

# O board consulta a revisão a cada 15 s (ADR 0008); mudanças feitas fora
# da página aparecem em até esse tempo.
ESPERA_ATUALIZACAO_MS = 20_000


def _entrar(page: Page, url_base: str, usuario: Usuario) -> None:
    page.goto(f"{url_base}/gov/login?next=/gov/tarefas")
    page.get_by_label("Email").fill(usuario.email)
    page.get_by_label("Senha").fill(usuario.senha)
    page.get_by_role("button", name="Entrar").click()
    expect(page).to_have_url(re.compile(r"/gov/tarefas$"))


def _metrica(page: Page, nome: str):  # type: ignore[no-untyped-def]
    return page.locator(f'[data-metrica="{nome}"]')


def _coluna(page: Page, status: str):  # type: ignore[no-untyped-def]
    return page.locator(f'ol[data-status="{status}"]')


def test_fluxo_completo(browser: Browser, url_base: str, usuarios: dict[str, Usuario]) -> None:
    nome_ws = f"E2E {uuid.uuid4().hex[:8]}"
    titulo = "Trocar switch do rack 2"

    admin = browser.new_context(base_url=url_base)
    page = admin.new_page()
    _entrar(page, url_base, usuarios["admin"])

    # Workspace (a página recarrega e o board "Principal" aparece no card do workspace)
    page.get_by_role("button", name="Novo workspace").click()
    page.get_by_label("Nome").fill(nome_ws)
    page.get_by_role("dialog").locator('button[type="submit"]').click()
    workspace = page.get_by_role("listitem").filter(has=page.get_by_role("heading", name=nome_ws))
    workspace.get_by_role("link", name="Principal").click()
    expect(page).to_have_url(re.compile(r"/gov/tarefas/b/\d+$"))
    url_board = page.url
    expect(page.locator("#tarefas-sync")).to_contain_text("Atualizado às")
    expect(_metrica(page, "total")).to_have_text("0")

    # Criação rápida no Backlog
    page.locator('[data-abrir-criar="backlog"]').click()
    page.get_by_label("Título do novo card em Backlog").fill(titulo)
    page.keyboard.press("Enter")
    card = _coluna(page, "backlog").locator(".tarefas-card", has_text=titulo)
    expect(card).to_be_visible()
    expect(_metrica(page, "total")).to_have_text("1")
    expect(_metrica(page, "done-doc")).to_have_text("—")

    # Mover só com o teclado até Done; cada passo é anunciado ao leitor de tela
    anuncio = page.locator("#tarefas-anuncio")
    for coluna, rotulo in (("todo", "To Do"), ("doing", "Doing"), ("done", "Done")):
        page.locator(".tarefas-card", has_text=titulo).focus()
        page.keyboard.press("Alt+ArrowRight")
        expect(anuncio).to_have_text(f'"{titulo}" movido para {rotulo}.')
        expect(_coluna(page, coluna).locator(".tarefas-card", has_text=titulo)).to_be_visible()
    expect(_metrica(page, "done")).to_have_text("1 (100%)")
    expect(_metrica(page, "done-doc")).to_have_text("0 de 1 (0%)")

    # A posição persiste ao recarregar
    page.reload()
    card = _coluna(page, "done").locator(".tarefas-card", has_text=titulo)
    expect(card).to_be_visible()

    # Painel do card: URL própria, documento salvo sozinho, comentário em Markdown
    card.click()
    painel = page.locator("#card-painel")
    expect(painel).to_be_visible()
    expect(page).to_have_url(re.compile(r"/gov/tarefas/b/\d+/c/\d+$"))
    url_card = page.url
    page.locator("#doc-texto").fill("## Decisão\n\nTrocar pelo modelo **48 portas**.")
    expect(page.locator("#doc-status")).to_contain_text("Salvo às", timeout=10_000)

    page.get_by_role("tab", name=re.compile("Comentários")).click()
    page.get_by_label("Novo comentário").fill("Compra aprovada pela **diretoria**.")
    page.keyboard.press("Control+Enter")
    expect(page.locator("#com-lista strong", has_text="diretoria")).to_be_visible()

    page.keyboard.press("Escape")
    expect(painel).to_be_hidden()
    expect(page).to_have_url(url_board)
    expect(_metrica(page, "done-doc")).to_have_text("1 de 1 (100%)", timeout=ESPERA_ATUALIZACAO_MS)

    # Link direto abre o card
    page.goto(url_card)
    expect(painel).to_be_visible()
    expect(page.locator("#card-titulo")).to_have_value(titulo)
    admin.close()

    # Visualizador: vê o board e o card, mas não cria nem move
    leitor = browser.new_context(base_url=url_base)
    page = leitor.new_page()
    _entrar(page, url_base, usuarios["visualizador"])
    page.goto(url_board)
    card = _coluna(page, "done").locator(".tarefas-card", has_text=titulo)
    expect(card).to_be_visible()
    expect(page.locator("[data-abrir-criar]")).to_have_count(0)
    card.focus()
    page.keyboard.press("Alt+ArrowLeft")
    expect(_coluna(page, "done").locator(".tarefas-card", has_text=titulo)).to_be_visible()
    card.click()
    expect(page.locator("#card-painel")).to_be_visible()
    expect(page.locator("#doc-preview strong", has_text="48 portas")).to_be_visible()
    expect(page.locator("#doc-texto")).to_have_count(0)
    leitor.close()
