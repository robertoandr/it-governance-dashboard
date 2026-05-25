# IT Governance Dashboard

Plataforma de monitoramento de métricas, SLA e compliance para TI.

**Stack:** Python 3.11 · Flask · Flask-RESTX · Pydantic v2 · InfluxDB v2 · structlog

## Pré-requisitos

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) — gerenciador de pacotes

```bash
pip install uv
```

## Setup local

```bash
# 1. Clone e entre no diretório
git clone https://github.com/robertoandr/it-governance-dashboard.git
cd it-governance-dashboard

# 2. Instale as dependências (cria .venv automaticamente)
uv sync --group dev

# 3. Configure as variáveis de ambiente
cp .env.example .env
# Edite .env com os valores reais

# 4. Instale os git hooks
uv run pre-commit install
```

## Comandos úteis

```bash
uv run pytest                    # rodar testes
uv run ruff check app/ tests/    # lint
uv run ruff format app/ tests/   # format
uv run mypy app/                 # type check
uv run bandit -r app/            # security scan
```

## Estrutura

```
app/
├── api/        # Endpoints Flask-RESTX
├── models/     # Pydantic models
├── services/   # Lógica de negócio
└── utils/      # Helpers
docker/         # Dockerfile + docker-compose
k8s/            # Manifests Kubernetes
tests/          # pytest
```

## Variáveis de ambiente

Veja `.env.example` para a lista completa. Nunca commite o arquivo `.env`.
