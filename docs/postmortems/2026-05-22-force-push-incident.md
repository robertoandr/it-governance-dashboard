# Postmortem — Force-push acidental no main

**Data do incidente:** 2026-05-22 ~23:00 BRT
**Detecção:** 2026-05-23 13:30 BRT
**Resolução:** 2026-05-23 14:30 BRT
**Duração:** ~14h
**Severidade:** 🟡 Média
**Autor:** Roberto Andrade

## 📋 Resumo
Force-push acidental no `main` reverteu o merge do PR #11, bloqueando o CI de
10 PRs do Dependabot por ~14h. Sem perda de dados (reflog preservou tudo).

## 🕵️ Timeline (UTC-3)
| Hora | Evento |
|---|---|
| 22/05 17:00 | Branch fix criada (`546502e`) |
| 22/05 22:00 | PR #11 mergeado → main em `1b3214e` |
| 22/05 ~23:00 | 🚨 Force-push reverte main para `8590b3c` |
| 23/05 13:30 | Discrepância detectada |
| 23/05 14:05 | Restauração via push normal |
| 23/05 14:30 | Branch protection ativada |

## 🎯 Causa Raiz
- **Imediata:** `git push --force` no main
- **Contribuinte:** ausência de branch protection
- **Latente:** sem processo formal para operações destrutivas

## 💥 Impacto
- 10 PRs do Dependabot bloqueados
- 0 perda de dados
- 0 usuários afetados (dev)

## 🛠️ Ações Corretivas

### ✅ Implementadas
- [x] Restauração do commit `1b3214e`
- [x] Branch protection via API:
  - `allow_force_pushes: false`
  - `allow_deletions: false`
  - `required_linear_history: true`
  - `enforce_admins: true`

### 📋 Backlog
- [ ] Pre-push hook local
- [ ] CONTRIBUTING.md
- [ ] Script IaC versionado

## 📚 Lições Aprendidas

### ✅ Funcionou
- Reflog preservou histórico
- Activity log confirmou hipóteses
- Recuperação trivial

### ❌ Falhou
- Sem proteção em projeto de Governança (ironia!)
- Sem alertas
- Sem hooks defensivos

## 🎓 Takeaways
1. Defense in depth (servidor + cliente + processo)
2. Observabilidade salva vidas
3. Postmortem blameless gera aprendizado
4. IaC para configurações de governança

> _"Hope is not a strategy. Branch protection is."_
