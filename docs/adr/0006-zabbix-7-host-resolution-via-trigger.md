# ADR-0006: Resolução de hosts via trigger.get no Zabbix 7.0

## Status: Aceito

## Contexto

Zabbix 7.0 removeu o parâmetro `selectHosts` de `problem.get`.
Tentativa inicial de enriquecimento inline (1 request) falhou com:

```
Invalid parameter "/": unexpected parameter "selectHosts"
```

A implementação original do `ZabbixCollector.fetch()` assumia que hosts
chegariam embutidos na resposta de `problem.get`, seguindo a documentação
do Zabbix 6.x.

## Decisão

Pipeline em 2 fases para resolução de hosts:

1. `problem.get` → retorna problemas com `objectid` (= `triggerid`)
2. `trigger.get(triggerids=[...], selectHosts=["hostid","host","name"])` →
   resolve hosts via trigger em batch único

```python
# collector.py — fetch()
trigger_ids = list({p["objectid"] for p in raw_problems if p.get("objectid")})
triggers_raw = await self.client.trigger_get(trigger_ids)
trigger_hosts = {t["triggerid"]: [ZabbixHost(**h) for h in t.get("hosts", [])]
                 for t in triggers_raw}
```

## Trade-offs

| | |
|---|---|
| ✅ | Compatível com Zabbix 7.0+ |
| ✅ | Retrocompatível com 6.x (`selectHosts` ainda funciona em `trigger.get`) |
| ✅ | Batching: 1 `trigger.get` para N problemas — sem N+1 queries |
| ❌ | +1 round-trip HTTP por coleta |
| ❌ | Acoplamento conceitual: problem → trigger → host (3 entidades) |

## Métricas (smoke test inicial — 5 problemas, 5 triggers)

| Request | Latência |
|---|---|
| `problem.get` | 201ms |
| `trigger.get` (5 em batch) | 93ms |
| `write_points` (governance_raw) | 204ms |
| **Total pipeline** | **638ms** |

Overhead do segundo round-trip (~100ms) é aceitável para coletas periódicas
de 5–60 minutos.

## Alternativas consideradas

**Manter `selectHosts` em `problem.get`:** incompatível com Zabbix 7.0+.
Descartado.

**Cache de hosts:** `host.get` uma vez por run e mapear por hostid. Adiciona
complexidade e uma terceira chamada. Descartado por prematuridade — o volume
atual (< 500 hosts) não justifica.

**`event.get` com `selectHosts`:** disponível no 7.0, mas retorna eventos de
log histórico e não o enriquecimento de trigger necessário para o transformer.
Descartado.
