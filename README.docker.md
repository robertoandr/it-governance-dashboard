# Docker — InfluxDB (portabilidade)

> **Operador:** substitua `<ITGOV_DEV_IP>` pelo IP real do servidor de dev
> (definido em `.env` local ou runbook — nunca versionado aqui).

## Contexto

No servidor **itgov-dev** (`<ITGOV_DEV_IP>`) existe um InfluxDB nativo (systemd) em `:8086`,
compartilhado com Grafana e outras stacks. Este compose **não é usado em dev** — serve
exclusivamente para reproduzir o ambiente em outro host.

## Quando usar

- Novo servidor sem InfluxDB nativo instalado
- CI/CD que precisa de ambiente isolado
- Testes de integração em ambiente efêmero

## Como usar

```bash
# Copie e preencha as variáveis
cp .env.example .env
# Edite .env com INFLUX_TOKEN, INFLUX_ADMIN_PASSWORD, etc.

# Produção (sem porta exposta no host)
docker compose up -d

# Dev (porta 8087 exposta — 8086 reservada ao nativo)
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d

# Bootstrap de buckets e tasks (roda uma vez e sai)
# influxdb-init executa automaticamente após healthcheck OK
docker compose logs influxdb-init
```

## Arquitetura 3 buckets

| Bucket | Retention | Uso |
|---|---|---|
| `governance_raw` | 90 dias | Escrita do app (1–5 min) |
| `governance_hourly` | 1 ano | Queries de dashboards operacionais |
| `governance_monthly` | infinito | Relatórios históricos |

## Flux tasks (auto-criadas pelo init)

| Task | Schedule | Fonte → Destino |
|---|---|---|
| `governance_downsample_hourly` | 1h (offset 10m) | raw → hourly |
| `governance_downsample_monthly` | 1d (offset 6h) | hourly → monthly |

## Token em produção real

O token `INFLUX_TOKEN` no servidor itgov-dev é compartilhado com:
- Grafana datasource (`/etc/grafana/provisioning/datasources/influxdb.yml`)
- Stack `/opt/it-gov-dashboard/`

Ao provisionar um novo servidor, gere um token **dedicado** com escopo restrito
aos 3 buckets de governança. O app valida isso no startup via
`app/storage/influxdb/guards.py`.
