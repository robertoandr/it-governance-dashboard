# Spike — Microsoft Graph PoC

**Objetivo:** validar premissas de ADR-009 e ADR-010 antes da Sprint 12.  
**Duração estimada:** 2–3h  
**Responsável:** executar antes de 09/06/2026  
**Output:** este documento atualizado + `scripts/spike_graph.py` executado

---

## Pré-requisitos

1. App registrada no Entra ID com scopes do ADR-009
2. Certificado X.509 gerado e exportado (PEM)
3. Variáveis de ambiente configuradas:

```bash
export M365_TENANT_ID="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
export M365_CLIENT_ID="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
export M365_CERT_PATH="/path/to/cert.pem"
export M365_CERT_THUMBPRINT="ABCDEF1234567890..."
```

4. Dependências instaladas:

```bash
pip install httpx msal tenacity structlog
```

---

## Checklist de validação

### Auth

- [ ] App registrada no Entra ID com scopes corretos
- [ ] Certificado gerado, thumbprint anotado
- [ ] `MSAL.acquire_token_for_client()` retorna token
- [ ] Token cacheado entre chamadas (verificar via `app.get_accounts()`)
- [ ] Token refresh automático antes de expirar

### Endpoints críticos

- [ ] `GET /security/secureScores` — retorna dados?
- [ ] `GET /users?$top=999&$count=true` — quantos usuários no tenant?
- [ ] `GET /subscribedSkus` — SKUs de licenças visíveis?
- [ ] `GET /identityProtection/riskyUsers` — scope permitido?
- [ ] `GET /reports/getMailboxUsageDetail(period='D7')` — relatório funciona?

### Latência (medir 5 chamadas cada, anotar p50/p95)

| Endpoint | p50 | p95 | Notas |
|----------|-----|-----|-------|
| Secure Score | — ms | — ms | |
| Users list | — ms | — ms | tamanho paginação? |
| Licenses | — ms | — ms | |
| Risky users | — ms | — ms | |

### Throttling

- [ ] Loop de 50+ chamadas rápidas → bate 429?
- [ ] Header `Retry-After` presente na resposta 429?
- [ ] Tenacity respeita `Retry-After` (aguarda o valor exato)?
- [ ] Após backoff, chamada bem-sucedida?

### Paginação

- [ ] Tenant tem >999 usuários? (se não, testar com `$top=5`)
- [ ] `@odata.nextLink` presente quando há mais páginas?
- [ ] Paginar manualmente 3 páginas funciona?
- [ ] `$count=true` retorna total correto?

---

## Script PoC

Salvar como `scripts/spike_graph.py`:

