# ADR-009 — Microsoft Graph Integration Strategy

**Status:** Accepted
**Data original:** 2026-06-02
**Última revisão:** 2026-06-02 (pós-descoberta de coletores legados)

**Histórico de revisões:**
- v1 (2026-06-02): Proposed — abordagem greenfield com certificado X.509
- v2 (2026-06-02): Accepted — alinhado com realidade de produção (coletor legado validou premissas)

---

## Problema

Microsoft Graph é uma API externa com características específicas:
- Latência variável (timeout configurado em 30s no legado — tipicamente <2s)
- Throttling via HTTP 429 com `Retry-After`
- Paginação via `@odata.nextLink`
- Alguns endpoints exigem licenças premium (AAD P1, Entra ID P2)
- Reports retornam CSV por padrão (não JSON)

---

## Decisão

### 1. Cliente HTTP: `httpx` async (migrar de `requests` síncrono)

**Escolhido:** `httpx.AsyncClient` — mesma lib já em uso no projeto.

**Legado atual:** `requests` síncrono, `timeout=30` em todas as chamadas.

**Por que migrar:** event loop do Flask + threads de refresh; consistência com `SyncAPIClient` do projeto.

**Trade-off aceito:** Legado continua rodando via `requests` enquanto endpoints são migrados (ADR-011).

### 2. Autenticação: Client Credentials com client secret

**Decisão atual (MVP):** OAuth2 Client Credentials com **client secret**.

**Justificativa pragmática:**
- Dois coletores em produção desde maio/2026 usam este padrão ✅
- `m365_status.py`: credenciais via `.env` (correto)
- `m365_collector.py`: credenciais hardcoded (⚠️ — rotacionar + mover para `.env`)
- MSAL cacheia tokens automaticamente entre chamadas
- Rotação trimestral manual via Entra ID

**Configuração:**

```python
# itgov/integrations/m365/auth.py
from msal import ConfidentialClientApplication

class GraphAuthProvider:
    """Token provider com client secret e cache MSAL."""

    def __init__(self, tenant_id: str, client_id: str, client_secret: str) -> None:
        self._app = ConfidentialClientApplication(
            client_id=client_id,
            authority=f"https://login.microsoftonline.com/{tenant_id}",
            client_credential=client_secret,
        )

    def get_token(self) -> str:
        result = self._app.acquire_token_for_client(
            scopes=["https://graph.microsoft.com/.default"]
        )
        if "access_token" not in result:
            raise GraphAuthError(result.get("error_description", "unknown"))
        return result["access_token"]
```

**Secrets:** env vars `M365_TENANT_ID`, `M365_CLIENT_ID`, `M365_CLIENT_SECRET`.
Carregar de `.env` via `python-dotenv` (padrão do projeto).

#### 2.1 Future improvement — Certificado X.509

**Quando migrar (não Sprint 12):**
- Compliance corporativo exigir (auditoria formal)
- Secret atual atingir 6 meses
- Sprint dedicada a hardening (Sprint 15+)

### 3. Scopes mínimos (validados em produção)

| Scope | Uso | Status |
|-------|-----|--------|
| `ServiceHealth.Read.All` | Service Health / Incidents | ✅ produção |
| `Directory.Read.All` | Users, groups, roles | ✅ produção |
| `Reports.Read.All` | MFA, SharePoint, Teams | ✅ produção |
| `SecurityEvents.Read.All` | Secure Score, Alerts | ✅ produção |
| `Policy.Read.All` | Conditional Access | ✅ produção |
| `IdentityRiskyUser.Read.All` | Risky users | ⚠️ **requer Entra ID P2** |

### 4. Retry com backoff exponencial (tenacity)

```python
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

**Nota legado:** coletores legados não implementam retry — retornam `-1` em falhas.
Novo código DEVE implementar retry para maior resiliência.

### 5. Paginação via `@odata.nextLink`

Padrão Graph: `$top=999` (máximo) para minimizar round-trips.

**Quirk descoberto:** `@odata.nextLink` pode aparecer mesmo sem próxima página real.
Sempre verificar se `value` está vazio antes de continuar.

```python
async def paginate(self, initial_url: str, max_pages: int = 100) -> AsyncIterator[dict]:
    """Safety limit via max_pages para evitar loops infinitos."""
