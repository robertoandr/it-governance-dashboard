#!/usr/bin/env bash
# Setup do ambiente de pre-commit hooks — IT Governance Dashboard
# Uso: ./scripts/setup-precommit.sh
# Requisito: gitleaks instalado (ver docs/security/gitleaks-setup.md)

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

log()  { echo -e "${GREEN}[✓]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err()  { echo -e "${RED}[✗]${NC} $1" >&2; }
info() { echo -e "${CYAN}[i]${NC} $1"; }

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  🔐 Setup pre-commit + gitleaks — IT Governance Dashboard"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# ── 1. Valida pré-requisitos ──────────────────────────────────

info "Verificando pré-requisitos..."

if ! command -v gitleaks >/dev/null 2>&1; then
    err "gitleaks não encontrado. Instale primeiro:"
    echo ""
    echo "  GITLEAKS_VERSION=8.21.2"
    echo "  wget https://github.com/gitleaks/gitleaks/releases/download/v\${GITLEAKS_VERSION}/gitleaks_\${GITLEAKS_VERSION}_linux_x64.tar.gz"
    echo "  tar -xzf gitleaks_*.tar.gz && sudo mv gitleaks /usr/local/bin/"
    echo ""
    echo "  Documentação: docs/security/gitleaks-setup.md"
    exit 1
fi

if ! command -v pre-commit >/dev/null 2>&1; then
    err "pre-commit não encontrado."
    echo "  Instale: pip install pre-commit==4.0.1"
    exit 1
fi

if [[ ! -f ".gitleaks.toml" ]]; then
    err ".gitleaks.toml não encontrado. Execute a partir da raiz do projeto."
    exit 1
fi

if [[ ! -f ".pre-commit-config.yaml" ]]; then
    err ".pre-commit-config.yaml não encontrado."
    exit 1
fi

log "gitleaks $(gitleaks version) OK"
log "pre-commit $(pre-commit --version) OK"
log ".gitleaks.toml encontrado"

# ── 2. Instala hooks ──────────────────────────────────────────

info "Instalando pre-commit hooks (pre-commit + pre-push + commit-msg)..."
pre-commit install --install-hooks
pre-commit install --hook-type pre-push
pre-commit install --hook-type commit-msg
log "Hooks instalados em .git/hooks/"

# ── 3. Scan inicial em todos os arquivos ──────────────────────

echo ""
warn "Rodando scan inicial em todos os arquivos..."
warn "(pode demorar 30-60s na primeira execução — downloads de environments)"
echo ""

if pre-commit run --all-files 2>&1; then
    log "Todos os hooks passaram no scan inicial! ✨"
else
    HOOK_EXIT=$?
    echo ""
    warn "Alguns hooks reportaram issues (exit $HOOK_EXIT)."
    warn "Isso é esperado em instalação nova — corrija os erros e rode novamente."
    warn "Para ver apenas erros de secrets: pre-commit run gitleaks --all-files"
fi

# ── 4. Scan profundo do histórico git ─────────────────────────

echo ""
warn "Escaneando histórico completo do git com Gitleaks..."
warn "(scan pode demorar em repos com histórico longo)"
echo ""

if gitleaks detect --source . --config .gitleaks.toml; then
    log "Nenhum secret detectado no histórico git! 🎉"
else
    echo ""
    err "═══════════════════════════════════════════════════"
    err "ATENÇÃO: SECRETS DETECTADOS NO HISTÓRICO DO GIT!"
    err "═══════════════════════════════════════════════════"
    err ""
    err "Ações imediatas necessárias:"
    err "  1. Rotacionar TODOS os secrets expostos AGORA"
    err "  2. Usar BFG Repo-Cleaner para limpar o histórico:"
    err "     https://rtyley.github.io/bfg-repo-cleaner/"
    err "  3. Documentar no formato LL-NNN em docs/lessons-learned/"
    err ""
    err "Veja: docs/incidents/LL-003-secret-rotation.md (exemplo)"
    exit 1
fi

# ── 5. Resumo ─────────────────────────────────────────────────

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
log "Setup completo! 🚀"
echo ""
info "Hooks ativos:"
echo "    • pre-commit : ruff, gitleaks, detect-private-key, ..."
echo "    • pre-push   : mesmos hooks (linha de defesa extra)"
echo "    • commit-msg : (sem hook configurado — adicionar se quiser CC)"
echo ""
info "Comandos úteis:"
echo "    pre-commit run --all-files          # scan completo"
echo "    pre-commit run gitleaks --all-files # só gitleaks"
echo "    gitleaks detect --source . -v       # verbose direto"
echo ""
info "Bypass de emergência (usar com cuidado):"
echo "    git commit --no-verify"
echo ""
info "Documentação completa: docs/security/gitleaks-setup.md"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
