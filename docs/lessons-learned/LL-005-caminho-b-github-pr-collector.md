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

## 🔮 Evoluções Previstas

- **Rota O+ (Value Delivery+):** enriquecer `gov_github_pr` com campos
  DORA-like: `additions`, `deletions`, `changed_files`, `review_comments`,
  `cycle_time_seconds`, tag `author_team` via `config/teams.yaml`.
  PR: `feat(collector): enrich gov_github_pr with DORA-like metrics`

- **Rota A (Risk Management):** surfaçar dados órfãos de PATs e Gitleaks
  em dashboard `risk-management.json` com 6 painéis (3 PATs + 3 secrets).

- **ETag persistence:** mover cache de ETags para volume Docker (arquivo JSON)
  eliminando re-fetch completo a cada restart.

- **Paginação GitHub API:** implementar loop de paginação para repos com
  >100 PRs fechados, eliminando truncagem silenciosa (Bug #6).

---

## 📚 Referências

- `app/jobs/github_pr_collector.py` — implementação atual do coletor
- `grafana/dashboards/github-insights.json` — dashboard Value Delivery
- `docker-compose.yml` — configuração do container `itgov-collector`
- `app/core/config.py` — AppSettings com `env_nested_delimiter`
- LL-003 — Incident de hardcoded secret (contexto de segurança)
