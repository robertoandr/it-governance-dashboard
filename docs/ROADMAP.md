# IT Governance Dashboard — Roadmap

## Visão do Projeto

O IT Governance Dashboard centraliza a gestão de fornecedores, contratos, indicadores de infraestrutura e chamados de TI em uma única plataforma interna. O sistema combina uma camada de negócio em Flask (Fornecedores, Contratos, Hub de Governança, Service Desk) com dashboards de observabilidade embedados via Grafana, ambos servidos por um gateway Nginx com autenticação Microsoft Entra ID. O armazenamento é unificado em PostgreSQL com extensão TimescaleDB, eliminando a complexidade do modelo dual-store original (SQLite + InfluxDB).

---

## Sprints

| Sprint | Período | Objetivo | Entregáveis |
|--------|---------|----------|-------------|
| **Sprint 7** | Semanas 1-2 | Foundation & Infrastructure | Setup Docker Compose (PostgreSQL + TimescaleDB + Grafana + Nginx), estrutura Flask[async], `config.py`, `conftest.py`, pipeline CI básico |
| **Sprint 8** | Semanas 3-4 | Flask Shell & Grafana Embed | Flask CLI shell funcional, rota `/health`, embed Grafana kiosk via iframe, ADRs 0001-0005 finalizados, schema inicial TimescaleDB |
| **Sprint 9** | Semanas 5-6 | ⭐ Fornecedores — CRUD Core | Modelo `Fornecedor` (Pydantic v2), endpoints REST `/api/v1/fornecedores`, CRUD completo, validações CNPJ, paginação, testes unitários >80% |
| **Sprint 10** | Semanas 7-8 | ⭐ Contratos — CRUD + Alertas | Modelo `Contrato`, endpoints `/api/v1/contratos`, hypertable `contratos_eventos`, alertas de vencimento (job agendado), cálculo `valor_anual`, testes de integração |
| **Sprint 11** | Semanas 9-10 | Hub de Governança — Estrutura | Blueprint `hub`, agregados KPI (contratos ativos, vencendo em 30/60/90 dias, total mensal), queries TimescaleDB, endpoints `/api/v1/hub/kpis` |
| **Sprint 12** | Semanas 11-12 | Score Real & Continuous Aggregates | Score de saúde do fornecedor calculado a partir de eventos reais, continuous aggregates TimescaleDB para SLA histórico, painel Grafana provisionado |
| **Sprint 13** | Semanas 13-14 | Service Desk — Zendesk Integration | Blueprint `service_desk`, cliente Zendesk API, sincronização de tickets por fornecedor, endpoints `/api/v1/tickets`, cache Redis para rate-limit Zendesk |
| **Sprint 14** | Semanas 15-16 | Service Desk — Hub Integration | Tickets linkados a contratos, SLA de atendimento calculado, alertas de SLA breach em `contratos_eventos`, relatório de chamados por fornecedor |
| **Sprint 15** | Semanas 17-18 | Endpoints & Autenticação | OAuth2 Microsoft Entra ID completo, RBAC middleware em todos os blueprints, endpoints MS Graph (M365 health), auditoria de acesso a dados pessoais (LGPD) |
| **Sprint 16** | Semanas 19-20 | Polimento & Produção | Performance profiling, índices TimescaleDB otimizados, job de anonimização LGPD, runbook de breach notification, documentação OpenAPI final, deploy Kubernetes |

---

## Módulo Hero: Fornecedores & Contratos (Sprints 9-10)

Os Sprints 9 e 10 são os mais críticos do roadmap. Eles entregam o núcleo de valor do sistema — sem uma gestão sólida de fornecedores e contratos, os módulos subsequentes (Hub, Service Desk) não têm base de dados real para operar.

### Sprint 9 — Fornecedores

**Objetivo:** CRUD completo e validado de fornecedores.

**Entregáveis:**
- Tabela `fornecedores` (schema em `db/migrations/001_fornecedores_contratos.sql`)
- Pydantic model `FornecedorCreate`, `FornecedorUpdate`, `FornecedorResponse`
- Service `FornecedorService` com métodos `create`, `get`, `list`, `update`, `soft_delete`
- Endpoints Flask-RESTX:
  - `GET    /api/v1/fornecedores` — lista paginada, filtrável por `status` e `categoria`
  - `POST   /api/v1/fornecedores` — cria fornecedor
  - `GET    /api/v1/fornecedores/{id}` — detalhe
  - `PUT    /api/v1/fornecedores/{id}` — atualização completa
  - `PATCH  /api/v1/fornecedores/{id}` — atualização parcial
  - `DELETE /api/v1/fornecedores/{id}` — soft delete (seta `deleted_at`)
- Validação de CNPJ (formato e dígitos verificadores)
- Testes unitários (service) e de integração (endpoints) com cobertura > 80%

**Critério de aceite:** `pytest tests/test_fornecedores.py` passa com cobertura declarada.

### Sprint 10 — Contratos

**Objetivo:** CRUD de contratos vinculado a fornecedores, com eventos de ciclo de vida.

**Entregáveis:**
- Tabela `contratos` e hypertable `contratos_eventos`
- Pydantic models `ContratoCreate`, `ContratoResponse` (inclui `valor_anual` calculado)
- Service `ContratoService` com validação de datas (`data_fim > data_inicio`)
- Job agendado `check_contract_expiry` — insere evento `alerta_vencimento` em `contratos_eventos` para contratos vencendo em ≤30 dias
- Endpoint `GET /api/v1/contratos?vencendo_em_dias=30` com filtro eficiente via índice `idx_contratos_data_fim`
- Testes de integração cobrindo: criação, vencimento, soft delete, integridade referencial

**Critério de aceite:** `pytest tests/test_contratos.py` passa; job de vencimento insere eventos corretamente em banco de teste.

---

## Dependências entre Sprints

```
Sprint 7-8 (Foundation)
    └── Sprint 9 (Fornecedores) ← BLOCKER para todos os módulos seguintes
            └── Sprint 10 (Contratos) ← BLOCKER para Hub e Service Desk
                    ├── Sprint 11-12 (Hub + Score)
                    └── Sprint 13-14 (Service Desk)
                            └── Sprint 15-16 (Endpoints + Produção)
```

---

## Stack de Referência

| Componente | Tecnologia | Versão mínima |
|------------|------------|---------------|
| Runtime | Python | 3.11 |
| Framework | Flask[async] | 3.x |
| API | Flask-RESTX | 1.x |
| Validação | Pydantic | v2 |
| ORM | SQLAlchemy (async) | 2.x |
| Driver | asyncpg | latest |
| Banco | PostgreSQL | 16 |
| Time-series | TimescaleDB | 2.x |
| Observabilidade | Grafana OSS | 10.x |
| Gateway | Nginx | 1.25+ |
| Logging | structlog | latest |
| Auth | Microsoft Entra ID (OAuth2) | — |
| Testes | pytest + pytest-asyncio | latest |

---

## Métricas de Sucesso

- Sprint 9-10 entregues sem dívida técnica detectável no `code-review ultra`.
- Cobertura de testes ≥ 80% nos módulos Fornecedores e Contratos.
- Zero secrets hardcoded (validado pelo pre-commit hook `detect-secrets`).
- Score de SLA calculável a partir de dados reais até o fim do Sprint 12.
- Deploy em Kubernetes funcional com rollout zero-downtime até Sprint 16.
