## What

Implementa o Hero Module completo (Sprint 10/11): CRUD de Fornecedores e Contratos sobre TimescaleDB + módulo de métricas SLA sobre InfluxDB v2.

**Endpoints entregues (13 no total):**

| Método | Rota | Descrição |
|--------|------|-----------|
| `GET` | `/api/v1/fornecedores/` | Listagem paginada com filtros |
| `POST` | `/api/v1/fornecedores/` | Cadastro com validação CNPJ (módulo-11) |
| `GET` | `/api/v1/fornecedores/{id}` | Detalhe |
| `PATCH` | `/api/v1/fornecedores/{id}` | Atualização parcial |
| `DELETE` | `/api/v1/fornecedores/{id}` | Soft delete |
| `GET` | `/api/v1/fornecedores/{id}/contratos` | Sub-resource contratos |
| `GET` | `/api/v1/contratos/` | Filtros: status, fornecedor_id, criticidade, vigente_em |
| `POST` | `/api/v1/contratos/` | Cria contrato (validação cross-field Pydantic v2) |
| `GET` | `/api/v1/contratos/{id}` | Detalhe com fornecedor embedded |
| `PATCH` | `/api/v1/contratos/{id}` | Atualização parcial |
| `DELETE` | `/api/v1/contratos/{id}` | Soft delete |
| `POST` | `/api/v1/metricas/sla` | Ingestão InfluxDB (measurement: sla_uptime) |
| `GET` | `/api/v1/metricas/sla/{id}?range=7d&window=1h` | Série temporal |
| `GET` | `/api/v1/metricas/dashboard?range=30d` | Uptime médio + top-5 contratos em risco |

## Why

Sprint 10/11 do roadmap IT Governance Dashboard.  
O módulo Hero (Fornecedores + Contratos) é o núcleo que alimenta SLA, alertas de vencimento e governança. Sem ele os módulos de Service Desk e Hub de Governança não têm contexto de negócio.

A camada InfluxDB viabiliza o dashboard executivo com séries temporais de uptime sem degradar o TimescaleDB com queries analíticas em alta frequência.

## How

**Stack e padrões:**
- Pydantic v2 para validação (CNPJ módulo-11, cross-field `data_fim > data_inicio`, enums estritos)
- SQLAlchemy 2.x async (`asyncpg`) + `asyncio.run()` no padrão Flask-RESTX
- Soft delete (`deleted_at`) em todas as entidades
- `structlog` com bind de IDs em cada operação
- `from __future__ import annotations` para evitar shadowing de built-in `list` por métodos homônimos

**Alembic migração `0002_contratos_v2`:**
- `DROP COLUMN valor_anual` (GENERATED) + renomeia colunas + adiciona `criticidade`, `numero_contrato` (UNIQUE)
- Índices em `(fornecedor_id, status)`, `deleted_at`, `(data_inicio, data_fim)`, `criticidade`
- `upgrade` + `downgrade` completos

**InfluxDB:**
- Bucket `metrics` criado com retention 2160h (90d)
- Token scoped read+write ao bucket
- Retry exponencial (0.5s→1s→2s) com detecção de erros permanentes (4xx)
- Dashboard join: Flux (avg uptime por contrato) ⊕ PostgreSQL (`sla_uptime_pct`) em Python via `asyncio.gather`

**docker/docker-compose.yml** atualizado com serviço `influxdb:2.7` e init automático (org/bucket/token via env).

## Tests

```
185 passed | 0 failed | 97.26% coverage

Módulos-chave:
  app/services/influx_client.py     98%   (era 0% antes desta PR)
  app/api/v1/contratos.py           97%
  app/api/v1/metricas.py            99%
  app/models/contrato.py            98%
  app/models/metrica.py            100%
  app/services/contrato_service.py  99%
```

**Testes notáveis:**
- `test_write_retries_on_transient_error` — corrige 3 problemas do teste original proposto: alvo de patch (WHERE USED não WHERE DEFINED), nome da classe (`InfluxService` não `InfluxClient`), instâncias vs classes na `side_effect`
- `test_exponential_backoff_delays` — verifica delays 0.5s e 1.0s explicitamente via `call_args_list`
- `test_permanent_error_raises_immediately` — garante que 401 não dispara retry nem sleep
- Testes de integração usam banco real (TimescaleDB) dentro de transações rollback

## Tech Debt registrado

Ver `TECH_DEBT.md` na raiz:
- **TD-001** Paginação cursor-based (3 SP) — offset atual degrada em > 10k registros
- **TD-002** `POST /fornecedores/{id}/restore` (1 SP) — reativação após soft delete
- **TD-003** Rate limiting em POSTs (2 SP) — `Flask-Limiter` + Redis backend

---

🤖 Generated with [Claude Code](https://claude.ai/claude-code)
