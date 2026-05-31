# Migration Guide — collectors/ → app/services/

## Status

| Módulo | Status | Sprint |
|--------|--------|--------|
| `collectors/zabbix.py` | ⚠️ Deprecated | Migrar em 10F |
| `collectors/influx.py` | ⚠️ Deprecated | Migrar em 10F |
| `collectors/graph.py` | ⚠️ Deprecated | Migrar em 10F |
| `collectors/grafana.py` | ⚠️ Deprecated | Migrar em 10F |
| `collectors/ldap_collector.py` | ⚠️ Deprecated | Migrar em 10F |

## Por que migrar

Os `collectors/` atuais são:
- Síncronos com `requests` (sem timeout padrão, sem retry)
- Sem type hints
- Sem structlog
- Sem testes unitários
- Acoplados diretamente ao `_cache` global em `app.py`

Os novos `app/services/` são:
- Síncronos com `httpx` (timeouts, HTTP/2, API moderna)
- Retry automático via `tenacity` (5xx + TransportError)
- Type hints completos + Pydantic v2 para validação de resposta
- structlog com correlation_id por request
- Testáveis com `respx` (mock do httpx)
- Sem acoplamento ao `_cache` global

## Plano Sprint 10F

1. Criar `app/services/graph_service.py` (migra `collectors/graph.py`)
2. Criar `app/services/grafana_service.py` (migra `collectors/grafana.py`)
3. Criar `app/services/influx_service.py` (migra `collectors/influx.py`)
4. Criar `app/services/ldap_service.py` (migra `collectors/ldap_collector.py`)
5. Refatorar `app.py/_refresh_loop` para usar os novos services
6. Remover `collectors/` após validação em staging
7. Cobrir migração com testes de integração

## Mapeamento de Métodos

### ZabbixCollector → ZabbixService (Sprint 10E)

| Método antigo | Método novo | Notas |
|---------------|-------------|-------|
| `get_host_summary()` | `get_hosts()` | Retorna `list[ZabbixHost]` |
| `get_problems(limit)` | `get_problems()` | Retorna `list[ZabbixProblem]` |
| `get_active_triggers(limit)` | — | Consolidado em `get_problems()` |
| `acknowledge_event(id, msg)` | `acknowledge_event(id, msg)` | + audit log + rate limit |
| `unacknowledge_event(id)` | — | Avaliar em 10F |

### Zendesk → ZendeskService (Sprint 10E — nova integração)

| Método | Descrição |
|--------|-----------|
| `get_tickets()` | Lista tickets ativos |
| `get_sla_metrics()` | Métricas de SLA por fila |
| `get_satisfaction()` | CSAT score |

## Regra de Ouro

**Não adicione código novo nos `collectors/`.** Se precisar de nova funcionalidade,
implemente diretamente em `app/services/` e abra PR com o label `sprint-10e` ou `sprint-10f`.
