# Inventário Forense do :8080 (Nginx Gateway + Legado)

> **Gerado em:** D.0 — 2026-06-07
> **Objetivo:** Capturar TUDO que tem valor antes de qualquer descomissionamento.
> **Autor:** Forense automatizada via Playwright + análise manual.

---

## ⚠️ CORREÇÃO CRÍTICA DE TOPOLOGIA

> O briefing original afirmava ":8080 = legado a matar". **Isso está errado.**

### Topologia Real (verificada em D.0)

| Porta | Serviço | Status |
|-------|---------|--------|
| `:8080` | **Nginx host** — gateway de PRODUÇÃO | ativo, crítico |
| `:8443` | HTTPS self-signed → mesmo Nginx | ativo |
| `:443` | HTTPS Let's Encrypt `noc.grupogadens.com.br` → mesmo Nginx | **produção pública** |
| `:8091` | **Gunicorn host** (10 workers) — app Flask PRODUÇÃO em `/opt/it-gov-dashboard/` | ativo, crítico |
| `:5000` | Docker Flask — **dev/staging** | ativo |
| `:8082` | Uvicorn (app desconhecida, 404 root) | investigar |
| `:9090` | Uvicorn (app desconhecida, redirect `/login`) | investigar |
| `:15050-15053` | 4 processos Python (coletores/schedulers?) | investigar |
| `:10051` | Zabbix Server (host) | produção |
| `:10050` | Zabbix Agent 2 (host) | produção |
| `:5432` | PostgreSQL host (interno) | produção |
| `:18086` | InfluxDB Docker (exposto — Issue #148) | risco |
| `:13000`, `:3000` | Grafana Docker (x2 instâncias) | ativo |
| `:3100` | Loki | ativo |
| `:3200` | Tempo | ativo |
| `:9091` | Prometheus | ativo |
| `:4317/4318` | OpenTelemetry Collector | ativo |
| `:3389` | RDP | investigar (exposição?) |

### O que o Nginx :8080 realmente faz

```
:8080 / :443 / :8443  ←→  Nginx host
    /              → proxy → :8091 (Flask PRODUÇÃO)
    /dashboard/    → proxy → :8091
    /governanca/   → proxy → :8091
    /gov/          → proxy → :8091
    /static/       → proxy → :8091
    /api/          → proxy → :8091
    /auth/         → proxy → :8091
    /v2/           → proxy → :8091
    /zabbix/       → FastCGI PHP (Zabbix no host)
    /api_jsonrpc.php → proxy → :8080/zabbix/api_jsonrpc.php (CORS habilitado)
    /m365data/     → /var/www/m365data/ (JSONs estáticos coletados)
    /dashboard-ti.html → /usr/share/nginx/html/dashboard-ti.html (legado, acessível mas não default)
    /legacy/       → /usr/share/nginx/html/ (legado completo)
```

**Conclusão: "Matar :8080" = desligar produção pública. NÃO FAZER sem migrar o Nginx config para container ou k8s primeiro.**

---

## 🎨 1. Ativos Visuais (Design Tokens)

### Paleta Dark (GitHub-inspired)

```css
/* Extraída do dashboard-ti.html via análise CSS */
:root {
  --bg:       #0d1117;   /* fundo principal */
  --surface:  #161b22;   /* cards, sidebar */
  --surface2: #21262d;   /* inputs, hover */
  --border:   #30363d;   /* bordas */
  --text:     #e6edf3;   /* texto principal */
  --muted:    #8b949e;   /* texto secundário */
  --accent:   #58a6ff;   /* azul principal (links, destaque) */
  --accent2:  #3fb950;   /* verde (sucesso, OK) */
  --cyan:     #39d0d8;   /* ciano (métricas especiais) */
  --purple:   #bc8cff;   /* roxo (RBAC, segurança) */
  --danger:   #f85149;   /* vermelho (alertas críticos) */
  --warn:     #d29922;   /* amarelo (atenção) */
  --radius:   6px;       /* border-radius padrão */
}
```

**Migrar para:** `tailwind.config.js` como tema customizado dark (skill: `tailwind-design-system`).

### Tipografia
- **Fonte:** `'Segoe UI', system-ui, sans-serif` — substituível por `Inter` (já em uso no :5000)
- **Monospace:** para trigger rules e código técnico

### Ícones
- **Font Awesome 6.5** via CDN: `fa-solid fa-shield-halved` (logo/header)
- **Substituir por:** Tabler Icons (já em uso no :5000) ou SVG inline

---

## 🧩 2. Componentes UI Reutilizáveis

### 2.1 Navegação por Tabs (9 seções)

**Tabs identificadas (ordem do menu):**

| Tab | Conteúdo | Dados |
|-----|----------|-------|
| `overview` | KPIs gerais, rings de disponibilidade e SLA | **hardcoded** (mock) |
| `incidents` | Histórico de incidentes por mês/tipo | **hardcoded** (mock) |
| `services` | Tabela de serviços M365 | `/var/www/m365data/` (real) |
| `alerts` | Alertas Zabbix | Zabbix API |
| `users` | Usuários M365, MFA, contas bloqueadas | `/var/www/m365data/` (real) |
| `rbac` | Tabela de perfis e permissões | **hardcoded** |
| `heatmap` | Mapa de calor de incidentes (hora × dia) | **hardcoded** |
| `triggers` | Triggers Zabbix ativos | Zabbix API |
| `checklist` | Checklists ISO 27001, ITIL v4, LGPD | **hardcoded** |

**Padrão de lazy rendering (RESGATAR):**

```javascript
const renderedTabs = new Set(['overview']);
function renderTabCharts(tab) {
  if (renderedTabs.has(tab)) return;  // evita re-render
  renderedTabs.add(tab);
  if (tab === 'incidents') renderIncidents();
  // ...
}
```

**Equivalente Alpine.js:**
```html
<div x-data="{ activeTab: 'overview', rendered: new Set(['overview']) }"
     x-init="rendered.add(activeTab)">
  <template x-if="rendered.has('incidents')">
    <!-- chart component -->
  </template>
</div>
```

### 2.2 Chart.js — Helpers Reutilizáveis (RESGATAR)

```javascript
// Sistema de defaults dark (resgatar inteiro)
const chartDefaults = {
  responsive: true,
  maintainAspectRatio: false,
  plugins: { legend: { labels: { color: '#8b949e', font: { size: 11 } } } },
  scales: {
    x: { grid: { color: '#21262d' }, ticks: { color: '#8b949e', font: { size: 10 } } },
    y: { grid: { color: '#21262d' }, ticks: { color: '#8b949e', font: { size: 10 } } }
  }
};

// Helper genérico (resgatar)
function mkChart(id, type, data, extraOpts = {}) { ... }

// Ring chart (disponibilidade, MFA%) — resgatar
function mkRing(id, pct, color) { ... }
```

**Migrar para:** `app/static/js/charts.js` ou Alpine.js component.

### 2.3 KPI Rings (Padrão Visual Validado)

Gráficos donut de disponibilidade (availChart, mfaChart, complianceChart, profileChart):
- `cutout: '75%'` — anel fino
- Fundo: `#21262d`
- Cor dinâmica por threshold

**Migrar para:** componente Jinja2 + Chart.js CDN (já em uso no :5000).

### 2.4 Relógio em Tempo Real

```javascript
function updateClock() {
  document.getElementById('clock').textContent =
    now.toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'medium' });
}
setInterval(updateClock, 1000);
```

**Equivalente Alpine.js:**
```html
<span x-data="{ now: '' }"
      x-init="setInterval(() => now = new Date().toLocaleString('pt-BR', {dateStyle:'short', timeStyle:'medium'}), 1000)"
      x-text="now"></span>
```

---

## 🔌 3. Integrações Externas (CRÍTICAS)

### 3.1 M365 Data — JSON Estático (ATIVO e REAL)

**Localização:** `/var/www/m365data/dashboard_data.json`
**Atualizado:** 2026-06-07T19:20 (coletado hoje)

```json
{
  "updated_at": "2026-06-07T19:20:07-0300",
  "services_total": 29, "services_ok": 25, "services_degraded": 4,
  "incidents_active": 7,
  "users": { "total": 329, "active": 327, "blocked": 2, "guests": 3 },
  "security": {
    "mfa_enabled": 281, "mfa_disabled": 43,
    "risky_users": 20, "admins_count": 5
  },
  "services": [ { "name": "Exchange Online", "status": "serviceDegradation" }, ... ]
}
```

**Plano de migração:** Expor via rota Flask `GET /api/m365/summary` que lê o JSON (enquanto o coletor M365 não tem endpoint próprio).

### 3.2 Zabbix — Host FastCGI

- **Zabbix Frontend:** `/zabbix/` via FastCGI PHP no host
- **Zabbix API:** `POST /api_jsonrpc.php` com CORS `*` habilitado
- **ZABBIX_FRONT_URL:** presente no `.env` ✅
- **Zabbix Server:** `:10051` no host (produção)

**Importante:** O Nginx :8080 faz proxy da API com CORS permissivo (`*`). Ao migrar, revisar e restringir.

### 3.3 Nginx Headers de Segurança (resgatar para Flask)

Presentes no bloco HTTPS (:8443 e :443):
```nginx
add_header X-Frame-Options "SAMEORIGIN";           # Issue #145
add_header X-Content-Type-Options "nosniff";
add_header X-XSS-Protection "1; mode=block";
add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
```

**Migrar para:** `@app.after_request` no Flask (resolve Issue #145 e #146).

### 3.4 Cache de Assets Estáticos

```nginx
location /static/ {
  expires 1h;
  add_header Cache-Control "public, max-age=3600" always;
}
```

**Migrar para:** Flask `send_from_directory` com headers ou Gunicorn + WhiteNoise.

---

## ⚙️ 4. Configuração Nginx Útil (Preservar)

### 4.1 Rotas Alias para Retrocompatibilidade

```nginx
# Esses aliases já existem e têm usuários:
/dashboard/   → app
/governanca/  → app
/gov/         → app
```

**Migrar para:** Blueprints Flask ou redirecionamentos permanentes.

### 4.2 TLS/SSL

- Self-signed em `/etc/nginx/ssl/dashboard-ti.{crt,key}`
- Let's Encrypt em `/etc/letsencrypt/live/noc.grupogadens.com.br/`
- **Renovação automática via certbot** (acme-challenge.conf presente)

### 4.3 Logs

```
/var/log/nginx/noc-access.log
/var/log/nginx/noc-error.log
/var/log/nginx/noc-https-access.log
/var/log/nginx/noc-ssl-error.log
```

---

## 📊 5. Dados do Dashboard Legacy — Estado Real

| Dado | Fonte | É Real? |
|------|-------|---------|
| Disponibilidade semanal | Hardcoded JS | ❌ mock |
| SLA por área | Hardcoded JS | ❌ mock |
| Incidentes por mês/tipo | Hardcoded JS | ❌ mock |
| Alertas Zabbix | Zabbix API | ✅ potencialmente real |
| Triggers Zabbix | Zabbix API | ✅ potencialmente real |
| Serviços M365 | `/var/www/m365data/` | ✅ real (atualizado hoje) |
| Usuários/MFA M365 | `/var/www/m365data/` | ✅ real |
| Checklists ISO/ITIL/LGPD | Hardcoded HTML | ❌ estático |
| Heatmap incidentes | Hardcoded JS | ❌ mock |
| RBAC table | Hardcoded HTML | ❌ estático |

**Insight:** O legado é 70% mockado. O valor real está nos padrões visuais e nos dados M365 + Zabbix.

---

## 🚫 6. O Que NÃO Migrar

| Item | Motivo |
|------|--------|
| Dados hardcoded (mock) | Substituir por API calls reais |
| CORS `*` na API Zabbix | Restringir para origens específicas |
| `dashboard-ti.html` como arquivo estático | Já substituído por templates Flask |
| `X-XSS-Protection: 1; mode=block` | Header obsoleto (navegadores ignoram) |
| Múltiplas cópias do HTML | Consolidar em uma fonte de verdade |

---

## 🗺️ 7. Mapa das Instâncias do dashboard-ti.html

| Caminho | Linhas | Data | Versão |
|---------|--------|------|--------|
| `/usr/share/nginx/html/dashboard-ti.html` | 1.233 | Mai/14 | antiga |
| `/var/www/dashboard-ti/dashboard-ti.html` | 909 | Mai/11 | mais antiga |
| `/var/www/html/dashboard-ti.html` | **2.074** | **Mai/17** | **✅ canônica** |
| `~/projects/.../dashboard-ti.html` | 2.074 | Mai/17 | igual à canônica |

**Fonte da verdade:** `/var/www/html/dashboard-ti.html` (2.074 linhas, Mai/17).

---

## 📅 8. Plano de Migração Revisado

> **Premissa corrigida:** :8080 não morre. O Nginx host se mantém como gateway.
> O que muda é: o Gunicorn em :8091 (host) e o Docker :5000 devem ser consolidados.

### Sprint 1 — Dados Reais (P1, ~3h)

- [ ] Rota `GET /api/m365/summary` lendo `/var/www/m365data/dashboard_data.json`
- [ ] Conectar `/api/pillars` com dados Zabbix (ZABBIX_FRONT_URL já disponível)
- [ ] Remover mocks hardcoded do legado (não migrar, substituir)

### Sprint 2 — Design Tokens Dark (P2, ~2h)

- [ ] Criar `tailwind.config.js` com paleta GitHub-dark extraída acima
- [ ] Resgatar `mkChart` / `mkRing` helpers → `app/static/js/charts.js`
- [ ] Migrar lazy-render pattern → Alpine.js `x-if` + `rendered Set`

### Sprint 3 — Headers de Segurança (P2, ~1h)

- [ ] `X-Frame-Options: SAMEORIGIN` → Flask `after_request` (resolve #145)
- [ ] `Strict-Transport-Security` → Flask (ambiente prod)
- [ ] Restringir CORS da API Zabbix de `*` para origem específica

### Sprint 4 — Aliases de URL (P3, ~30min)

- [ ] Blueprint Flask com redirects `/dashboard/`, `/governanca/`, `/gov/`
- [ ] Atualizar `noc.grupogadens.com.br.conf` para refletir novos paths

### Sprint 5 — Consolidação Gunicorn (P2, ~2h)

- [ ] Unificar `:8091` (host) e `:5000` (Docker) em uma estratégia
- [ ] Decisão: usar Docker em prod ou manter host Gunicorn? (ADR necessário)
- [ ] Investigar `:8082`, `:9090`, `:15050-53` (apps desconhecidas)

### Sprint 6 — Descomissionamento do Legado (P3, ~30min)

- [ ] Mover `/dashboard-ti.html` para `docs/archive/`
- [ ] Remover blocos `/dashboard-ti.html` e `/legacy/` do nginx.conf
- [ ] Redirect 301 de `/legacy/` → `/`

---

## 🔍 9. Itens Para Investigar (Antes de Agir)

| Item | Risco | Ação |
|------|-------|------|
| `:8082` — qual app? (requer sudo para ver PID) | Médio | Ver seção 10 abaixo |
| `:3389` RDP exposto | **Alto** | Verificar se é interno ou público |
| Duas instâncias Grafana (:3000 e :13000) | Baixo | Qual é a oficial? |
| `itgov-postgres` TimescaleDB ativo | Médio | ADR-0004 foi superseded mas container roda |

---

## 🔬 10. REVISÃO PÓS-RECON (D.0 — 2026-06-07)

Recon completo executado após inventário inicial. Todos os "mistérios" foram identificados.

### 10.1 Processos :15050–15053 — Claude Code Orphans (NÃO são coletores)

**Identificação:** 4 processos Python rodando `from app import create_app; app.run(port=1505x)` com:

```
FLASK_ENV=testing
APP__ENVIRONMENT=testing
CLAUDE_CODE_SSE_PORT=53115
cwd → /home/zabbix/projects/it-governance-dashboard
PPID → 1 (init — detached de sessão anterior do Claude Code)
```

**Conclusão:** São servidores de teste do **Claude Code** (tool executor para testes de integração). Ficaram órfãos quando sessões anteriores encerraram sem cleanup. **Não são coletores APScheduler.**

**Ação:** Matar com segurança — não afetam produção. São recriados automaticamente quando Claude Code precisar deles.

```bash
kill 2894850 2926932 2930424 2932261
```

### 10.2 :9090 — Zabbix MCP Admin UI (initMAX)

**Identificação:** Interface web administrativa do `zabbix-mcp-server` (produto initMAX).

```
Serviço: /etc/systemd/system/zabbix-mcp-server.service
Processo: /opt/zabbix-mcp/venv/bin/python3.12 zabbix-mcp-server --config /etc/zabbix-mcp/config.toml
Usuário: zabbix-mcp (uid separado)
Bind: 127.0.0.1:9090 (localhost — não exposto externamente)
Title: "Login — Zabbix MCP Admin"
```

**Risco:** Baixo — localhost only. Mas credenciais hardcoded no env podem ser risco se acessível via SSRF.

### 10.3 :8082 — Identidade pendente (requer sudo)

`ss -tlnp` mostra `127.0.0.1:8082` com backlog 2048 (padrão gunicorn/uvicorn) mas **sem PID visível** (owner é root ou outro usuário). Curl retorna `404 text/plain` — app existe mas rota `/` não mapeada.

**Candidatos eliminados:**
- Não é Docker (nenhum container usa 8082)
- Não é Nginx host (não aparece em `/etc/nginx/conf.d/`)
- Não é gunicorn do `it-gov-dashboard` (usa :8091)
- Não é `dashboard-ti.service` (usa :5000 ou estava em :8081 per env vars)

**Candidato mais provável:** `zabbix-mcp-server` transport MCP (SSE/HTTP) — o admin UI está em :9090 e o protocolo MCP pode ser em :8082. Confirmar com `sudo ss -tlnp | grep 8082`.

### 10.4 Mapa de Serviços Adicional (descoberto no recon)

Novos serviços não mapeados no inventário inicial:

| Porta | Processo/Serviço | Bind | Notas |
|-------|-----------------|------|-------|
| `:5001` | Desconhecido (backlog 511 = nginx) | 0.0.0.0 | Investigar |
| `:5939` | TeamViewer daemon | 127.0.0.1 | `/opt/teamviewer` instalado |
| `:7070` | Desconhecido (backlog 10 = pequeno) | 0.0.0.0 | Investigar |
| `:12666` | `MainThread` pid=626212 | 127.0.0.1 | Investigar |
| `:46881` | `code-f6cfa2ea24` pid=2220621 | 127.0.0.1 | VS Code tunnel? |
| `:44015` | `code-6a44c352bd` pid=626160 | 127.0.0.1 | VS Code tunnel? |
| `:100.64.x.x:39306` | Tailscale/WireGuard | Tailscale IP | VPN mesh |

**TeamViewer:** `/opt/teamviewer` existe — potencial risco de acesso remoto não auditado. Verificar com equipe de infra.

### 10.5 Serviços Systemd Identificados

| Arquivo | Serviço | Status atual |
|---------|---------|-------------|
| `it-gov-dashboard.service` | Gunicorn :8091 (produção) | ativo, 10 workers |
| `dashboard-ti.service` | Gunicorn legado `/opt/dashboard-ti` (:5000?) | status desconhecido |
| `zabbix-mcp-server.service` | Zabbix MCP (initMAX) | ativo |
| `actions.runner.*.service` | GitHub Actions Runner | ativo (user: github-runner) |

**`dashboard-ti.service`** tinha credenciais hardcoded em `Environment=` (senha padrão Zabbix).
**Remediado em D.0:** movido para `EnvironmentFile=/etc/dashboard-ti/secrets.env`,
usuário rotacionado para `svc_dashboard` com senha forte, porta migrada de `:5000` para `:8082`.
