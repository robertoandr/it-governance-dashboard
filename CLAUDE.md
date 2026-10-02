# IT Governance Dashboard

---

## 1. Ambiente

| Parametro            | Valor                                        |
|----------------------|----------------------------------------------|
| Usuario de execucao  | `zabbix`                                     |
| Servidor DEV         | `itgov-dev` (172.29.2.11)                    |
| Caminho do projeto   | `/home/zabbix/projects/it-governance-dashboard` |
| Repositorio          | `robertoandr/it-governance-dashboard`        |
| Branch padrao        | `main` (protegida — requer PR + aprovacao)   |

Valores sensiveis (DSNs, tokens, caminhos de producao) estao em `.env.local` (nao versionado).
Consulte `.env.local.example` para o formato esperado.

---

## 2. Persistencia Hibrida

| Papel                  | Tecnologia DEV   | Tecnologia PROD       |
|------------------------|------------------|-----------------------|
| Dados relacionais CRUD | SQLite (WAL)     | PostgreSQL 16         |
| Time-series / metricas | InfluxDB 2.7     | InfluxDB 2.7 (Cloud)  |

> **Nota ADR:** A ADR-0004 (TimescaleDB como storage unico) foi **SUPERSEDED** durante o
> planejamento da V1.1. A estrategia vigente e a ADR-0001: dual storage
> SQLite/PostgreSQL para CRUD + InfluxDB para series temporais.
> O InfluxDB ja estava em producao para metricas Zabbix, tornando o
> dual storage mais eficiente que uma migracao para TimescaleDB unificado.

ADRs completas em `docs/adr/`.

---

## 3. Integracoes

| Sistema          | Protocolo          | Proposito                                  |
|------------------|--------------------|--------------------------------------------|
| GitHub           | REST API v3        | Metricas de repositorios e PRs             |
| Zabbix           | JSON-RPC 2.0       | Alertas e disponibilidade de infraestrutura|
| Zendesk          | REST API v2        | SLA e CSAT de service desk                 |
| Microsoft Graph  | REST + OAuth2      | Dados M365: usuarios, MFA, Secure Score    |

---

## 7. Regras de Codigo (obrigatorias)

1. **Type hints** em TODAS as funcoes — parametros e retorno
2. **Docstrings Google style** em classes e metodos publicos
3. **Pydantic v2** para validacao de I/O em endpoints e servicos
4. **structlog** para logging — `print()` e `logging.getLogger()` sao proibidos
5. **async/await** para todas as operacoes de I/O (BD, HTTP, filesystem)
6. **Secrets via env vars** — nenhuma credencial em codigo ou arquivos versionados
7. **Try/except tipado** — capturar excecoes especificas, nunca `except Exception` nuo
8. **git status** apos cada commit — validar que nao ha arquivos esquecidos
9. **Testes obrigatorios** — todo novo modulo/endpoint exige testes; PRs sem testes sao bloqueados
10. **Conventional Commits** — `feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `test:`

---

## 8. Cuidados Especificos do Servidor itgov-dev

- **NAO** modificar configuracoes do Zabbix Agent sem autorizacao explicita do time de infra
- **NAO** reiniciar o servico `zabbix-server` sem aviso previo — afeta monitoramento de producao
- Logs do Zabbix em `/var/log/zabbix/` — apenas leitura
- Hotfixes direto em `main` exigem aprovacao conforme ADR-002 (processo de verificacao)
- Servicos Docker em producao: parar/reiniciar apenas apos confirmar janela de manutencao

---

## 10. Skills do Claude Code Recomendadas

> Auditado em 2026-09-22: `kubernetes-specialist`, `devops-engineer`,
> `postgres-pro`, `database-optimizer` e `monitoring-expert` estao em
> `~/.claude/skills/` (fonte: marketplace de terceiros `jeffallan/claude-skills`, MIT).
> `zabbix-api`, `grafana` e `git` sao versionadas no repo em `.claude/skills/` (PR #224).
> Nao existe skill para InfluxDB — usar Flux diretamente.

| Skill                    | Quando usar                                               |
|---------------------------|-----------------------------------------------------------|
| `zabbix-api`              | Hosts, triggers, itens, eventos via JSON-RPC               |
| `grafana`                 | Dashboards, datasources e alertas via HTTP API             |
| `git`                     | Rebase, worktrees, reflog, recuperacao de historico        |
| `kubernetes-specialist`   | Manifests, Helm, RBAC, NetworkPolicy, GitOps               |
| `devops-engineer`         | Dockerfiles, CI/CD, Terraform, deploy, runbooks de incidente |
| `postgres-pro`            | EXPLAIN ANALYZE, JSONB, replicacao, VACUUM (Postgres do Zabbix) |
| `database-optimizer`      | Queries lentas, indices, particionamento                   |
| `monitoring-expert`       | Dashboards Prometheus/Grafana, alerting, tracing            |
| `code-review`             | Review de PRs com niveis de profundidade (low/ultra)        |
| `security-review`         | Auditoria de seguranca antes de merges em main              |
| `python-testing-patterns` | pytest, fixtures, mocking, TDD                              |
| `linux-backup-dr-rclone`  | Backup local + sync externo (usado em `scripts/backup_governanca.sh`) |
