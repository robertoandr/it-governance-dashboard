#!/usr/bin/env bash
# Instala hooks defensivos no repositório local

set -euo pipefail

HOOK_DIR=".git/hooks"

echo "🛡️  Instalando hooks de proteção..."

# ═══ Hook 1: Pre-push (bloqueia force-push no main) ═══
cat > "$HOOK_DIR/pre-push" << 'PREPUSH'
#!/usr/bin/env bash
# Bloqueia push --force/--force-with-lease nas branches protegidas

PROTECTED_BRANCHES="main master production"
CURRENT_BRANCH=$(git symbolic-ref HEAD | sed 's!refs/heads/!!')

# Detecta force-push
if [[ "${GIT_PUSH_OPTION_COUNT:-0}" -gt 0 ]] || \
   [[ "$*" == *"--force"* ]] || \
   [[ "$*" == *"-f"* ]]; then
  for protected in $PROTECTED_BRANCHES; do
    if [[ "$CURRENT_BRANCH" == "$protected" ]]; then
      echo ""
      echo "╔══════════════════════════════════════════════════════════╗"
      echo "║  🚨 FORCE-PUSH BLOQUEADO no branch '$protected'                ║"
      echo "╚══════════════════════════════════════════════════════════╝"
      echo ""
      echo "  Para forçar mesmo assim (NÃO RECOMENDADO):"
      echo "    git push --no-verify --force origin $protected"
      echo ""
      exit 1
    fi
  done
fi

exit 0
PREPUSH

chmod +x "$HOOK_DIR/pre-push"
echo "  ✅ pre-push instalado"

# ═══ Hook 2: Pre-commit (bloqueia commit direto no main) ═══
cat > "$HOOK_DIR/pre-commit" << 'PRECOMMIT'
#!/usr/bin/env bash
# Avisa quando commit é feito diretamente em branches protegidas

PROTECTED_BRANCHES="main master production"
CURRENT_BRANCH=$(git symbolic-ref HEAD | sed 's!refs/heads/!!')

for protected in $PROTECTED_BRANCHES; do
  if [[ "$CURRENT_BRANCH" == "$protected" ]]; then
    echo ""
    echo "⚠️  AVISO: você está commitando diretamente no '$protected'"
    echo "    Recomendado: usar branch + PR"
    echo ""
    echo "    Continuar mesmo assim? (s/N)"
    read -r resposta </dev/tty
    [[ "$resposta" =~ ^[sS]$ ]] || exit 1
  fi
done

exit 0
PRECOMMIT

chmod +x "$HOOK_DIR/pre-commit"
echo "  ✅ pre-commit instalado"

echo ""
echo "🧪 Teste de fogo (deve FALHAR):"
echo "    git push --force origin main"
echo ""
echo "✅ Hooks ativos! Erros humanos agora são bloqueados localmente."