```python
"""Spike PoC — Microsoft Graph integration validation.

Valida premissas de ADR-009 e ADR-010 antes da Sprint 12.
NÃO usar em produção — script de diagnóstico one-shot.

Uso:
    export M365_TENANT_ID=... M365_CLIENT_ID=...
    export M365_CERT_PATH=... M365_CERT_THUMBPRINT=...
    python scripts/spike_graph.py
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import httpx
import structlog
from msal import ConfidentialClientApplication

log = structlog.get_logger()
GRAPH_BASE = "https://graph.microsoft.com/v1.0"


def get_token() -> str:
    with open(os.environ["M365_CERT_PATH"], "rb") as f:
        cert_pem = f.read()

    app = ConfidentialClientApplication(
        client_id=os.environ["M365_CLIENT_ID"],
        authority=f"https://login.microsoftonline.com/{os.environ['M365_TENANT_ID']}",
        client_credential={
            "private_key": cert_pem,
            "thumbprint": os.environ["M365_CERT_THUMBPRINT"],
        },
    )
    result = app.acquire_token_for_client(
        scopes=["https://graph.microsoft.com/.default"]
    )
    if "access_token" not in result:
        raise RuntimeError(f"Auth failed: {result.get('error_description')}")
    log.info("auth_ok", expires_in=result.get("expires_in"))
    return result["access_token"]


async def timed_get(
    client: httpx.AsyncClient,
    path: str,
    token: str,
    label: str,
) -> dict[str, Any]:
    """GET com medição de latência e log estruturado."""
    url = f"{GRAPH_BASE}{path}"
    start = time.perf_counter()
    resp = await client.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30.0,
    )
    elapsed_ms = (time.perf_counter() - start) * 1000

    if resp.status_code == 429:
        retry_after = resp.headers.get("Retry-After", "?")
        log.warning("throttled", label=label, retry_after=retry_after)
        resp.raise_for_status()

    resp.raise_for_status()
    data = resp.json()
    log.info(
        "request_ok",
        label=label,
        latency_ms=round(elapsed_ms, 1),
        status=resp.status_code,
    )
    return data


async def run_measurements(
    client: httpx.AsyncClient,
    token: str,
    path: str,
    label: str,
    n: int = 5,
) -> None:
    """Roda N medições e imprime p50/p95."""
    times = []
    for i in range(n):
        start = time.perf_counter()
        try:
            await client.get(
                f"{GRAPH_BASE}{path}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=30.0,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("measurement_error", i=i, error=str(exc))
            continue
        times.append((time.perf_counter() - start) * 1000)

    if times:
        times.sort()
        p50 = times[len(times) // 2]
        p95 = times[int(len(times) * 0.95)]
        log.info(
            "latency_stats",
            label=label,
            n=len(times),
            p50_ms=round(p50, 1),
            p95_ms=round(p95, 1),
            min_ms=round(min(times), 1),
            max_ms=round(max(times), 1),
        )


async def main() -> None:
    token = get_token()

    async with httpx.AsyncClient() as client:
        # 1. Secure Score
        data = await timed_get(
            client, "/security/secureScores?$top=1", token, "secure_score"
        )
        log.info("secure_score_sample", count=len(data.get("value", [])))

        # 2. Users
        data = await timed_get(
            client,
            "/users?$top=999&$count=true&$select=id,displayName,userPrincipalName",
            token,
            "users",
        )
        log.info(
            "users_result",
            odata_count=data.get("@odata.count"),
            has_next_link=bool(data.get("@odata.nextLink")),
            page_size=len(data.get("value", [])),
        )

        # 3. Licenses
        data = await timed_get(client, "/subscribedSkus", token, "licenses")
        log.info("licenses_result", sku_count=len(data.get("value", [])))

        # 4. Latency measurements
        log.info("--- starting_latency_measurements ---")
        await run_measurements(
            client, token, "/security/secureScores?$top=1", "secure_score", n=5
        )
        await run_measurements(
            client,
            token,
            "/users?$top=10&$select=id",
            "users_small_page",
            n=5,
        )

        log.info("spike_done", next_step="fill in results below in docs/spikes/m365-graph-poc.md")


if __name__ == "__main__":
    asyncio.run(main())
```

---

## Resultados (preencher após executar)

```
Data execução: ____________
Tenant: ____________ (nome, não ID)
Python version: ____________

[Auth]
  ✅ / ❌  Token obtido: ___
  ✅ / ❌  Cache MSAL funcionou entre 2 chamadas: ___

[Secure Score]
  ✅ / ❌  Endpoint responde: ___
  Latência p50: ___ ms | p95: ___ ms
  Dados presentes: score = ___ / 100

[Users]
  ✅ / ❌  Endpoint responde: ___
  Latência p50: ___ ms | p95: ___ ms
  Total usuários no tenant: ___
  ✅ / ❌  Paginação ($top=999 → nextLink): ___

[Licenses]
  ✅ / ❌  Endpoint responde: ___
  Latência p50: ___ ms | p95: ___ ms
  SKUs encontrados: ___

[Risky Users]
  ✅ / ❌  Scope permitido: ___
  (se 403: scope não configurado — atualizar ADR-009)

[Throttling]
  ✅ / ❌  Bate 429 em chamadas rápidas: ___
  ✅ / ❌  Header Retry-After presente: ___
  ✅ / ❌  Tenacity aguardou corretamente: ___
```

---

## Decisões pós-spike (preencher)

Com base nos resultados, atualizar antes de iniciar Sprint 12:

**TTLs (ADR-010) — ajustar se necessário:**
- Secure Score: manter 6h? ___
- Users: manter 1h? ___
- Licenses: manter 1h? ___

**Cliente httpx confirmado?** ___

**Scopes ADR-009 completos?** ___

**Algum endpoint mais lento que esperado?** ___  
→ Se sim, priorizar cache agressivo nesse endpoint.

**Throttling mais agressivo que esperado?** ___  
→ Se sim, reduzir concorrência máxima em GraphClient.

**Após spike concluído:** atualizar status de ADR-009 e ADR-010 para **Accepted**.
