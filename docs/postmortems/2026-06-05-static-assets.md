# Post-Mortem: Static Assets 404 via nginx :8080

**Data:** 2026-06-05
**Duração:** ~2h de investigação
**Impacto:** Acesso via tunnel SSH dev (localhost:5000) sem CSS/JS

## Causa Raiz

`location` blocks de `/static/`, `/api/`, `/v2/`, `/auth/` existiam apenas
nos server blocks HTTPS (`:443`, `:8443`). Bloco HTTP (`:8080`) ficou
incompleto após migração para HTTPS.

```
Browser/tunnel → nginx :8080 → ??? → 404
                              ↑
                    location /static/ AUSENTE no bloco HTTP
```

Flask (upstream `:8091`) servia os assets corretamente o tempo todo —
o problema era o nginx não roteando até ele.

## Timeline

| Hora | Evento |
|------|--------|
| Diagnóstico | `curl 127.0.0.1:8091/static/style.css → 200` — Flask OK |
| Diagnóstico | `curl 127.0.0.1:8080/static/style.css → 404` — nginx bloqueando |
| Root cause | Leitura do nginx conf: `location /static/` ausente no bloco `:8080` |
| Fix | Adição de 5 `location` blocks ao server HTTP |
| Validação | `curl 127.0.0.1:8080/static/style.css → 200` ✅ |

## Por Que Não Foi Detectado Antes?

- nginx config não estava versionada no repo (sem code review)
- Sem testes automatizados de smoke nos 3 listeners (`:8080`, `:8443`, `:443`)
- Acesso via tunnel HTTP é caminho menos usado (dev only) — HTTPS passou nos testes

## Correção Aplicada

**PR #125** — dois entregáveis:

1. `infra/nginx/noc.grupogadens.com.br.conf` — config versionada no repo pela primeira vez
2. Adição ao bloco `server { listen 8080 }`:
   - `location /static/` → `proxy_pass 8091/static/` com `Cache-Control: public, max-age=3600`
   - `location /api/` → `proxy_pass 8091/api/` com `no-cache`
   - `location /v2/` → `proxy_pass 8091/v2/`
   - `location /auth/` → `proxy_pass 8091/auth/`
   - `location /` → catch-all `proxy_pass 8091/`

## Resultado da Validação Pós-Fix

```
127.0.0.1:8080 /          → 200 ✅
127.0.0.1:8080 /static/   → 200 ✅  Cache-Control: public, max-age=3600
127.0.0.1:8080 /api/      → 200 ✅
127.0.0.1:8091 /static/   → 200 ✅  (Flask direto)
127.0.0.1:8443 /static/   → 200 ✅  (nginx SSL interno)
noc.grupogadens.com.br     → 401    (Cloudflare Access Gate — esperado em prod)
```

## Prevenção

### 1. CI: `nginx -t` obrigatório em PRs que tocam `infra/nginx/`

Adicionar ao `.github/workflows/`:

```yaml
- name: Validate nginx config
  if: contains(github.event.pull_request.changed_files, 'infra/nginx/')
  run: docker run --rm -v $PWD/infra/nginx:/etc/nginx/conf.d:ro nginx nginx -t
```

### 2. Refatorar com `include` para eliminar duplicação

Extrair os `location` blocks comuns (static, api, auth) para um arquivo compartilhado:

```nginx
# infra/nginx/includes/dashboard-upstreams.conf
location /static/ { proxy_pass http://127.0.0.1:8091/static/; ... }
location /api/    { proxy_pass http://127.0.0.1:8091/api/; ... }
location /auth/   { proxy_pass http://127.0.0.1:8091/auth/; ... }

# noc.grupogadens.com.br.conf
server { listen 8080; include includes/dashboard-upstreams.conf; }
server { listen 443 ssl; include includes/dashboard-upstreams.conf; }
```

### 3. Health check script em 3 camadas

```bash
# scripts/healthcheck-nginx.sh
for port in 8080 8443; do
  proto=$([ "$port" = "8080" ] && echo http || echo https)
  for path in / /static/style.css /api/health; do
    code=$(curl -sk -o /dev/null -w "%{http_code}" $proto://127.0.0.1:$port$path)
    [ "$code" = "200" ] || echo "FAIL :$port$path → $code"
  done
done
```
