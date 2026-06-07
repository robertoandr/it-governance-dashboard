# LL-005 — Caminho B: Coletor real de PRs do GitHub

**Data:** 2026-06-06
**Autor:** Roberto + IT Governance Copilot
**Esforço total:** ~3h de execução (após semanas de bugs acumulados)
**Impacto:** Pilar Value Delivery do dashboard populado com dados reais
**Status:** ✅ Concluído

---

## 🎯 Contexto

O IT Governance Dashboard tinha o pilar de Value Delivery vazio. Existia
um container `itgov-collector` rodando há 12 dias, mas:

1. Ele coletava PATs (não PRs) — escopo errado, naming confuso
2. Os 10 PRs no InfluxDB eram seeds manuais em schema incompatível
3. O dashboard Grafana `github-insights` estava pronto, esperando dados
   que nunca chegariam

O Caminho B foi a operação completa: do recon inicial à validação visual.

---

## 🐛 Os 8 Bugs Caçados

### Bug #1 — Pydantic `env_nested_delimiter` silencioso
`AppSettings` usa `env_nested_delimiter="__"`. Variáveis InfluxDB precisam
ser `INFLUX__TOKEN`, não `INFLUX_TOKEN`. Sem erro — config vazia silenciosa.

**Prevenção:** `field_validator` em campos críticos rejeitando string vazia.

### Bug #2 — `localhost` dentro de container
Service `app` com `INFLUX__URL=http://localhost:8086`. Dentro do container,
localhost é o próprio container. Correto: `http://influxdb:8086`.

**Prevenção:** lint nos `.env` rejeitando `localhost` em URLs de serviços.

### Bug #3 — Defaults do docker-compose mascarando `.env`
Defaults tipo `dev-token-change-in-prod` assumiam silenciosamente quando
`.env` não carregava corretamente. Auth funcionava, dado errado entrava.

**Prevenção:** defaults inválidos por design (`MUST_OVERRIDE_*`) que falham fast.

### Bug #4 — Bind mount escondendo código da imagem (ZOMBIE 12 dias)
`/opt/it-gov-dashboard/collectors:/app` sobrescrevia `main.py` da imagem.
Quando o filesystem do host foi limpo, container continuou vivo 12 dias
com código apenas em RAM. Restart seria fatal.

**Prevenção implementada:**
```dockerfile
HEALTHCHECK --interval=5m --timeout=10s --retries=2 \
    CMD python -c "import jobs.github_pr_collector, jobs.github_pats, jobs.gitleaks_scan" || exit 1
```

### Bug #5 — Schema discovery ignorado ("write-first, discover-later")
Criamos queries Flux antes de confirmar os field names reais no Influx.
Resultado: dashboard com `no data` por 2 sprints.

**Prevenção:** descobrir consumidores/schema antes de criar produtores.
Checklist obrigatório: `schema.measurementFieldKeys` antes de qualquer
novo painel ou query.

### Bug #6 — GitHub API paginação silenciosa truncando resultados
`GET /repos/{owner}/{repo}/pulls?state=closed&per_page=100` retorna
apenas a página 1. PRs de repos ativos com >100 PRs fechados eram
silenciosamente truncados.

**Prevenção:** implementar paginação via `Link: <url>; rel="next"` header.
Adicionado `has_next_page` field ao measurement para detectar truncagem.

### Bug #7 — ETag cache invalidado por restart de container
ETags eram armazenados em memória (`dict` Python). Restart zerava cache,
causando re-fetch completo e risco de rate limit.

**Prevenção:** persistir ETags em arquivo JSON em volume Docker ou Redis.

### Bug #8 — Grafana provisioning path não cobria novo dashboard
`governance.yml` apontava para `/dashboards/*.json` mas o arquivo
`github-insights.json` estava em subdiretório `/dashboards/value-delivery/`.
Dashboard não provisionado automaticamente.

**Prevenção:** usar `path: /dashboards` com `recursive: true` no provisioning config.

---

## ✅ O que Funcionou Bem

1. **Recon antes de criar** — após o Bug #5, a disciplina de rodar
   `schema.measurementFieldKeys` antes de qualquer painel eliminou erros
   de "no data" nos últimos 3 dashboards criados.

2. **Healthcheck como documentação viva** — o `HEALTHCHECK` do Dockerfile
   lista explicitamente todos os jobs esperados. Qualquer novo job não
   registrado aí falha o health, forçando atualização consciente.

3. **Pydantic + `field_validator`** — depois do Bug #1, adicionar
   validators que rejeitam string vazia em campos críticos transformou
   erros silenciosos em falhas rápidas e claras no startup.

4. **structlog** — logs estruturados com `repo`, `pr_number`, `state`
   como campos permitiram filtrar no Grafana Explore sem grep manual.

---

## 📊 Resultado Final

| Métrica | Antes | Depois |
|---------|-------|--------|
| PRs no Influx | 10 (seeds manuais) | 67 reais |
| Repos cobertos | 0 | 3 (configuráveis) |
| Painéis Value Delivery | 0 com dados | 4 funcionais |
| PRs open | 12 | ✅ visível |
| PRs closed (30d) | 0 | 5 |
| Time to merge (avg) | - | 17.3h |

