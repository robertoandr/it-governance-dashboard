# Sprint 11 Retrospective — Tech Debt & Asset Inventory

**Período planejado:** 02–08/06/2026
**Período real:** 02/06/2026 (1 dia)
**Velocity ratio:** 7× acima do estimado
**Status:** ✅ 5/5 issues entregues — Sprint fechada

---

## Entregas

| Issue | Título | PR | Testes | Coverage módulo |
|-------|--------|----|--------|-----------------|
| #89 | CI Coverage Gate 85% | #95 | — | infra |
| #84 | AtivoDB Schema + Alembic | #96 | 15 | 100% |
| #85 | AtivoService CRUD + upsert | #97 | 42 | 94.87% |
| #86 | REST API /api/v1/ativos | #98 | 21 | — |
| #88 | UI Widget Inventário | #99 | 20 | — |

**Suite final:** 390 testes · 93.10% branch coverage · gate 85% ativo

---

## KEEP — O que funcionou

### 1. Execução em ordem natural de dependência

A sequência schema → service → API → UI eliminou retrabalho.
Cada camada testou a anterior como side-effect natural dos seus próprios testes.
`get_stats()` no service (#85) foi consumido diretamente pelo widget (#88) sem ajuste.

### 2. Commits atômicos por camada

O hook de pre-commit que rejeita >500 linhas forçou granularidade saudável.
Cada commit conta uma história isolada, rollback é cirúrgico.

### 3. Coverage gate como guarda-corpo automático

Com `--cov-fail-under=85` no CI, qualquer PR que degradar coverage quebra
antes de chegar em revisão. Descobrimos que branch coverage (95→93%) é
mais rigoroso que line coverage — e ainda assim dentro do gate.

### 4. ADRs como contratos rastreáveis

ADR-0003 (LGPD soft delete) embasou decisões em #84 e #85 sem debate.
Ter a decisão documentada = menos fricção na execução.

### 5. PRs pequenos + merge no mesmo dia

5 PRs mergeados no mesmo dia, zero conflitos, zero retrabalho.
Revisão mental fica trivial quando o diff é focado.

---

## CHANGE — O que melhorar

### 1. Smoke test antes de usar decorators de libs externas

O bug do `@marshal_with` no Flask-RESTX 1.3.x custou ~40 min.
Um smoke test isolado (`curl -i` em cada status code) teria capturado em 2 min.

**Ação:** Antes de wrappar endpoints em qualquer decorator de framework,
validar todos os status codes manualmente.

### 2. Lesson learned documentado na hora

O quirk do Flask-RESTX foi identificado, corrigido, mas não documentado no PR.
Virou memória tácita. Esta retro corrige, mas o timing ideal é o PR description.

**Ação:** Campo obrigatório "Quirks / Gotchas" no template de PR para bugs de libs.

### 3. Velocity 7× não deve virar expectativa

Sprint estimada em 14 dias entregue em 1. Isso é excepcional, não baseline.
Sprint 12 (M365 Graph) tem complexidade de integração externa real.

**Ação:** Manter estimativa honesta. Spike obrigatório antes de Sprint 12.

---

## STOP — O que parar de fazer

### Confiar em "vai funcionar" com libs em versão minor nova

Flask-RESTX 1.3.x quebrou contrato implícito do `@marshal_with`.
Sorte que a regressão foi silenciosa nos testes e não em produção.

**Regra:** Para libs que envolvem serialização HTTP, validar manualmente
TODOS os status codes esperados antes de considerar a implementação done.

---

## Insights estratégicos

**"Slow is smooth, smooth is fast" — confirmado.**
Planejamento detalhado por issue + execução disciplinada > improvisação rápida.

**Coverage 93% pode mascarar testes superficiais.**
Linha coberta ≠ comportamento testado. Avaliar `mutmut` (mutation testing) no roadmap Q3.

**Service layer antes de API/UI — padrão a manter.**
A inversão teria gerado mocks desnecessários ou acoplamento prematuro.

---

## Action items para Sprint 12

- [ ] POC Microsoft Graph antes de 09/06/2026
- [ ] ADR-009 — Estratégia M365 Graph (cache, rate limit, retry)
- [ ] Template de PR com campo "Quirks / Gotchas"
- [ ] Avaliar mutation testing (mutmut) para roadmap Q3
- [x] `docs/lessons-learned/` criado — LL-001 documentado
- [x] ADR-003 API Conventions documentado
