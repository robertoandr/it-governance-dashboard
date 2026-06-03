# Incident LL-003 — M365 Client Secret Rotation

**Tipo:** Security Incident — Credential Exposure
**Severidade:** P0 (High)
**Status:** ✅ FECHADO
**Abertura:** 2026-06-02T21:30 BRT (descoberta)
**Fechamento:** 2026-06-02T23:59 BRT
**MTTR:** ~2h30min

---

## Linha do tempo

| Hora (BRT) | Evento |
|------------|--------|
| ~09/05 | `m365_collector.py` criado com `CLIENT_SECRET` hardcoded |
| 02/06 21:30 | Descoberto durante auditoria de arquitetura (Sprint 12) |
| 02/06 21:45 | Fase 0: exposição mapeada — não estava em git, sem cópias externas |
| 02/06 22:00 | Fase 2: hardcoded substituído por `_load_env_file("/opt/zabbix/m365/.env")` |
| 02/06 22:15 | Código validado, sem credencial literal no arquivo |
| 02/06 ~22:30 | Portal Entra ID: novo secret gerado (`prod-2026-06-02-rotation`, 6 meses) |
| 02/06 ~22:45 | `/opt/zabbix/m365/.env` atualizado com novo secret |
| 02/06 ~23:00 | Coletor testado e funcionando com novo secret |
| 02/06 ~23:15 | Secret antigo (`pK78Q~...`) invalidado no Entra ID |
| 02/06 ~23:30 | Sign-in audit (30 dias): **limpo, sem atividade suspeita** |
| 02/06 23:59 | LL-003 fechado |

---

## Resumo executivo

Um script de coleta M365 (`m365_collector.py`) foi identificado com
credenciais de aplicação Microsoft hardcoded no código-fonte. O arquivo
concede acesso Read a dados do tenant: Secure Score, usuários, MFA,
Conditional Access e service health.

**Impacto real:** nenhuma evidência de uso indevido. Arquivo restrito
ao servidor interno, não estava em repositório Git. Rotação executada
preventivamente dentro de 2h30min da descoberta.

---

## Ações executadas

### Fase 0 — Avaliação de exposição ✅
- Arquivo confirmado: não está em git repo
- Permissão: `755 root:root` — qualquer usuário do servidor podia ler
- 5 referências no bash_history do usuário zabbix
- Não encontrado em backups externos ou logs transmitidos

### Fase 1 — Novo secret gerado ✅
- Portal Entra ID: `prod-2026-06-02-rotation`
- Expiração: 6 meses (rotação forçada em dez/2026)
- Copiado para password manager imediatamente

### Fase 2 — Hardcoded removido ✅
- `CLIENT_SECRET = "..."` → `_load_env_file("/opt/zabbix/m365/.env")`
- Implementação pura Python (sem dependências extras)
- Validação: sintaxe OK, env carrega, nenhum literal no arquivo

### Fase 3 — Invalidação + auditoria ✅
- Todos os secrets antigos deletados no Entra ID
- Sign-in logs (30 dias): nenhuma atividade de IPs externos
- Nenhum acesso fora do horário operacional do coletor

---

## Achado secundário

Foram encontrados 4 valores distintos de `AZURE_CLIENT_SECRET` para
o mesmo app em diferentes arquivos `.env` no servidor. Nenhum estava
exposto publicamente, mas indica histórico de rotações sem limpeza
dos secrets antigos no Entra ID. Todos invalidados nesta operação.

---

## Ações preventivas em andamento

| Ação | Sprint | Status |
|------|--------|--------|
| ADR-009 v2: credentials SEMPRE via env | Sprint 12 | ✅ |
| ADR-011: coletor legado → deprecar Sprint 13 | Sprint 13 | 📋 planejado |
| `gitleaks` em pre-commit global do servidor | Sprint 13 | 📋 planejado |
| Inventário de todos os `.env` do servidor | Sprint 13 | 📋 planejado |
| Calendário de rotação trimestral (jan/abr/jul/out) | Sprint 12 | ⏳ criar |
| Backlog: Azure Key Vault para novo código M365 | Sprint 15+ | 🔮 futuro |

---

## Métricas de resposta

| KPI | Valor |
|-----|-------|
| Tempo de descoberta após criação | 24 dias |
| MTTR (detecção → fechamento) | ~2h30min |
| Chamadas Graph indevidas confirmadas | 0 |
| IPs externos em sign-in logs | 0 |
| Secrets invalidados | 4 (todos os antigos) |

---

## Refs

- `docs/lessons-learned/003-hardcoded-secret-incident.md` — detalhes técnicos completos
- ADR-009 v2 — seção 2 (auth via env vars)
- ADR-011 — deprecação planejada do coletor legado
