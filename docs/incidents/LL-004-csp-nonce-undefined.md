# LL-004 — CSP nonce undefined → HTTP 500 em todas as rotas HTML

| Campo | Valor |
|-------|-------|
| **Data** | 2026-06-06 |
| **Severidade** | P2 — HTTP 500 em todas as rotas HTML em Docker |
| **Detectado por** | Roberto (navegação manual, F11 fullscreen) |
| **Tempo até diagnóstico** | ~3h |
| **Tempo até fix** | 15min após diagnóstico correto |
| **Commit do fix** | `65b34e2` |
| **PR relacionado** | #134 (introduziu o `base.html` que dependia de Talisman) |
| **Status** | ✅ Fechado |

---

## Sintoma reportado

Tooltips "fantasmas" aparecendo nos cards de pilar ao entrar em fullscreen (F11),
desaparecendo sozinhos após ~2s. Comportamento intermitente, não reproduzível
via `python app.py` direto.

## Causa raiz

`app/templates/base.html` invoca `{{ csp_nonce() }}` como função Jinja2.
O Flask-Talisman, quando inicializado, registra `csp_nonce` automaticamente
no contexto de templates. Porém, a `create_app()` em `app/__init__.py` **nunca
inicializou Talisman** — só configurava headers básicos via `@app.after_request`.

Resultado:

```
jinja2.exceptions.UndefinedError: 'csp_nonce' is undefined
```

Disparado no render de `base.html`, **antes** de qualquer conteúdo da página
ser produzido. Flask retornava HTTP 500, mas o browser exibia render parcial
do cache anterior, induzindo o observador a atribuir o erro ao último elemento
visual conhecido (os cards de pilar com tooltip).

## Por que demorou ~3h para diagnosticar

1. **Sintoma visual descolado da causa real.** O "tooltip fantasma" era na
   verdade o browser renderizando uma página em cache enquanto a nova request
   retornava 500 com body mínimo.

2. **Cache de browser mascarou o 500.** O HTML de erro que Flask-RESTX gera
   para 500 é tão curto que o browser substituiu apenas parte do DOM, tornando
   o comportamento intermitente e confuso.

3. **Não reproduzível fora do container.** `python app.py` (via `wsgi_prod.py`)
   serve o app legado (`app.py`), onde `csp_nonce` não é chamado. O bug só
   existia na rota do factory app (`wsgi.py` → `create_app()`), que é o que
   Docker usa.

4. **Recon insuficiente no início.** A investigação começou pelo template e
   tooltip, não por `curl localhost:5000 | grep -c "500"`.

## Solução aplicada

Em `app/__init__.py` (`65b34e2`):

```python
# Nonce por request, armazenado em g
@app.before_request
def _generate_nonce() -> None:
    g.csp_nonce = secrets.token_urlsafe(16)

# Exposto como callable no contexto Jinja2
@app.context_processor
def inject_globals() -> dict[str, Any]:
    return {
        ...,
        "csp_nonce": lambda: getattr(g, "csp_nonce", ""),
    }

# Header CSP real emitido com o nonce
@app.after_request
def add_security_headers(response):
    nonce = getattr(g, "csp_nonce", "")
    response.headers["Content-Security-Policy"] = (
        f"default-src 'self'; "
        f"script-src 'nonce-{nonce}' 'strict-dynamic' ..."
    )
    return response
```

## Timeline

| Hora (BRT) | Evento |
|-----------|--------|
| ~20:00 | Roberto reporta "tooltip fantasma" nos cards de pilar |
| ~20:10 | Investigação começa pelo HTML/CSS dos cards |
| ~21:30 | Recon dos templates (`grep -rn tooltip`) — cards sem tooltip |
| ~22:00 | Reconhecimento da dualidade de apps (`app.py` vs `app/__init__.py`) |
| ~22:30 | `docker compose up` revela HTTP 500 via `curl` |
| ~22:35 | `docker logs itgov-app` mostra `UndefinedError: 'csp_nonce' is undefined` |
| ~22:50 | Fix implementado, testes passando, deploy feito |

## Ações preventivas

- [x] Fix imediato: `csp_nonce` registrado no factory via `before_request` + `context_processor`
- [x] `TestHTMLViews` agora passa (4/4) — cobria esse caminho mas estava falhando
- [ ] Smoke test em CI que sobe container e valida `GET /` retorna 200 com conteúdo esperado
- [ ] Migrar para Flask-Talisman no factory app — elimina implementação manual do nonce
- [ ] Documentar dependências implícitas do `base.html` em comentário no topo do arquivo
- [ ] `docs/dev-setup.md`: documentar dualidade `app.py` vs `app/__init__.py` e quando cada um é servido

## Lições aprendidas

**1. O sintoma mente, o curl não.**
Sempre validar o que o servidor realmente retorna antes de investigar UI:
```bash
curl -s http://localhost:5000/ | head -5   # status code real
docker logs itgov-app | tail -30          # traceback real
```

**2. Cache de browser é inimigo do diagnóstico.**
Hard reload (`Ctrl+Shift+R`) ou aba anônima são obrigatórios. Melhor ainda:
verificar direto com `curl` que não tem cache.

**3. Templates têm contratos implícitos.**
`{{ csp_nonce() }}` em `base.html` é uma dependência de runtime que não aparece
em nenhum `import` ou `requirements.txt`. Merece comentário explícito:
```html
<!-- Requer: csp_nonce registrado via context_processor (app/__init__.py) -->
```

**4. Smoke test em CI vale ouro.**
Um `curl / | grep 200` automatizado no pipeline teria pego isso antes do merge
do PR #134. Custo: 10 linhas de YAML. Benefício: detecta toda a categoria de
"factory app não inicializa X" em < 30s.

**5. Dois apps no mesmo repo são um landmine.**
`app.py` (legado, servido via `wsgi_prod.py`) e `app/__init__.py` (factory,
servido via `wsgi.py`) têm configurações independentes. Um bug em um não
reproduz no outro. Isso **deve** estar documentado no README de dev.

## Referências

- Commit do fix: [`65b34e2`](https://github.com/robertoandr/it-governance-dashboard/commit/65b34e2)
- PR que introduziu a regressão: [#134](https://github.com/robertoandr/it-governance-dashboard/pull/134)
- Flask-Talisman (substituto correto): <https://github.com/GoogleCloudPlatform/flask-talisman>
- CSP nonce spec: <https://www.w3.org/TR/CSP3/#security-nonces>
- Issue dualidade InfluxDB no dev server: [#139](https://github.com/robertoandr/it-governance-dashboard/issues/139)
