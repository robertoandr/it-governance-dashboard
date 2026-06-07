# Incident Registry

Post-mortems de incidentes em produção. Formato inspirado no Google SRE Book.

## Convenções

- Arquivo: `LL-NNN-slug-descritivo.md` (sequencial global)
- Toda P0/P1/P2 **DEVE** ter post-mortem dentro de 24h do fix
- Blameless: foco em sistemas e processos, nunca em pessoas

## Severidades

| Nível | Critério                              | SLA fix   |
|-------|---------------------------------------|-----------|
| P0    | Sistema inteiro down / dado exposto   | Imediato  |
| P1    | Funcionalidade crítica indisponível   | 4h        |
| P2    | Funcionalidade degradada / 500 em prod| 24h       |
| P3    | Bug cosmético / edge case             | Sprint    |

## Histórico

| ID | Data | Título | Severidade | Status |
|----|------|--------|------------|--------|
| [LL-003](LL-003-secret-rotation.md) | 2026-06-02 | M365 Client Secret Rotation | P0 | ✅ Fechado |
| [LL-004](LL-004-csp-nonce-undefined.md) | 2026-06-06 | CSP nonce undefined → HTTP 500 em todas as rotas HTML | P2 | ✅ Fechado |
