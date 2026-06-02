# ADR-010 — Caching Layer for Slow Upstream APIs

**Status:** Proposed  
**Data:** 2026-06-02  
**Sprint alvo:** Sprint 12  
**Contexto:** Microsoft Graph tem latência 1–5s por chamada e throttling
agressivo. O dashboard precisa ser responsivo (<200ms p95). Sem cache,
cada abertura de aba M365 faria 3–8 chamadas Graph em série.

---

## Problema

| Cenário | Sem cache | Com cache |
|---------|-----------|-----------|
| Abrir aba Segurança | ~4s (4 calls Graph) | <50ms (HIT) |
| 10 usuários simultâneos | 40 chamadas Graph | 4 chamadas (1 miss + 9 hits) |
| Throttle ativo | erros 429 visíveis | 0 erros (HIT serve) |
| Redis down | sistema funciona lento | sistema funciona lento |

---

## Decisão

### 1. Backend: Redis 7.x

**Escolhido:** Redis 7 standalone (sentinel para HA em produção).

**Rejeitados:**

| Alternativa | Motivo da rejeição |
|-------------|-------------------|
| `functools.lru_cache` in-memory | Não compartilha entre workers/pods |
| SQLite | I/O síncrono, sem TTL nativo, bloqueia event loop |
| Memcached | Sem persistência, sem estruturas ricas, sem pub/sub |

### 2. Cliente: `redis-py` async (`redis.asyncio`)

```python
# Esqueleto — itgov/cache/client.py
from redis.asyncio import Redis, ConnectionPool

class CacheClient:
    def __init__(self, url: str, max_connections: int = 50) -> None:
        pool = ConnectionPool.from_url(
            url, max_connections=max_connections, decode_responses=True
        )
        self._redis = Redis(connection_pool=pool)

    async def get_json(self, key: str) -> dict | None: ...
    async def set_json(self, key: str, value: dict, ttl: int) -> None: ...
    async def delete(self, *keys: str) -> int: ...
    async def invalidate_pattern(self, pattern: str) -> int: ...
```

### 3. TTL por domínio (calibrado com contexto de produção)

| Domínio | TTL | Latência típica | Justificativa |
|---------|-----|-----------------|---------------|
| Secure Score | 6h | `<2s` | Atualiza 1×/dia no Graph |
| Service Health | 5min | `<1s` | Incidentes mudam rápido |
| Users (lista total) | 1h | `<2s` | Mudanças raras, volume alto |
| Licenses | 1h | `<1s` | Muda por compras/atribuições |
| MFA reports | 4h | `<3s` | Reports diários (timeout=30 no legado) |
| Conditional Access | 30min | `<2s` | Admin pode mudar a qualquer hora |
| Risky users | 15min | `<2s` | Segurança — frescor importa |
| Admin roles/members | 2h | `<1s` | Muda raramente |
| Intune compliance | 30min | `<3s` | Refresh por ciclo de sync Intune |
| Token MSAL | `exp` do token | n/a | MSAL gerencia internamente |

**Nota:** latências marcadas como `<Xs` são estimativas do legado
(timeout=30s configurado, não medido formalmente). Medir com structlog
em Sprint 12 e ajustar TTLs com dados reais.

**Regra geral:** TTL = (frequência de atualização upstream) ÷ 2

### 4. Convenção de chaves

```
m365:<domínio>:<recurso>:<identificador>:<versão>

Exemplos:
  m365:secure_score:current:v1
  m365:users:list:v1
  m365:user:by_id:abc-123:v1
  m365:licenses:tenant:v1
```

`<versão>` permite invalidação global ao mudar schema do model:
bumpar `v1 → v2` no código faz o cache antigo ser ignorado sem flush.

### 5. Padrão cache-aside + Stale-While-Revalidate (SWR)

```
HIT fresh  → retorna imediato (p95 < 50ms)
HIT stale  → retorna stale + dispara refresh em background
MISS       → busca upstream (única vez que usuário espera)
```

```python
# Esqueleto — itgov/cache/decorators.py
def cached(
    key_template: str,
    ttl: int,
    stale_while_revalidate: int = 0,
) -> Callable:
    """Cache-aside com SWR opcional.

    stale_while_revalidate=300 serve stale por +5min
    enquanto refresh acontece em background.
    """
```

### 6. Invalidação explícita

Para casos onde TTL não basta (forçado pelo admin):

```python
# Após mutação manual
await cache.invalidate_pattern("m365:users:*")
```

Endpoint de refresh forçado:
```python
@ns.route("/m365/refresh")
class M365RefreshResource(Resource):
    @ns.response(204, "Cache invalidado")
    def post(self):
        """Rate limited: 1 req/min/usuário."""
        cache.invalidate_pattern("m365:*")
        return "", 204
```

### 7. Fallback graceful obrigatório

Se Redis cair, o sistema **não pode quebrar**:

```python
async def get_with_fallback(key: str) -> Any | None:
    try:
        return await cache.get_json(key)
    except RedisError as exc:
        log.warning("cache_unavailable", key=key, error=str(exc))
        return None  # força fetch upstream — degrada, não quebra
```

**Trade-off:** sem cache = lento + mais throttle. Aceitável como
modo degradado. Inaceitável como modo normal — monitorar hit ratio.

### 8. Observabilidade (métricas Prometheus)

```
cache_hits_total{domain, key_pattern}
cache_misses_total{domain, key_pattern}
cache_errors_total{domain, operation}
cache_latency_seconds{operation}       # histogram
upstream_calls_total{api, endpoint}
upstream_latency_seconds{api, endpoint} # histogram
```

**Alertas futuros:**
- Hit ratio < 70% → cache ineficaz, revisar TTLs
- `cache_errors_total` > 0 em 5min → Redis com problema
- p95 upstream > 5s → Graph degradado, acionar suporte Microsoft

### 9. Deploy K8s (referência)

```yaml
# Conceitual — k8s/redis.yaml
kind: StatefulSet
spec:
  replicas: 1   # MVP; sentinel em fase 2
  containers:
  - name: redis
    image: redis:7-alpine
    args: ["--maxmemory", "256mb", "--maxmemory-policy", "allkeys-lru"]
    resources:
      requests: { memory: "128Mi", cpu: "50m" }
      limits:   { memory: "300Mi", cpu: "200m" }
```

**Política LRU:** Redis evicta menos usados quando atinge o limite.

---

## Open questions (decidir durante Sprint 12)

- [ ] Redis local em dev: `docker-compose` service ou `fakeredis`?
- [ ] Compressão de payloads grandes (>10KB)? gzip ou não?
- [ ] Expor "cache age" na UI do dashboard para debug?
- [ ] Invalidação cross-pod via pub/sub — escopo Sprint 12 ou 13?

---

## Consequências

**Positivas:**
- Dashboard responsivo (<200ms p95 em condições normais)
- Throttling Graph minimizado (menos chamadas reais por hora)
- Resiliente a falha Redis (degrada graceful)

**Negativas / Trade-offs:**
- Redis é mais um componente para monitorar/operar
- Dados podem estar desatualizados por até TTL segundos (aceitável por domínio)
- Invalidação em multi-pod exige pub/sub (fase 2 — não Sprint 12)

**Este ADR passa de Proposed → Accepted após spike validar TTLs reais.**

---

## Referências

- ADR-009 (Graph integration — cliente que será cacheado)
- [redis-py async docs](https://redis-py.readthedocs.io/en/stable/examples/asyncio_examples.html)
- [Stale-While-Revalidate](https://web.dev/stale-while-revalidate/)
- [Redis cache patterns](https://redis.io/docs/manual/patterns/)
