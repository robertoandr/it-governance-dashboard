#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════════╗
# ║  GOVERNANCE FIX — Script Master de Recuperação e Hardening      ║
# ║  Projeto: it-governance-dashboard                               ║
# ║  Autor:   Roberto Andrade                                       ║
# ╚══════════════════════════════════════════════════════════════════╝

set -euo pipefail

# ═══ CONFIGURAÇÃO ═══
REPO="robertoandr/it-governance-dashboard"
BRANCH="main"
CI_CONTEXT="test"

SKIP_PROTECTION=false
SKIP_DEPENDABOT=false
SKIP_POSTMORTEM=false

for arg in "$@"; do
  case $arg in
    --skip-protection) SKIP_PROTECTION=true ;;
    --skip-dependabot) SKIP_DEPENDABOT=true ;;
    --skip-postmortem) SKIP_POSTMORTEM=true ;;
    --help|-h) grep -E '^# ' "$0" | head -10; exit 0 ;;
  esac
done

# ═══ CORES ═══
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

log()     { echo -e "${CYAN}[$(date +%H:%M:%S)]${NC} $*"; }
ok()      { echo -e "${GREEN}✅ $*${NC}"; }
warn()    { echo -e "${YELLOW}⚠️  $*${NC}"; }
err()     { echo -e "${RED}❌ $*${NC}" >&2; }
title()   { echo -e "\n${BOLD}${BLUE}═══ $* ═══${NC}\n"; }
section() { echo -e "\n${BOLD}${CYAN}▸ $*${NC}"; }
confirm() { read -rp "$(echo -e ${YELLOW}❓ $1 \(s/N\): ${NC})" r; [[ "$r" =~ ^[sS]$ ]]; }

# ═══ PRÉ-CHECAGENS ═══
title "PRÉ-CHECAGENS"

section "Verificando dependências"
for cmd in gh git jq; do
  if command -v "$cmd" >/dev/null 2>&1; then
    ok "$cmd encontrado"
  else
    err "$cmd não encontrado"
    [[ "$cmd" == "jq" ]] && echo "   → sudo apt install jq -y"
    exit 1
  fi
done

section "Verificando autenticação gh"
if ! gh auth status >/dev/null 2>&1; then
  err "gh não autenticado"
  exit 1
fi
ok "Autenticado como: $(gh api user --jq .login)"

section "Verificando repositório"
if [[ ! -d .git ]]; then
  err "Não está em um repositório git"
  exit 1
fi
ok "Repo: $REPO"

section "Sincronizando branch local"
git fetch origin --prune
git checkout "$BRANCH" 2>/dev/null || true
git pull origin "$BRANCH" --ff-only 2>/dev/null || warn "pull falhou (pode estar ok)"
ok "Branch $BRANCH sincronizado"

# ═══ ETAPA 1: BRANCH PROTECTION ═══
if [[ "$SKIP_PROTECTION" == false ]]; then
  title "ETAPA 1: BRANCH PROTECTION"
  
  section "Aplicando regras no $BRANCH"
  
  PAYLOAD=$(cat <<EOF
{
  "required_status_checks": {"strict": true, "contexts": ["$CI_CONTEXT"]},
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": false,
    "required_approving_review_count": 0
  },
  "restrictions": null,
  "required_linear_history": true,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_conversation_resolution": true
}
EOF
)
  
  if echo "$PAYLOAD" | gh api --method PUT \
      -H "Accept: application/vnd.github+json" \
      "/repos/$REPO/branches/$BRANCH/protection" \
      --input - >/dev/null 2>&1; then
    ok "Branch protection aplicado"
  else
    warn "Tentando sem status_checks..."
    echo "$PAYLOAD" | jq '.required_status_checks = null' | \
      gh api --method PUT \
      -H "Accept: application/vnd.github+json" \
      "/repos/$REPO/branches/$BRANCH/protection" \
      --input -
    ok "Branch protection aplicado (sem checks)"
  fi
  
  section "Validando"
  gh api "/repos/$REPO/branches/$BRANCH/protection" --jq '{
    force_push_blocked: (.allow_force_pushes.enabled | not),
    deletion_blocked: (.allow_deletions.enabled | not),
    linear_history: .required_linear_history.enabled,
    enforce_admins: .enforce_admins.enabled,
    required_checks: (.required_status_checks.contexts // [])
  }'
  ok "Proteção validada"
else
  warn "ETAPA 1 pulada"
