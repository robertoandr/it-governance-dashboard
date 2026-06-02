# ADR-0010: Frontend Strategy — Grafana agora, custom depois

## Status: Aceito

## Contexto

Com os coletores Zabbix e GitHub gravando em InfluxDB (`governance_raw`),
é necessária uma camada de visualização. As opções avaliadas:

1. **Grafana** — open-source, suporte nativo a InfluxDB/Flux, provisioning via YAML/JSON
2. **React custom** — flexibilidade máxima, custo de desenvolvimento alto
3. **Metabase** — BI focado em SQL, suporte a Flux limitado

## Decisão

**Grafana com dashboards-as-code** para Sprint 10.

Dashboards armazenados como JSON versionado em `grafana/dashboards/`.
Provisioning 100% automático via YAML — zero click-ops, reproduzível em
qualquer ambiente.

React custom dashboards reavaliados em Sprint 13+ após validação
dos dashboards Grafana com stakeholders.

## Arquitetura

```
grafana/
├── provisioning/
│   ├── datasources/influxdb.yml   # InfluxDB auto-config
│   └── dashboards/default.yml     # auto-load provider
└── dashboards/
    ├── 00-executive-overview.json
    ├── 01-infrastructure-health.json
    └── 02-engineering-metrics.json
```

### Workflow de edição (dashboards-as-code)

1. Editar via UI Grafana (`http://localhost:3000`)
2. Dashboard Settings → JSON Model → copiar tudo
3. Salvar em `grafana/dashboards/<nome>.json`
4. Commitar: `feat(grafana): update <nome> dashboard`

> ⚠️ **NUNCA** editar o JSON diretamente — sempre exportar da UI
> para garantir schema válido e UIDs consistentes.

## Convenções de dashboards

### UIDs (obrigatório, estável entre ambientes)
- `itgov-exec` — Executive Overview
- `itgov-infra` — Infrastructure Health
- `itgov-eng` — Engineering Metrics

### Variables (padrão entre dashboards)
| Variável | Tipo | Fonte |
|---|---|---|
| `$severity` | Custom | not_classified,information,warning,average,high,disaster |
| `$repo` | Query | `schema.tagValues(bucket:"governance_raw", tag:"repo")` |
| `$state` | Custom | open,resolved |

### Token InfluxDB
Grafana usa token **read-only** dedicado (`INFLUX_TOKEN_GRAFANA_RO`),
distinto do token de escrita dos coletores. Princípio do least privilege.

## Trade-offs

| | |
|---|---|
| ✅ | Provisioning automático — zero setup manual |
| ✅ | JSON versionado → reproducível, auditável, rollback |
| ✅ | InfluxDB Flux nativo → queries diretas nos dados coletados |
| ✅ | Alerting integrado (Sprint 11) |
| ❌ | Customização visual limitada vs React |
| ❌ | Grafana não é substituto para relatório PDF para diretoria |

## Alternativas Rejeitadas

**React custom (Sprint 10):** Estimativa de 4 semanas para MVP. Stakeholders
precisam de dados esta semana. Grafana entrega em 2 dias.

**Metabase:** Suporte a Flux é experimental. Bucket `governance_raw` usa
schema de séries temporais não compatível com SQL nativo do Metabase.
