# ADR-009 — Microsoft Graph Integration Strategy

**Status:** Proposed  
**Data:** 2026-06-02  
**Sprint alvo:** Sprint 12 (09–22/06/2026)  
**Contexto:** Implementação de governança M365 (Secure Score, identidades,
licenças, compliance) requer integração robusta com Microsoft Graph API.
Validar com spike antes de marcar como Accepted.

---

## Problema

Microsoft Graph é uma API externa com características específicas:
- Latência 1–5s por chamada
- Throttling agressivo (HTTP 429 com `Retry-After`)
- Paginação via `@odata.nextLink`
- Autenticação OAuth2 com token de curta duração

Uma integração ingênua (chamada direta no handler HTTP) quebraria a UX
do dashboard e seria bloqueada por throttle em produção.

---

## Decisão

### 1. Cliente HTTP: `httpx` async — NÃO o SDK oficial

**Escolhido:** `httpx.AsyncClient` direto contra a Graph REST API.

**Rejeitado:** `msgraph-sdk-python` (SDK oficial Microsoft).

| Critério | httpx | msgraph-sdk-python |
|----------|-------|--------------------|
| Async nativo | ✅ first-class | ⚠️ via Kiota |
| Tamanho da dep | ✅ ~1MB | ❌ ~30MB+ |
| Debug / curl-friendly | ✅ | ❌ caixa-preta |
| Controle retry/throttle | ✅ tenacity | ⚠️ middleware opaco |
| Type safety | ✅ Pydantic próprio | ⚠️ modelos gerados |
| Curva de aprendizado | ✅ HTTP puro | ❌ abstrações Kiota |

**Trade-off aceito:** manter nossos próprios Pydantic models em vez dos
modelos gerados pelo SDK. Esforço inicial maior, controle total.

### 2. Autenticação: Client Credentials com certificado

OAuth2 Client Credentials Flow usando certificado X.509 (não client secret).

```python
# Esqueleto conceitual — itgov/integrations/m365/auth.py
from msal import ConfidentialClientApplication

class GraphAuthProvider:
    """Token provider com cache automático via MSAL."""

    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        cert_path: str,
        cert_thumbprint: str,
    ) -> None: ...

    async def get_token(self) -> str:
        """Retorna token válido; MSAL faz refresh automático."""
```

**Por que certificado (não secret):**
- Compliance corporativo — rotação automática
- MSAL cacheia tokens em memória entre chamadas
- Microsoft best practice para production apps

**Secrets:** env vars obrigatórias: `M365_TENANT_ID`, `M365_CLIENT_ID`,
`M365_CERT_PATH`, `M365_CERT_THUMBPRINT`.

### 3. Scopes mínimos (princípio do menor privilégio)

| Scope | Uso |
|-------|-----|
| `SecurityEvents.Read.All` | Secure Score |
| `Directory.Read.All` | Users, groups |
| `Reports.Read.All` | Usage reports |
| `Policy.Read.All` | Conditional Access |
| `IdentityRiskyUser.Read.All` | Identity Protection |

**Regra:** `.Read.*` apenas — nenhum `.ReadWrite.*` no MVP.

### 4. Retry com backoff exponencial (tenacity)

```python
# Esqueleto — itgov/integrations/m365/client.py
from tenacity import (
    retry, stop_after_attempt,
    wait_exponential, retry_if_exception_type,
)

class GraphClient:
    @retry(
        retry=retry_if_exception_type(GraphThrottledError),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    async def _request(self, method: str, path: str, **kwargs) -> dict:
        """Respeita Retry-After quando presente no header 429."""
```

**Regras:**
- 5 retries máximo, backoff 2s → 60s
- Honrar `Retry-After` SEMPRE que presente
- Logar cada throttle via structlog (warning)

### 5. Paginação via `@odata.nextLink`

```python
async def paginate(
    self,
    initial_url: str,
    max_pages: int = 100,
) -> AsyncIterator[dict]:
    """Itera todas as páginas automaticamente.

    $top=999 (máximo Graph) minimiza round-trips.
    max_pages como safety limit contra loops infinitos.
    """
```

### 6. Hierarquia de exceções

```python
# itgov/integrations/m365/exceptions.py
class GraphError(Exception): ...        # base
class GraphAuthError(GraphError): ...   # 401, 403
class GraphNotFoundError(GraphError):...# 404
class GraphThrottledError(GraphError):..# 429
class GraphUpstreamError(GraphError):...# 5xx
class GraphValidationError(GraphError):.# Pydantic mismatch
```

Mapeamento para API conventions (ADR-003):
- `GraphAuthError` → `502 UPSTREAM_ERROR` (não expor 401 do Graph)
- `GraphThrottledError` após retries → `503 UPSTREAM_ERROR`
- `GraphUpstreamError` → `502 UPSTREAM_ERROR`

### 7. Estrutura de pastas

```
itgov/integrations/m365/
├── __init__.py
├── auth.py              # GraphAuthProvider (MSAL)
├── client.py            # GraphClient base (httpx + retry)
├── exceptions.py        # Hierarquia de erros
├── models/              # Pydantic models por domínio
│   ├── secure_score.py
│   ├── identity.py
│   ├── license.py
│   └── compliance.py
└── services/            # Service layer (lógica de negócio)
    ├── secure_score_service.py
    ├── identity_service.py
    └── license_service.py
```

---

## Consequências

**Positivas:**
- Controle total sobre auth, retry, cache, observabilidade
- Bundle leve — deploy K8s mais rápido
- Debug curl-friendly (sem abstrações)
- Type safety via Pydantic em todo o pipeline

**Negativas / Trade-offs:**
- Manutenção própria dos Pydantic models
- Mudanças breaking na Graph API exigem update manual
- Curva de aprendizado do MSAL para quem nunca usou

---

## Validação obrigatória antes da Sprint 12

Ver `docs/spikes/m365-graph-poc.md`. Critérios de aceite do spike:

- [ ] Auth com certificado funciona end-to-end
- [ ] Secure Score retorna dados válidos
- [ ] Paginação com `$top=999` funciona
- [ ] Retry em 429 funciona (observado ou simulado)
- [ ] Latência p50/p95 medida e documentada

**Este ADR passa de Proposed → Accepted após spike concluído.**

---

## Referências

- [Graph throttling](https://learn.microsoft.com/graph/throttling)
- [MSAL Python](https://learn.microsoft.com/entra/identity-platform/msal-python)
- [Graph permissions reference](https://learn.microsoft.com/graph/permissions-reference)
- ADR-003 (API conventions)
- LL-001 (validar lib antes de usar)
