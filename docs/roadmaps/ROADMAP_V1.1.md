# Roadmap V1.1 — IT Governance Dashboard

**Versao:** draft-2
**Periodo:** 02/06/2026 → 30/06/2026
**Status:** Em execucao (Sprint 11 iniciada em 02/06/2026)
**Responsavel:** Roberto Andrade (TI Interno — Grupo Gadens)

---

## Triagem e Limpeza Pre-V1.1 (02/06/2026)

Antes do inicio formal do ciclo V1.1, foi executada triagem completa do milestone legado:

| Milestone | Status | Acao |
|-----------|--------|------|
| #1 v0.2.0 Integration Layer | Arquivado | 7 issues fechadas (escopo entregue em V1.0) |
| #2 V1.1 - Go-Live | Ativo | Sprint 11-14 (02/06 → 30/06) |

**Issues triadas:** #27, #28, #29, #30, #31, #32, #34 — fechadas como "not planned"
**Issue #33 (APScheduler):** movida para backlog V1.2 (nao implementada no codebase)
**Justificativa:** Funcionalidades entregues por outras rotas durante Sprints 1-10 (commit base `b91bd19`).

---

## Visao Geral

A V1.1 adiciona tres capacidades novas ao nucleo de Fornecedores e Contratos entregue na V1.0:

1. **Inventario de Ativos de TI** — modelo `Ativo`, CRUD completo, vinculo com fornecedores/contratos
2. **Governanca Microsoft 365** — 6 pilares (Identidade, Dispositivos, Dados, Apps, Infra, Compliance), integracao com Microsoft Graph, Secure Score e MFA tracking
3. **Hub SharePoint + Alertas SMTP** — centralizacao de documentos e 8 triggers de notificacao proativos

---

## Baseline V1.0 → Meta V1.1

