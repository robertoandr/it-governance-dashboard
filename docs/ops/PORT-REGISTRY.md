# Port Registry — it-governance-dashboard

Mapeamento de todas as portas do stack em `noc.grupogadens.com.br` (172.29.2.11).

Atualizado: 2026-06-28

---

## Stack principal (`docker-compose.yml`)

| Porta host | Porta container | Servico        | Container       | Bind        | Notas                                       |
|-----------|----------------|----------------|----------------|-------------|---------------------------------------------|
| 5000      | 5000           | Flask/Gunicorn | itgov-app      | 0.0.0.0     | Interno; exposto via nginx                  |
| 18086     | 8086           | InfluxDB       | itgov-influxdb | 0.0.0.0     | Var: INFLUX_PORT. Host usa 8086 nativamente |
| —         | 6379           | Redis          | itgov-redis    | sem bind    | Acesso apenas via rede Docker itgov-net     |
| —         | 5432           | PostgreSQL     | itgov-postgres | 127.0.0.1   | Acesso apenas interno (127.0.0.1:5432)      |
| 13000     | 3000           | Grafana (dev)  | itgov-grafana  | 0.0.0.0     | Instancia de dev; prod usa stack observ.    |

---

## Stack de observabilidade (`docker/observability/docker-compose.observability.yml`)

| Porta host | Servico        | Container                            | Notas                         |
|-----------|----------------|--------------------------------------|-------------------------------|
| 3000      | Grafana        | itgov-observability-grafana-1        | UI Grafana principal          |
| 3100      | Loki           | itgov-observability-loki-1           | Log aggregation               |
| 3200      | Tempo          | itgov-observability-tempo-1          | Tracing backend               |
| 4317      | OTel Collector | itgov-observability-otel-collector-1 | OTLP gRPC                     |
| 4318      | OTel Collector | itgov-observability-otel-collector-1 | OTLP HTTP                     |
| 8889      | OTel Collector | itgov-observability-otel-collector-1 | Prometheus metrics exporter   |
| 9091      | Prometheus     | itgov-observability-prometheus-1     | Host 9091 mapeia container 9090|
| 13133     | OTel Collector | itgov-observability-otel-collector-1 | Health check                  |

---

## Nginx (host nativo)

| Porta | Bind        | server_name                 | Destino            | Notas                              |
|-------|-------------|-----------------------------|--------------------|-------------------------------------|
| 80    | 0.0.0.0     | *                           | —                  | Redireciona para 443                |
| 443   | 0.0.0.0     | noc.grupogadens.com.br      | 127.0.0.1:5000     | HTTPS Let's Encrypt                 |
| 5001  | 0.0.0.0     | 172.29.2.11                 | 127.0.0.1:5000     | Acesso interno por IP               |
| 8080  | 0.0.0.0     | noc.grupogadens.com.br      | 127.0.0.1:5000 + Zabbix | HTTP principal NOC + Zabbix proxy |
| 8443  | 0.0.0.0     | 172.29.2.11                 | 127.0.0.1:5000     | HTTPS self-signed (alternativo)     |

---

## Servicos nativos (host / systemd)

| Porta | Bind        | Servico                  | Notas                                           |
|-------|-------------|--------------------------|--------------------------------------------------|
| 8082  | 127.0.0.1   | zabbix-mcp-server (MCP)  | API MCP HTTP. Movido de 8080 em 2026-05-25      |
| 9090  | 127.0.0.1   | zabbix-mcp-server (admin)| Portal admin do MCP                             |
| 9877  | 0.0.0.0     | acronis_exporter.py      | Prometheus metrics do Acronis (/home/zabbix/)   |
| 10050 | *           | zabbix_agent2            | Porta padrao agentes passivos                   |
| 10051 | 0.0.0.0     | zabbix_server            | Trapper — recebe dados de agentes ativos        |
| 22    | 0.0.0.0     | SSH                      | Acesso ao servidor                              |
| 3389  | *           | RDP                      | Acesso remoto Windows (xrdp)                    |
| 7070  | 0.0.0.0     | Desconhecido             | Aceita TCP; nao responde HTTP. Investigar       |

---

## Conflitos e reservas

| Porta | Situacao  | Detalhe                                                         |
|-------|-----------|------------------------------------------------------------------|
| 8080  | OCUPADA   | Zabbix frontend — nao usar para outros servicos                 |
| 8082  | RESERVADA | MCP server — nao usar (conflito historico com nginx 2026-05-25) |
| 3000  | OCUPADA   | Grafana observability — itgov-grafana usa 13000                 |
| 18086 | RESERVADA | InfluxDB Docker — host nativo usa 8086; nunca mapear 8086:8086  |

---

## Como atualizar este arquivo

Ao adicionar qualquer novo servico com `ports:` no compose ou novo processo nativo:
1. Atualizar a tabela correspondente
2. Verificar conflito com portas ja listadas
3. Commitar junto com a mudanca que criou a porta