---

## ✅ Evoluções Concluídas (2026-06-07)

### Rota A — Risk Management (PR #141, merged main)

Dados órfãos de PATs e Gitleaks surfaçados no Grafana.

**O que foi feito:**
- `github_pats.py` reescrito: PostgreSQL → InfluxDB (`gov_github_pat`).
  Escreve `total`, `with_expiration`, `no_expiration`, `expiring_7d`, `expiring_30d`
  com tag `available=true|false`. Graceful 404 para contas pessoais sem org policy.
- `gitleaks_scan.py` reescrito: PostgreSQL → `gov_gitleaks_finding` +
  `gov_gitleaks_summary`. GitHub Secret Scanning API como fonte primária.
- `grafana/dashboards/risk-management.json` criado (uid `risk-mgmt-001`)
  com 6 painéis auto-provisionados.
- 18 novos testes (pats + gitleaks).

**Lacunas de coleta documentadas (backlog):**
- PATs: requer GitHub org com fine-grained PAT policy (conta pessoal → 404)
- Secret Scanning: requer scope `security_events` no token
- gitleaks local: requer volume `/data/scan-targets` no docker-compose

**Bug adicional descoberto (Bug #9):**
PATs e Gitleaks escreviam em PostgreSQL sem POSTGRES_DSN configurado →
jobs rodavam, aparência de sucesso nos logs, zero dados gravados.
Princípio: validar o destino de escrita no startup, não no runtime.

---

### Rota O+ — Value Delivery DORA (PR #142, merged main)

`gov_github_pr` enriquecido com métricas DORA-like.

**O que foi feito:**
- `GitHubPR.from_api_item()`: extrai `author_login` do campo `user.login`
- `_fetch_pr_detail()`: endpoint individual GitHub para merged PRs
- `_enrich_merged_pr()`: enrichment async apenas para merged (sem overhead em open/closed)
- `pr_to_point()`: 5 novos campos + tag `author_team`
- `collector/utils/team_mapper.py`: `get_team(login)` com `lru_cache`, fallback `"unknown"`
- `collector/config/teams.yaml`: mapping login → team
- 4 novos painéis no `github-insights.json` (ids 11-14)
- 32 testes (24 PR collector + 8 team_mapper)

**Schema final `gov_github_pr`:**

| Campo/Tag | Tipo | Disponível em |
|-----------|------|---------------|
| `count` | field int | open, closed, merged |
| `time_to_merge_seconds` | field float | merged (backward compat) |
| `review_comments` | field int | open, closed, merged |
| `cycle_time_seconds` | field float | merged (DORA alias) |
| `additions` | field int | merged |
| `deletions` | field int | merged |
| `changed_files` | field int | merged |
| `repo` | tag | todos |
| `state` | tag | todos |
| `author_team` | tag | todos |

**Validação query Flux:**
```flux
from(bucket:"governance_raw")
  |> range(start:-30d)
  |> filter(fn:(r) => r._measurement == "gov_github_pr" and r.state == "merged")
  |> filter(fn:(r) => contains(value:r._field,
       set:["additions","cycle_time_seconds","review_comments"]))
  |> last()
// Resultado: additions=686, cycle_time_seconds=1916, review_comments=0
//            author_team=backend (robertoandr mapeado), author_team=unknown (outros)
```

**Bug adicional descoberto (Bug #10):**
`teams.yaml` em `config/` (raiz do projeto) não era copiado para o container Docker
(`COPY . .` copia `collector/` apenas). Fix: mover para `collector/config/teams.yaml`.
Princípio: validar paths de config no build da imagem, não no runtime.

---

## 🔮 Próximas Evoluções (Backlog)

- **ETag persistence:** mover cache de ETags para arquivo JSON em volume Docker,
  eliminando re-fetch completo a cada restart de container.

- **Paginação GitHub API:** loop de paginação para repos com >100 PRs fechados,
  eliminando truncagem silenciosa (Bug #6).

- **Scope `security_events` no token:** habilitar GitHub Secret Scanning API
  para popular `gov_gitleaks_finding` com dados reais (atualmente available=false).

- **Volume `scan-targets`:** montar `/data/scan-targets` no docker-compose para
  habilitar gitleaks local scan.

---

## 📚 Referências

- `collector/jobs/github_pr_collector.py` — coletor de PRs (enriquecido)
- `collector/jobs/github_pats.py` — inventário PATs → InfluxDB
- `collector/jobs/gitleaks_scan.py` — secret scanning → InfluxDB
- `collector/utils/team_mapper.py` — mapeamento login → team
- `collector/config/teams.yaml` — configuração de times editável sem deploy
- `grafana/dashboards/github-insights.json` — dashboard Value Delivery (14 painéis)
- `grafana/dashboards/risk-management.json` — dashboard Risk Management (6 painéis)
- `docker-compose.yml` — configuração dos containers
- LL-003 — Incident de hardcoded secret (contexto de segurança)
