# M365 Collector — PromQL Reference

Prometheus endpoint: `http://172.29.2.11:9091`

All metrics use the prefix `itgov_` as exported by the OTel Collector
(namespace `itgov` configured in `otel-collector/config.yaml`).

> **Naming note:** OTel metric names (`m365_collections_total`) are
> exported to Prometheus with the configured namespace prefix:
> `itgov_m365_collections_total`. Adjust the queries below accordingly
> when the app is sending real metrics.

---

## Collection health

### Rate de coletas por minuto
```promql
rate(itgov_m365_collections_total[5m]) * 60
```

### Taxa de erro (error / total)
```promql
sum(rate(itgov_m365_collections_total{status="error"}[10m]))
  /
sum(rate(itgov_m365_collections_total[10m]))
```

### Collector DOWN (zero coletas nos últimos 30 min)
```promql
sum(increase(itgov_m365_collections_total[30m])) == 0
```

---

## Graph API latência

### Latência p50 de requests ao Graph API
```promql
histogram_quantile(0.50,
  sum by (le, tenant) (
    rate(itgov_m365_graph_request_duration_seconds_bucket[5m])
  )
)
```

### Latência p95 por endpoint
```promql
histogram_quantile(0.95,
  sum by (le, endpoint) (
    rate(itgov_m365_graph_request_duration_seconds_bucket[5m])
  )
)
```

### Latência p99 global
```promql
histogram_quantile(0.99,
  sum(rate(itgov_m365_graph_request_duration_seconds_bucket[5m])) by (le)
)
```

---

## Graph API erros

### Taxa de erro Graph API por tipo
```promql
sum by (error_type) (
  rate(itgov_m365_graph_errors_total[5m])
)
```

### Rate limit spikes (erros de rate_limit por segundo)
```promql
rate(itgov_m365_graph_errors_total{error_type="rate_limit"}[1m])
```

### Taxa de erro total (errors / requests)
```promql
sum(rate(itgov_m365_graph_errors_total[10m]))
  /
sum(rate(itgov_m365_graph_requests_total[10m]))
```

---

## Governança — SPs em risco

### Total de SPs críticos no momento (último valor)
```promql
itgov_m365_sps_risk_count{risk_level="critical"}
```

### Total de órfãos acionáveis por tenant
```promql
itgov_m365_sps_orphans_actionable
```

### Tendência de críticos nas últimas 24h
```promql
itgov_m365_sps_risk_count{risk_level="critical"}
  offset 24h
```

### Distribuição de risco (todos os níveis)
```promql
sum by (risk_level) (itgov_m365_sps_risk_count)
```
