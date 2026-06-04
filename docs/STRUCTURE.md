# IT Governance Dashboard — Estrutura da Fundação

## 5 Pilares de Governança (COBIT-aligned)

| # | Pilar | ID | Peso | Cor |
|---|---|---|---|---|
| 1 | Alinhamento Estratégico | `strategic_alignment` | 15% | #3B82F6 |
| 2 | Entrega de Valor | `value_delivery` | 20% | #10B981 |
| 3 | Gestão de Riscos | `risk_management` | 25% | #EF4444 |
| 4 | Gestão de Recursos | `resource_management` | 15% | #F59E0B |
| 5 | Mensuração de Desempenho | `performance_measure` | 25% | #8B5CF6 |

## Fórmula do Score

```
global_score = Σ(pillar_score × weight)
```

**Status:**
- `>= 85` → OPERACIONAL
- `60–84` → DEGRADADO
- `< 60` → CRÍTICO

## Arquitetura

```
┌────────────────────────────────────────────────────────────┐
│                    Flask App Factory                        │
│                    app/__init__.py                          │
│                    create_app(settings)                     │
└─────────┬──────────────────────────┬───────────────────────┘
          │                          │
   ┌──────▼──────┐           ┌───────▼──────┐
   │ Flask-RESTX  │           │  Blueprints  │
   │  /api/...   │           │  HTML views  │
   └──────┬──────┘           └───────┬──────┘
          │                          │
   ┌──────▼──────────────────────────▼──────┐
   │         MetricsAggregator               │
   │  asyncio.gather(5 pillar collectors)    │
   └──────────────────┬─────────────────────┘
                      │
   ┌──────────────────▼─────────────────────┐
   │         MockMetricsProvider             │
   │  (→ real collectors in future sprints)  │
   └──────────────────┬─────────────────────┘
                      │
   ┌──────────────────▼─────────────────────┐
   │         ScoreCalculator                 │
   │  calculate_pillar() + calculate_global()│
   └──────────────────┬─────────────────────┘
                      │
   ┌──────────────────▼─────────────────────┐
   │  Pydantic Models: ComponentMetric       │
   │                   PillarScore           │
   │                   GovernanceScore       │
   └────────────────────────────────────────┘
```

## Stack Técnica

| Camada | Tecnologia |
|---|---|
| Framework | Flask 3.x + Flask-RESTX |
| Validação | Pydantic v2 + pydantic-settings |
| Logging | structlog (JSON em prod, console em dev) |
| Banco | SQLite (dev) / PostgreSQL (prod) |
| Frontend | Jinja2 + TailwindCSS CDN + Alpine.js + Chart.js |
| Testes | pytest + pytest-cov + pytest-flask |
| Lint | ruff |

## Como Rodar Localmente

```bash
# 1. Instalar dependências
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

# 2. Copiar e configurar env
cp .env.example .env
# Editar .env conforme necessário

# 3. Popular banco
python -m data.seed --reset

# 4. Iniciar app
python wsgi.py
# App disponível em http://localhost:5000

# 5. Testes
pytest tests/test_score_calculator.py tests/test_metrics_aggregator.py tests/test_api_overview.py -v
```

## Estrutura de Pastas

```
app/
├── __init__.py              # Factory: create_app()
├── config.py                # Pydantic Settings (nested, env-file)
├── api/
│   ├── dashboards.py        # GET /api/overview (cache 25s)
│   └── pillars.py           # GET /api/pillars, /api/pillars/<id>
├── models/
│   └── governance.py        # PillarID, ComponentMetric, PillarScore, GovernanceScore
├── services/
│   ├── db.py                # SQLite lifecycle (before_request / teardown)
│   ├── score_calculator.py  # ScoreCalculator (weighted average)
│   ├── metrics_aggregator.py# MetricsAggregator (async gather)
│   └── mock_data.py         # MockMetricsProvider (seed=42)
├── views/
│   └── dashboards.py        # HTML routes: /, /pillars, /pillars/<id>
├── templates/
│   ├── base.html
│   ├── partials/{sidebar,topbar}.html
│   ├── dashboards/{overview,pillars,pillar_detail}.html
│   └── errors/{404,500}.html
└── utils/
    └── logging.py           # configure_logging(level, fmt)
data/
├── schema.sql               # DDL idempotente (CREATE TABLE IF NOT EXISTS)
├── seed.py                  # Seed 30 dias histórico + dados mestres
└── govti.db                 # Gerado pelo seeder
tests/
├── conftest.py              # Fixtures legadas + factory_app/factory_client
├── test_score_calculator.py # 19 testes
├── test_metrics_aggregator.py # 11 testes
└── test_api_overview.py     # 19 testes
```

## Endpoints

| Método | URL | Descrição |
|---|---|---|
| GET | `/health` | Health check |
| GET | `/api/overview` | Score global completo (cache 25s) |
| GET | `/api/pillars` | Lista todos os 5 pilares |
| GET | `/api/pillars/<id>` | Detalhe de um pilar |
| GET | `/api/docs` | Swagger UI |
| GET | `/` | Dashboard overview (HTML) |
| GET | `/pillars` | Todos pilares expansíveis (HTML) |
| GET | `/pillars/<id>` | Drill-down pilar (HTML) |