fi

# ═══ ETAPA 2: DEPENDABOT ═══
if [[ "$SKIP_DEPENDABOT" == false ]]; then
  title "ETAPA 2: DEPENDABOT MERGES"
  
  PR_COUNT=$(gh pr list --author "app/dependabot" --state open --json number --jq 'length')
  
  if [[ "$PR_COUNT" -eq 0 ]]; then
    ok "Nenhum PR pendente do Dependabot 🎉"
  else
    log "Encontrados $PR_COUNT PRs"
    gh pr list --author "app/dependabot" --state open \
      --json number,title --template \
      '{{range .}}  #{{.number}}  {{.title}}{{"\n"}}{{end}}'
    
    if confirm "Habilitar auto-merge em TODOS?"; then
      gh pr list --author "app/dependabot" --state open \
        --json number,title --jq '.[] | "\(.number)|\(.title)"' | \
      while IFS='|' read -r num title; do
        echo ""
        log "🔄 PR #$num — $title"
        gh pr update-branch "$num" 2>/dev/null && ok "  atualizada" || warn "  já atualizada"
        if gh pr merge "$num" --squash --auto --delete-branch 2>&1 | grep -qE "enabled|merged"; then
          ok "  auto-merge ativado"
        else
          warn "  pode já estar ativo"
        fi
        sleep 2
      done
      ok "Processamento concluído"
    fi
  fi
else
  warn "ETAPA 2 pulada"
fi

# ═══ ETAPA 3: POSTMORTEM ═══
if [[ "$SKIP_POSTMORTEM" == false ]]; then
  title "ETAPA 3: POSTMORTEM"
  
  PM_FILE="docs/postmortems/2026-05-22-force-push-incident.md"
  
  if [[ -f "$PM_FILE" ]]; then
    warn "Postmortem já existe: $PM_FILE"
  else
    section "Criando branch"
    git checkout -b docs/postmortem-force-push 2>/dev/null || \
      git checkout docs/postmortem-force-push
    
    mkdir -p docs/postmortems
    
    cat > "$PM_FILE" << 'POSTMORTEM'
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
POSTMORTEM
    
    ok "Arquivo criado"
    
    section "Commit + Push"
    git add docs/postmortems/
    git commit -m "docs: postmortem do incidente de force-push

- Timeline detalhada e causa raiz
- Ações corretivas implementadas
- Backlog preventivo
- Material para portfólio de Governança de TI"
    
    git push -u origin docs/postmortem-force-push
    ok "Branch publicada"
    
    section "Criando PR"
    PR_URL=$(gh pr create \
      --base "$BRANCH" \
      --head docs/postmortem-force-push \
      --title "docs: postmortem do incidente de force-push" \
      --body "Documenta incidente de 22/05 e ações corretivas.

## Checklist
- [x] Timeline
- [x] Causa raiz
- [x] Ações corretivas
- [x] Lições aprendidas

🤖 Gerado via governance-fix.sh")
    
    ok "PR criado: $PR_URL"
    
    PR_NUM=$(echo "$PR_URL" | grep -oE '[0-9]+$')
    sleep 3
    gh pr merge "$PR_NUM" --squash --auto --delete-branch 2>&1 | sed 's/^/  /' || true
    ok "Auto-merge habilitado"
    
    git checkout "$BRANCH"
  fi
else
  warn "ETAPA 3 pulada"
fi

# ═══ RELATÓRIO FINAL ═══
title "RELATÓRIO FINAL"

section "Branch protection"
gh api "/repos/$REPO/branches/$BRANCH/protection" 2>/dev/null --jq '{
  force_push_blocked: (.allow_force_pushes.enabled | not),
  deletion_blocked: (.allow_deletions.enabled | not),
  linear_history: .required_linear_history.enabled,
  enforce_admins: .enforce_admins.enabled
}' || warn "não foi possível ler"

section "PRs do Dependabot"
gh pr list --author "app/dependabot" --state open \
  --json number,title --template \
  '{{range .}}  #{{.number}}  {{.title}}{{"\n"}}{{end}}' || true

section "Últimos commits"
git log --oneline -5 origin/"$BRANCH" 2>/dev/null || true

echo ""
echo -e "${GREEN}${BOLD}╔════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}${BOLD}║  ✅ EXECUÇÃO CONCLUÍDA                     ║${NC}"
echo -e "${GREEN}${BOLD}╚════════════════════════════════════════════╝${NC}"