```

### 6. Reports: forçar JSON (não CSV)

Endpoints `/reports/*` retornam **CSV por padrão** — comportamento inesperado.

```python
# SEMPRE adicionar header para JSON
headers["Accept"] = "application/json"

# OU usar $format query param
url = f"/reports/getSharePointSiteUsageDetail(period='D30')?$format=application/json"
```

**Legado:** `m365_collector.py` implementa fallback CSV→JSON com `csv.DictReader`.
Novo código não deve precisar desse fallback se o header for enviado.

### 7. Hierarquia de exceções

```python
# itgov/integrations/m365/exceptions.py
class GraphError(Exception): ...         # base
class GraphAuthError(GraphError): ...    # 401, 403
class GraphNotFoundError(GraphError): ...# 404
class GraphThrottledError(GraphError): ..# 429
class GraphUpstreamError(GraphError): ...# 5xx
class GraphValidationError(GraphError):.# Pydantic mismatch
class GraphLicenseError(GraphError): ...# 403 por falta de licença (P1/P2)
```

Mapeamento para API conventions (ADR-003):
- `GraphAuthError` → `502 UPSTREAM_ERROR`
- `GraphLicenseError` → `503` com mensagem "feature requires premium license"
- `GraphThrottledError` após retries → `503 UPSTREAM_ERROR`

### 8. Catálogo de endpoints validados em produção

| Endpoint | Domínio | Quirks | Fonte |
|----------|---------|--------|-------|
| `/security/secureScores?$top=1` | Secure Score | `$top=1` retorna o mais recente | graph.py |
| `/subscribedSkus` | Licenças | Sem paginação, retorna todos | ambos |
| `/identity/conditionalAccess/policies` | CAP | Pode ter >100; iterar | m365_collector |
| `/reports/authenticationMethods/userRegistrationDetails` | MFA | Requer `Reports.Read.All` | ambos |
| `/users?$count=true&$filter=userType eq 'Member'` | Users | `ConsistencyLevel: eventual` obrigatório | m365_status |
| `/users?$filter=accountEnabled eq true and signInActivity/...` | Inativos | ⚠️ **requer AAD P1** — sem P1, campo `null` sem erro | m365_collector |
| `/directoryRoles?$filter=displayName eq 'Global Administrator'` | Admins | Retorna role, depois buscar `/members` | m365_status |
| `/identityProtection/riskyUsers?$filter=riskLevel ne 'none'` | Risk | ⚠️ **requer Entra ID P2** — retorna 403 sem P2 | m365_status |
| `/admin/serviceAnnouncement/healthOverviews` | Service Health | Status mapeado para enum numérico | m365_status |
| `/admin/serviceAnnouncement/issues?$filter=isResolved eq false` | Incidents | Filtro de abertos | m365_status |
| `/reports/getSharePointSiteUsageDetail(period='D30')` | SharePoint | ⚠️ CSV por padrão — forçar JSON | m365_collector |
| `/reports/getTeamsUserActivityUserDetail(period='D30')` | Teams | ⚠️ CSV + BOM strip necessário | m365_collector |
| `/deviceManagement/managedDevices?$select=complianceState` | Intune | Requer licença Intune | graph.py |

**Fonte:** coletores legados operando em produção desde maio/2026.

### 9. Quirks de produção (lições do legado)

- ⚠️ **AAD P1 para `signInActivity`:** sem P1, campo retorna `null` silenciosamente
- ⚠️ **Entra ID P2 para risky users:** retorna `403 Forbidden` sem P2 — tratar como `GraphLicenseError`
- ⚠️ **Reports retornam CSV por padrão** — sempre enviar `Accept: application/json` ou `?$format=application/json`
- ⚠️ **BOM (U+FEFF)** nos CSVs de Reports — legado usa `.lstrip("﻿")` nas chaves
- ⚠️ **`ConsistencyLevel: eventual`** obrigatório em queries com `$count=true` em `/users`
- ⚠️ **`@odata.nextLink` falso positivo** — verificar `value` não-vazio antes de paginar
- ⚠️ **Credencial hardcoded em `m365_collector.py`** — rotacionar e mover para `.env`

### 10. Estrutura de pastas

```
itgov/integrations/m365/
├── __init__.py
├── auth.py              # GraphAuthProvider (MSAL + client secret)
├── client.py            # GraphClient base (httpx async + retry)
├── exceptions.py        # Hierarquia tipada incluindo GraphLicenseError
├── models/              # Pydantic models por domínio
│   ├── secure_score.py
│   ├── identity.py
│   ├── license.py
│   ├── service_health.py
│   └── compliance.py
└── services/            # Service layer
    ├── secure_score_service.py
    ├── identity_service.py
    ├── license_service.py
    └── service_health_service.py
```

---

## Validação

✅ **Validado em produção** via coletores legados desde maio/2026.

Spike (`docs/spikes/m365-graph-poc.md`) **não é mais bloqueador** —
mantido como referência para futuros endpoints novos.

Itens satisfeitos pelo legado:
- [x] Client credentials flow funciona com MSAL
- [x] `/security/secureScores` retorna dados válidos
- [x] `/subscribedSkus` retorna licenças
- [x] `/identity/conditionalAccess/policies` listável
- [x] MFA reports acessíveis via `Reports.Read.All`
- [x] Service health e incidents via `ServiceHealth.Read.All`

Pendente para Sprint 12 (não-bloqueante):
- [ ] Medir latências formalmente com structlog (substituir timeout=30 por dados reais)
- [ ] Calibrar TTLs do ADR-010 com medições reais
- [ ] Confirmar licenças AAD P1 e Entra ID P2 no tenant de produção
- [ ] Rotacionar credencial hardcoded de `m365_collector.py`

---

## Consequências

**Positivas:**
- Validação empírica em produção reduz risco da Sprint 12
- Legado serve como oracle de regressão (ADR-011)
- Quirks já conhecidos = menos surpresas em produção

**Negativas / Trade-offs:**
- Client secret (não certificado) — mitigado por rotação trimestral
- Necessidade de lidar com CSV em Reports — mitigado pelo header `Accept`
- Dependência de licenças premium para alguns endpoints

---

## Referências

- [Graph throttling](https://learn.microsoft.com/graph/throttling)
- [MSAL Python](https://learn.microsoft.com/entra/identity-platform/msal-python)
- Coletores legados: `collectors/graph.py`, `/opt/zabbix/m365_collector.py`, `/opt/zabbix/m365/m365_status.py`
- ADR-003 (API conventions)
- ADR-010 (Caching layer)
- ADR-011 (Migration strategy)
