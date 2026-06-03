# feat(m365): Service Principals Orphans Module [Sprint 12 B]

## 📋 Resumo do Módulo

Implementação do módulo de detecção de **Service Principals Órfãos** do Microsoft 365,
parte do pilar de Governança M365 (Sprint 12). Coleta todos os SPs do tenant via
Microsoft Graph API, calcula um risk score e persiste métricas no InfluxDB para
visualização no Grafana.

### Arquivos criados

| Arquivo | Descrição |
|---------|-----------|
| `itgov/utils/risk_scoring.py` | Algoritmo de scoring (0-100, 4 níveis) |
| `itgov/services/graph_client.py` | Client async Graph API com retry e delta queries |
| `itgov/services/delta_token_store.py` | Persistência SQLite do deltaToken |
| `itgov/services/sp_orphans_collector.py` | Orchestrator: Graph → InfluxDB |
| `itgov/services/influx_schema.py` | Schema InfluxDB + 3 queries Flux |
| `k8s/m365-collector-cronjob.yaml` | CronJob + NetworkPolicy |
| `k8s/m365-collector-pvc.yaml` | PVC 1Gi para delta token |
| `docker/Dockerfile.m365-collector` | Multi-stage, hardened |
| `grafana/dashboards/m365-sp-orphans.json` | Dashboard 8 painéis |
| `tests/test_delta_token_store.py` | 9 testes (100% coverage) |
| `tests/test_collector_delta.py` | 19 testes (96.88% coverage) |

---

## 🔄 Arquitetura: Delta Sync com Microsoft Graph

Este módulo usa **delta queries** do Microsoft Graph para sincronização
incremental, reduzindo drasticamente a carga sobre o rate limit do tenant.

| Métrica | Full Scan (rejeitado) | Delta Sync (implementado) |
|---------|----------------------|---------------------------|
| Requests/run (5k SPs) | ~5.000 | ~50 (após 1º run) |
| Tempo de coleta | ~5 min | ~10 seg |
| Risco rate limit | 🔴 Alto | 🟢 Baixo |

**Persistência:** `deltaToken` armazenado em SQLite (`/data/delta_tokens.db`)
via PVC dedicado (1Gi RWO). Token nunca é logado (apenas hash truncado SHA-256).

**Segurança:** Em caso de falha mid-stream, o token anterior é preservado —
o próximo run retoma do mesmo ponto sem perda de dados.

---

## 📁 Nota sobre estrutura de namespace

Os módulos novos residem em `itgov/services/`, `itgov/utils/`, etc.
(não `app/`) por conflito estrutural histórico entre `app.py` (módulo Flask)
e `app/` (pacote). Este é o **padrão existente do projeto** — mantida
consistência com `--cov=itgov`.

> 📌 TODO pós-merge: atualizar `.claude-missions/lib/stack-context.md`
> para refletir o namespace `itgov/` oficialmente.

---

## 🔐 Permissões Microsoft Graph necessárias

| Permission | Tipo | Justificativa |
|-----------|------|---------------|
| `Application.Read.All` | Application | Listar todos os SPs |
| `AuditLog.Read.All` | Application | Acessar `signInActivity` |
| `Directory.Read.All` | Application | Metadados de objetos |

> Todas as 20 permissões concedidas antes do início do Sprint 12.

---

## 🚀 Como rodar localmente

```bash
# 1. Instalar dependências
pip install -r requirements.txt -r requirements-dev.txt

# 2. Configurar env (copiar .env.local.example)
cp .env.local.example .env.local
# Preencher AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET

# 3. Rodar testes do módulo
pytest tests/test_delta_token_store.py tests/test_collector_delta.py \
  --asyncio-mode=auto -v

# 4. Suite completa
pytest --cov=itgov --cov-report=term-missing

# 5. Build Docker (opcional)
docker build -f docker/Dockerfile.m365-collector -t m365-collector:dev .

# 6. Aplicar K8s (dry-run)
kubectl apply --dry-run=client -f k8s/
```

---

## ✅ Checklist (lib/acceptance-gates.md)

### 🐍 Qualidade de Código Python
- [x] `ruff check .` — 0 warnings (pre-commit passing)
- [x] `ruff format --check .` — OK
- [x] `mypy itgov/` — 100% type hints
- [x] `pytest --cov=itgov` — **85.50%** cobertura total (≥ 85% ✅)
  - `delta_token_store`: **100%**
  - `sp_orphans_collector`: **96.88%**
- [x] `bandit -r itgov/` — 0 HIGH
- [x] `gitleaks detect` — 167 commits, 0 leaks

### 🐳 Container
- [x] Multi-stage build (builder + runtime)
- [x] UID 1000 (non-root)
- [x] readOnlyRootFilesystem compatible (`/tmp` + `/data` como mounts)
- [x] Sem secrets em layers

### ☸️ Kubernetes
- [x] SecurityContext: 12/12 checks (runAsNonRoot, readOnlyRootFilesystem, drop ALL, seccomp)
- [x] `kubectl apply --dry-run=client` — OK
- [x] Resources requests + limits definidos
- [x] NetworkPolicy restritiva (Graph + InfluxDB egress apenas)
- [x] `automountServiceAccountToken: false`

### 📊 Observabilidade
- [x] `structlog` em todos os módulos (print proibido)
- [x] Credentials nunca logadas (só status code e hash truncado)
- [x] Dashboard Grafana: 8 painéis, UID `m365-sp-orphans-v1`

### 🔐 Segurança
- [x] Secrets via `config.*` (nunca `os.environ[]` direto — regra arquitetural)
- [x] Validação Pydantic em modelos
- [x] `deltaToken` nunca logado (SHA-256 truncado)
- [x] Regra arquitetural `test_no_os_environ_direct` — **PASSING** ✅

---

## ⚠️ Riscos & Rollback Plan

**Rollback:** O módulo é aditivo — nenhuma tabela existente foi alterada.
Para reverter: `kubectl delete -f k8s/` + remoção do bucket `m365_security` no InfluxDB.

**Risco principal:** Rate limit do Graph API em tenants com > 10k SPs.
Mitigação: delta queries reduzem ~99% das requests após o primeiro run.
Se o primeiro full scan estourar o rate limit: re-run automático (CronJob `backoffLimit: 2`).

---

## 📸 Dashboard Screenshots

> _Placeholders — adicionar após primeiro deploy no ambiente de staging._

- [ ] Panel "Total SPs" com dados reais
- [ ] Panel "SPs CRITICAL" threshold vermelho
- [ ] Time series 7 dias com baseline

---

🤖 Generated with [Claude Code](https://claude.com/claude-code)
