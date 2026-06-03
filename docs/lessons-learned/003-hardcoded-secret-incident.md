# LL-003 — Client Secret M365 Hardcoded em Coletor Legado

**Data do incidente:** 2026-05-09 (criação do arquivo)
**Data da descoberta:** 2026-06-02 (durante auditoria de arquitetura Sprint 12)
**Severidade:** P0 (alta) — secret com acesso Read a todo o tenant M365
**Status:** ✅ Remediado em 2026-06-02
**Tempo de exposição:** ~24 dias

---

## O que aconteceu

`/opt/zabbix/m365_collector.py` foi criado em 09/05/2026 com três
credenciais M365 hardcoded no corpo do arquivo:

```
TENANT_ID = "e11a96e5-..."   # tenant inteiro
CLIENT_ID = "46d42f0b-..."   # app registration
CLIENT_SECRET = "pK78Q~..."  # secret com Read a users, MFA, Secure Score
```

O arquivo tinha permissões `755` (executável por todos) e pertencia a
`root:root`, mas qualquer usuário do sistema podia ler o conteúdo.

## Como foi descoberto

Durante auditoria de coletores legados para informar ADR-009
(Sprint 12 Architecture), o arquivo foi inspecionado para catalogar
endpoints e padrões de autenticação em uso.

## Superfície de exposição mapeada

| Local | Tipo | Status |
|-------|------|--------|
| `/opt/zabbix/m365_collector.py` | Hardcoded literal | ✅ Remediado |
| `/home/zabbix/.bash_history` | 5 linhas com refs | Histórico — rotacionar |
| `/home/zabbix/backup-systemd-20260526-235635/env.snapshot` | Snapshot de env | Contem vars legítimas do .env |
| Logs de conversa Claude Code (locais) | Context do LLM | Não transmitidos externamente |

**O secret NÃO estava:**
- Em nenhum repositório Git (confirmado: `/opt/zabbix` não é git repo)
- Nos arquivos `.env` do projeto principal (esses tinham secrets distintos)

## Achado adicional: múltiplos secrets distintos

Foram encontrados 4 valores de `AZURE_CLIENT_SECRET` distintos para o
mesmo app (`CLIENT_ID: 46d42f0b-...`) distribuídos em diferentes arquivos.
Isso indica rotações anteriores sem limpeza dos secrets antigos no Entra ID.
**Ação:** auditar no portal Entra ID quantos secrets estão ativos e
invalidar todos exceto o atual.

## Ações de remediação executadas

| # | Ação | Status |
|---|------|--------|
| 1 | Mapeamento de exposição (find, grep, git check) | ✅ |
| 2 | Backup protegido do arquivo original (`chmod 600`) | ✅ |
| 3 | Remoção do hardcoded — substituído por `_load_env_file("/opt/zabbix/m365/.env")` | ✅ |
| 4 | Validação: sintaxe Python OK, sem literal de credencial | ✅ |
| 5 | Gerar novo secret no Entra ID (`prod-2026-06-02-rotation`, 6 meses) | ✅ |
| 6 | Atualizar `/opt/zabbix/m365/.env` com novo secret | ✅ |
| 7 | Invalidar todos os secrets antigos no Entra ID (4 secrets removidos) | ✅ |
| 8 | Auditoria sign-in logs (30 dias): **limpa, sem atividade suspeita** | ✅ |

**Status: ✅ FECHADO em 2026-06-02T23:59 BRT — MTTR ~2h30min**

Ver timeline completa: `docs/incidents/LL-003-secret-rotation.md`

6. Deletar TODOS os secrets antigos (inclusive pK78Q~...)

7. Monitoring → Sign-in logs → Service principal sign-ins
   Filtrar: aplicação 46d42f0b-... | últimos 30 dias
   → Procurar IPs fora do range Brasil/datacenter
   → Horários anômalos | falhas seguidas de sucesso
```

## Causa raiz

1. **Ausência de padrão estabelecido** para scripts em `/opt/zabbix/`
   (nenhum pre-commit, nenhum code review, nenhum template)
2. **Criado como "script rápido"** sem revisão de segurança
3. **Múltiplos `.env` no sistema** sem inventário centralizado
4. **Sem rotação automática** — secret de maio ainda válido em junho

## Ações preventivas

- [x] ADR-009 v2 documenta: credentials SEMPRE via env, nunca hardcoded
- [x] ADR-011 marca coletor para deprecação em Sprint 13
- [ ] Implementar `gitleaks` em pre-commit hooks GLOBAL do servidor
- [ ] Criar inventário de todos os `.env` e rotação trimestral
- [ ] Sprint 13: avaliar Azure Key Vault para o novo código M365
- [ ] Documentar no onboarding: scripts em `/opt/` exigem code review

## Lição aprendida

**"Scripts de PoC viram produção mais rápido do que os controles de segurança."**

O coletor foi escrito como exploração pontual, virou produção em
semanas, e ficou sem revisão por um mês. A descoberta foi acidental.

A migração planejada em ADR-011 (Strangler Fig) é a solução permanente:
quando o novo `itgov/integrations/m365/` estiver em produção, este
coletor legado é arquivado e o risco desaparece.

## Referências

- ADR-009 v2 — seção 2 (Auth: env vars obrigatórias)
- ADR-011 — coletor marcado para deprecação Sprint 13
- LL-001 — padrão de documentar quirks no momento da descoberta
