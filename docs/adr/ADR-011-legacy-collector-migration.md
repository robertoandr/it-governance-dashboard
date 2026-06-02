# ADR-011 — Migração do Coletor M365 Legado (Strangler Fig)

**Status:** Accepted  
**Data:** 2026-06-02  
**Sprint alvo:** Sprint 12–13  
**Contexto:** Dois coletores M365 em produção desde maio/2026
(`collectors/graph.py` e `/opt/zabbix/m365_collector.py`) usam
`requests` síncrono, sem cache, sem Pydantic, sem structlog.
Precisam ser portados para a arquitetura do projeto sem interromper
a coleta de dados.

---

## Problema

Reescrever do zero seria:
- Arriscado (perder quirks de produção já descobertos)
- Demorado (validar todos os endpoints de novo)
- Desnecessário (legado funciona — o problema é a arquitetura, não a lógica)

Migrar tudo em 1 PR seria:
- Impossível de revisar
- Impossível de fazer rollback granular

---

## Decisão

**Strangler Fig Pattern** — envolver o legado gradualmente, endpoint por endpoint.
O legado continua funcionando como oracle de regressão até a paridade completa.

### Fase 1 — Fundação async (Sprint 12, semana 1)

Antes de migrar endpoints, criar a infraestrutura:

1. `itgov/integrations/m365/auth.py` — `GraphAuthProvider` (MSAL + client secret)
2. `itgov/integrations/m365/client.py` — `GraphClient` base (httpx + retry + structlog)
3. `itgov/integrations/m365/exceptions.py` — hierarquia tipada
4. `itgov/cache/client.py` — `CacheClient` Redis (ADR-010)
5. Testes unitários da infraestrutura (sem chamadas Graph reais)

### Fase 2 — Migração por endpoint (Sprint 12, semana 2)

Ordem do simples ao complexo:

| Ordem | Endpoint | Complexidade | Issue |
|-------|----------|-------------|-------|
| 1 | `/subscribedSkus` | Baixa — sem paginação | a criar |
| 2 | `/security/secureScores?$top=1` | Baixa — retorno único | a criar |
| 3 | `/admin/serviceAnnouncement/healthOverviews` | Baixa | a criar |
| 4 | `/identity/conditionalAccess/policies` | Média — paginação | a criar |
| 5 | `/reports/authenticationMethods/...` | Média — quirk JSON | a criar |
| 6 | `/users` + counts | Alta — `ConsistencyLevel`, paginação | a criar |
| 7 | `/identityProtection/riskyUsers` | Alta — requer P2, 403 graceful | a criar |

### Fase 3 — Deprecação (Sprint 13)

1. Feature flag `USE_LEGACY_M365_COLLECTOR` no `.env` (padrão `false`)
2. Monitorar 1 semana em produção com novo código
3. Arquivar legado em `archive/collectors/` — **NÃO deletar** (quirks são documentação viva)
4. Remover chamada ao legado do `app.py`

### Critérios de aceite por endpoint migrado

Antes de fechar a issue de cada endpoint:

- [ ] Pydantic model criado para response (em `itgov/integrations/m365/models/`)
- [ ] Service com type hints completos e docstrings
- [ ] Cache Redis funciona (TTL conforme ADR-010)
- [ ] Teste unitário com fixture do response real do Graph
- [ ] Teste de integração com mock `httpx`
- [ ] Diff vs legado: 0 diferenças em 3 execuções consecutivas
- [ ] structlog nos pontos-chave (`info` em sucesso, `warning` em degradado)
- [ ] Fallback documentado se endpoint falhar (retornar None ou valor seguro)

### Script de diff (oracle de regressão)

```python
# scripts/diff_m365_legacy.py
"""
Compara output do legado vs novo código para um endpoint.
Uso: python scripts/diff_m365_legacy.py --endpoint licenses
"""
import asyncio
import json

async def main(endpoint: str) -> None:
    # Legado
    legacy = await run_legacy_collector(endpoint)
    # Novo
    new = await run_new_service(endpoint)
    # Diff
    diff = deepdiff(legacy, new, ignore_order=True)
    if diff:
        print(f"❌ Diff encontrado:\n{json.dumps(diff, indent=2)}")
        exit(1)
    print(f"✅ {endpoint}: zero diferenças")
```

---

## Anti-padrões proibidos

- ❌ Refatorar legado durante a migração (deixa quieto)
- ❌ Migrar 2+ endpoints no mesmo PR
- ❌ Pular teste de diff "porque o código é simples"
- ❌ Deletar legado antes de 1 semana de produção do novo
- ❌ Ignorar o `-1` do legado como "erro desconhecido" — cada `-1` é um quirk documentado

---

## Legado como documentação viva

O legado não é lixo — é documentação executável de quirks de produção:

| Legado | Quirk documentado |
|--------|-------------------|
| `m["m365.users.no_mfa"] = -1` | `UserAuthenticationMethod.Read.All` separado de `Reports.Read.All` |
| `m[key] = -1  # requer AAD P1` | `signInActivity` silenciosamente null sem P1 |
| `risky_users: 403 (requer Entra ID P2)` | P2 necessário para identity protection |
| `api_csv()` + fallback JSON | Reports retornam CSV por padrão |
| BOM strip `.lstrip("﻿")` | Encoding quirk do Graph nos CSV headers |

Cada `-1` no legado = 1 `GraphLicenseError` no novo código.

---

## Consequências

**Positivas:**
- Rollback granular por endpoint (toggle flag)
- Legado como oracle de regressão durante coexistência
- Endpoints migrados ganham: cache + async + observability + Pydantic
- Time pode trabalhar em paralelo (um PR por endpoint)

**Negativas / Trade-offs:**
- Duplicação temporária de lógica (aceita, limitada a Sprint 12-13)
- Custo Graph dobrado durante coexistência (mitigado pelo cache Redis)
- Sprint 13 precisa janela de 1 semana para deprecação

---

## Referências

- ADR-009 (Graph integration patterns)
- ADR-010 (Caching layer)
- Coletores legados:
  - `collectors/graph.py` — Secure Score, Intune, Licenses
  - `/opt/zabbix/m365_collector.py` — Users, Security, SharePoint, Teams
  - `/opt/zabbix/m365/m365_status.py` — Service Health, MFA, Risky users
- [Strangler Fig Application](https://martinfowler.com/bliki/StranglerFigApplication.html)
