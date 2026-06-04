# Observability Troubleshooting Runbook

Stack: OTel Collector → Tempo (traces) + Prometheus (metrics) + Loki (logs) + Grafana

---

## 1. Containers da stack

```bash
docker compose -f docker/observability/docker-compose.observability.yml ps
# ou
make obs-status
```

Portas esperadas no host:

| Serviço          | Porta host | Porta interna |
|------------------|-----------|--------------|
| OTel Collector gRPC | 4317   | 4317          |
| OTel Collector HTTP | 4318   | 4318          |
| OTel Collector health | 13133 | 13133        |
| OTel metrics scrape | 8889  | 8889          |
| Tempo            | 3200      | 3200          |
| Loki             | 3100      | 3100          |
| Prometheus       | **9091**  | 9090          |
| Grafana          | 3000      | 3000          |

> ⚠️ Prometheus está na porta **9091** do host (conflito com prometheus nativo em :9090).

---

## 2. Health checks

```bash
# OTel Collector
curl -sf http://localhost:13133/ | python3 -m json.tool

# Tempo
curl -sf http://localhost:3200/ready

# Loki
curl -sf http://localhost:3100/ready

# Prometheus
curl -sf http://localhost:9091/-/healthy

# Grafana
curl -sf http://localhost:3000/api/health | python3 -m json.tool
```

---

## 3. Prometheus scrape targets

```bash
# Via API interna do container
docker exec itgov-observability-prometheus-1 \
  wget -qO- "http://localhost:9090/api/v1/targets" | \
  python3 -c "
import sys,json
for t in json.load(sys.stdin)['data']['activeTargets']:
    print(t['labels']['job'], '->', t['health'], t.get('lastError',''))
"
```

Targets esperados:
- `otel-collector → up` — scrape em `:8889`
- `itgov → unknown/down` — app Python em `172.17.0.1:9464` (só ativo com app rodando)

---

## 4. Sem métricas M365 no Prometheus

**Verificar:**
```bash
docker exec itgov-observability-prometheus-1 \
  wget -qO- "http://localhost:9090/api/v1/label/__name__/values" | \
  python3 -c "import sys,json; names=json.load(sys.stdin)['data']; [print(n) for n in names if 'm365' in n]"
```

**Causas comuns:**

1. **App Python não está enviando métricas** — `setup_metrics()` precisa ser chamado no startup
2. **OTel Collector não está raspando métricas** — verificar logs: `docker logs itgov-observability-otel-collector-1`
3. **Prometheus não raspa OTel Collector** — verificar se `otel-collector → up` (ponto 3)
4. **MeterProvider não configurado** — rodar smoke: `make smoke-metrics`

---

## 5. Traces não aparecem no Tempo

```bash
# Verificar se o Tempo recebe traces
curl -sG "http://localhost:3200/api/search" \
  --data-urlencode "tags=service.name=itgov-m365-collector" | \
  python3 -c "import sys,json; d=json.load(sys.stdin); print('Traces:', len(d.get('traces',[])))"

# Se vazio, rodar smoke de traces
make smoke
```

**Causas comuns:**

1. **BatchSpanProcessor não flushou** — span criado mas não exportado ainda (aguardar até 30s)
2. **OTel Collector não está rodando** — `curl http://localhost:13133/`
3. **Porta 4317 não acessível** — `nc -zv localhost 4317`

---

## 6. Grafana não carrega dashboard

```bash
# Verificar se arquivo está visível no container
docker exec itgov-observability-grafana-1 \
  find /etc/grafana/provisioning -type f

# Recarregar provisioning sem reiniciar
curl -X POST http://localhost:3000/api/admin/provisioning/dashboards/reload \
  -H "Content-Type: application/json"
```

**Causas comuns:**

1. **Volume montado com arquivo de inode antigo** — `docker compose up -d grafana --force-recreate`
2. **JSON inválido no dashboard** — validar: `python3 -m json.tool < docker/observability/grafana/dashboards/m365-collector.json`
3. **Datasource UID errado** — verificar UIDs em `grafana/provisioning/datasources/datasources.yaml`

---

## 7. Prometheus container sem rede Docker

Sintoma: `otel-collector` target DOWN com erro de DNS.

```bash
# Verificar se prometheus está na rede
docker network inspect itgov-observability_default \
  --format '{{range .Containers}}{{.Name}} {{.IPv4Address}}{{"\n"}}{{end}}'

# Fix: recriar o container
docker compose -f docker/observability/docker-compose.observability.yml \
  up -d prometheus --force-recreate
```

**Causa raiz:** Conflito de porta 9090 com prometheus nativo impede bind → container inicia sem publicar portas → fica fora da rede. Resolvido com `dns: ["127.0.0.11"]` e porta `9091:9090` no docker-compose.

---

## 8. Smoke test rápido (valida pipeline completo)

```bash
# Só traces
make smoke

# Traces + métricas
make smoke-metrics

# Verificar traces no Tempo
sleep 10
curl -sG "http://localhost:3200/api/search" \
  --data-urlencode "tags=service.name=itgov-m365-collector" | \
  python3 -m json.tool | grep traceID
```
