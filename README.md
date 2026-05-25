# IT Governance Dashboard v2.0

Dashboard de Governança de TI — **Grafana + TimescaleDB**.

## Arquitetura

- TimescaleDB (Postgres 16 + extensão time-series): única fonte de dados
- Grafana 11.3 OSS: visualização e alerting
- Python 3.12 + APScheduler: collectors
- Nginx 1.27: reverse proxy + TLS

## Quick Start

```bash
cd /opt/it-gov-dashboard

# Conferir .env (já gerado com senhas aleatórias)
cat .env

# IMPORTANTE: editar GITHUB_TOKEN antes de subir
nano .env

# Subir stack
docker compose up -d

# Acompanhar logs
docker compose logs -f collector
```

## Estrutura

```
/opt/it-gov-dashboard/
├── docker-compose.yml
├── .env.example
├── .gitignore
├── docs/governance/        # Políticas e inventário de secrets
├── db/postgres/            # Migrations SQL (TimescaleDB)
├── collectors/             # Python collectors (APScheduler)
├── grafana/provisioning/   # Datasources e dashboards provisionados
└── nginx/                  # Reverse proxy + TLS
```

## Políticas

- `docs/governance/POLICY-PAT.md` — Política de PATs
- `docs/governance/POLICY-SECRETS-MGMT.md` — Política de Secrets
- `docs/governance/SECRETS-INVENTORY.md` — Inventário ativo