| Indicador            | V1.0 (baseline)  | Meta V1.1       | Criterio de aceite                    |
|----------------------|------------------|-----------------|---------------------------------------|
| Testes               | 215              | >= 350          | `pytest` verde no CI                  |
| Coverage             | 88.21%           | >= 90%          | `--fail-under=90` no CI               |
| Secure Score M365    | (nao medido)     | >= 60%          | Graph API reporting                   |
| MFA administradores  | (nao medido)     | 100%            | Zero admins sem MFA no relatorio       |
| Triggers SMTP ativos | 0                | 8               | Todos testados em staging             |
| ADRs formalizadas    | 6                | 8               | ADR-001 e ADR-002 mergeadas           |
| Issues P3 residuais  | 4 (#73-#78)      | 0               | Todas fechadas ou aceitas como wont-fix|

---

## Sprint 11 — Debito Tecnico + Inventario de Ativos

**Periodo:** 02/06/2026 → 08/06/2026
**Objetivo:** Zerar debito tecnico P0/P1 da V1.0 e entregar a fundacao do modulo Ativos.

### Entregas

| # | Entrega                                              | Issue | Tipo       |
|---|------------------------------------------------------|-------|------------|
| 1 | ADR-001: Coverage Policy (piso 85%, alvo 90%)        | #73   | docs       |
| 2 | ADR-002: Verification Rigor (PR checklist)           | #74   | docs       |
| 3 | Refactor `collectors/_svc` → `itgov.services`        | #76   | refactor   |
| 4 | Audit e correcao de `base_url` em todos os clientes  | #78   | fix        |
| 5 | Model `Ativo` (Pydantic v2 + SQLModel)               | —     | feat       |
| 6 | Migration Alembic `002_ativos`                       | —     | feat       |
| 7 | CRUD `/api/v1/ativos` (POST/GET/PATCH/DELETE)         | —     | feat       |
| 8 | Testes unitarios e de integracao para modulo Ativos  | —     | test       |

### KPIs Sprint 11

- Coverage >= 88.5% ao final (nao regredir)
- ADR-001 e ADR-002 mergeadas em `main`
- CRUD `/ativos` com >= 90% de coverage individual
- Issues #73, #74, #76, #78 fechadas

---

## Sprint 12 — Governanca M365 (6 Pilares)

**Periodo:** 09/06/2026 → 22/06/2026
**Objetivo:** Painel completo de Governanca Microsoft 365 com dados reais via Graph API.

### Entregas

| # | Entrega                                              | Tipo    |
|---|------------------------------------------------------|---------|
| 1 | Abas de navegacao: Planos / 6 Pilares / Checklist    | feat    |
| 2 | Integracao Microsoft Graph (autenticacao + coleta)   | feat    |
| 3 | Pilar Identidade: usuarios, MFA status, grupos       | feat    |
| 4 | Pilar Dispositivos: compliance, Intune enrollment    | feat    |
| 5 | Pilar Dados: DLP policies, sensitivity labels        | feat    |
| 6 | Pilar Aplicativos: app registrations, permissions    | feat    |
| 7 | Pilar Infraestrutura: VMs, storage, security center  | feat    |
| 8 | Pilar Compliance: Secure Score, recomendacoes ativas | feat    |
| 9 | Cache InfluxDB 6h para metricas M365 (evitar throttle)| feat   |
| 10| Grafana embedado: dashboard Governanca M365          | feat    |

### KPIs Sprint 12

- Secure Score coletado e exibido via Graph API
- MFA tracking funcional (% de admins com MFA ativo)
- Cache InfluxDB reduzindo chamadas Graph em >= 80%
- Coverage global mantida >= 89%

---

## Sprint 13 — Hub SharePoint + 8 Triggers SMTP

**Periodo:** 23/06/2026 → 27/06/2026
**Objetivo:** Centralizar documentos no Hub SharePoint e ativar alertas proativos por e-mail.

### Entregas

| # | Entrega                                              | Tipo  |
|---|------------------------------------------------------|-------|
| 1 | Hub SharePoint: listagem e preview de documentos     | feat  |
| 2 | Integracao Graph API para bibliotecas de documentos  | feat  |
| 3 | Engine de triggers SMTP (scheduler + template Jinja2)| feat  |
| 4-11 | 8 triggers configurados (ver tabela abaixo)       | feat  |

### 8 Triggers SMTP

| # | Trigger                          | Condicao de disparo                            | Destinatarios      |
|---|----------------------------------|------------------------------------------------|--------------------|
| 1 | Contrato vence em 30 dias        | `data_vencimento - hoje <= 30`                 | Gestor + TI        |
| 2 | Contrato vence em 7 dias         | `data_vencimento - hoje <= 7`                  | Gestor + TI + Dir  |
| 3 | SLA breach detectado             | `sla_compliance < sla_target`                  | TI + Service Desk  |
| 4 | Ativo sem contrato vinculado     | `ativo.contrato_id IS NULL`                    | TI                 |
| 5 | Coverage abaixo do piso          | `coverage < 88%` (build CI falhou)             | Dev team           |
| 6 | Secure Score abaixo do limite    | `secure_score < 50%`                           | TI + Seguranca     |
| 7 | Admin sem MFA                    | `is_admin AND NOT mfa_enabled`                 | TI + Seguranca     |
| 8 | Job de coleta falhou 3x seguidas | `consecutive_failures >= 3`                    | TI (oncall)        |

### KPIs Sprint 13

- 8 triggers testados em staging com e-mails de teste enviados
- Hub SharePoint listando documentos reais do tenant
- Coverage global >= 89.5%

---

## Sprint 14 — Polimento + Go-Live

**Periodo:** 28/06/2026 → 30/06/2026
**Objetivo:** Validacao final, testes E2E, empacotamento e publicacao da V1.1.

### Entregas

| # | Entrega                                              | Tipo   |
|---|------------------------------------------------------|--------|
| 1 | Suite E2E com Playwright (fluxos criticos)           | test   |
| 2 | Manifests Kubernetes em `k8s/v1.1/`                  | ops    |
| 3 | User guide V1.1 em `docs/user-guide-v1.1.md`         | docs   |
| 4 | CHANGELOG atualizado com todas as entregas V1.1      | docs   |
| 5 | Tag `v1.1.0` no `main` + GitHub Release              | chore  |
| 6 | Deploy validado em itgov-dev                         | ops    |

### KPIs Sprint 14

- Todos os testes (unit + integracao + E2E) verdes no CI
- Coverage >= 90% confirmada
- Tag `v1.1.0` publicada com release notes
- Sem issues P0/P1 abertas

---

## Criterios de Aceite Globais V1.1

- [ ] `pytest --cov=itgov --fail-under=90` verde no CI
- [ ] `ruff check .` sem erros
- [ ] Nenhuma issue P0 ou P1 em aberto
- [ ] ADR-001 e ADR-002 mergeadas em `main`
- [ ] 8 triggers SMTP testados e documentados
- [ ] Secure Score >= 60% coletado via Graph API
- [ ] MFA 100% para contas administradoras
- [ ] Manifests `k8s/v1.1/` revisados e aplicados
- [ ] Tag `v1.1.0` com GitHub Release publicada
- [ ] User guide atualizado para V1.1

---

## Referencias

- [ADR-0001](../adr/0001-dual-storage-sqlite-influxdb.md) — Dual Storage (ativa)
- [ADR-001](../adr/ADR-001-coverage-policy.md) — Coverage Policy
- [ADR-002](../adr/ADR-002-verification-rigor.md) — Verification Rigor
- [ADR-0004](../adr/0004-timescaledb-single-storage.md) — TimescaleDB (SUPERSEDED)
- [ADR-0010](../adr/0010-frontend-strategy.md) — Frontend Strategy
- Issues relacionadas: #73, #74, #76, #78

---

*Documento gerado em 02/06/2026 — Revisao draft-2*
