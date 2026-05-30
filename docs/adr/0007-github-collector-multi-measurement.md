# ADR-0007: Coletor GitHub com fan-out multi-measurement

## Status: Aceito

## Contexto

O coletor GitHub precisa gravar dois measurements distintos:
- `gov_github_pr` — pull requests por repo
- `gov_github_workflow` — GitHub Actions workflow runs por repo

E precisa coletar dados de N repositórios configurados via `GITHUB_REPOS`.

## Decisão

### Coletor único com fan-out interno

Um único `GitHubCollector` itera sobre todos os repos em `fetch()`,
retornando envelopes tipados `{"type": "pr"|"workflow", "data": dict, "repo": str}`.
O `transform()` despacha para o transformer correto via `item["type"]`.

```
fetch()
  ├── repo-a → list_pulls_since + list_workflow_runs_since
  └── repo-b → list_pulls_since + list_workflow_runs_since

transform(envelopes)
  ├── {"type": "pr", ...}       → pr_to_point()
  └── {"type": "workflow", ...} → workflow_to_point()
```

### Reutilização do GitHubClient existente

`GitHubCollectorClient` é subclasse de `app.integrations.github.client.GitHubClient`.
Herda: autenticação Bearer, paginação via Link header, rate-limit handling,
retry tenacity em 5xx. Adiciona: `list_pulls_since()` e `list_workflow_runs_since()`
com early-exit quando o item está antes do cutoff.

### Partial failure por repo

Se um repo falhar (5xx, timeout, rate limit), o erro é capturado e logado;
os outros repos continuam sendo coletados normalmente.

```python
for repo in self.settings.repos:
    try:
        ...
    except (GitHubAPIError, httpx.HTTPError) as exc:
        log.error("github.repo_fetch_failed", repo=repo, error=str(exc))
```

## Trade-offs

| | |
|---|---|
| ✅ | 1 trigger do scheduler por coleta (vs 2 com coletores separados) |
| ✅ | Auth e rate-limit compartilhados — sem risco de double-counting do limit |
| ✅ | Partial failure por repo (erro em 1 não derruba os outros) |
| ✅ | Zero duplicação: herda toda a infraestrutura do integration client |
| ❌ | `fetch()` retorna dicts heterogêneos (type: pr/workflow) — menos type-safe |
| ❌ | `transform()` com dispatch condicional — adicionar novos types exige editar o switch |

## Alternativas consideradas

**Dois coletores separados** (`GitHubPRCollector`, `GitHubWorkflowCollector`):
- Duplica auth/client/rate-limit handling
- 2 triggers no scheduler, maior risco de race condition no rate limit
- Descartado

**GraphQL API** (single request para todos os dados):
- Reduziria o número de requests
- Maior complexidade de implementação e debug
- Descartado por prematuridade — REST v3 é suficiente para o volume atual

## Métricas esperadas (por coleta, repo único)

| Operação | Requests |
|---|---|
| `list_pulls_since` | 1–N páginas (early-exit por updated_at) |
| `list_workflow_runs_since` | 1–N páginas (early-exit por run_started_at) |
| `write_points` | 1 batch write em governance_raw |
