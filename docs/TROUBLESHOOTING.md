
---

## Bug #5: Stashes Perdidos em `git reset --hard`

### Sintoma
Após `git reset --hard`, mudanças em arquivos como app.py,
static/*, templates/* "desaparecem" do working tree.

### Causa Raiz
Sequência destrutiva:
1. `git stash` cria commits órfãos (refs/stash)
2. `git rebase --abort` pode dropar a stash ref
3. `git reset --hard` apaga working tree
4. Commits da stash ficam **dangling** (sem ref apontando)

### Diagnóstico
```bash
# Lista TUDO que está pendurado
git fsck --lost-found

# Pra cada dangling commit:
git show <sha> --stat
```

### Identificação de Stash Órfã
Mensagens com padrão:
- `WIP on <branch>: <sha> <mensagem>`
- `On <branch>: <descrição>`
- `index on <branch>: <sha> <mensagem>`

### Recuperação Completa
```bash
# 1. Inspeciona
git show <sha> --stat

# 2. Cria branch de resgate
git branch rescue/<descrição> <sha>

# 3. Cherry-pick por arquivo
git checkout <sha> -- path/to/file

# 4. OU restaura como stash novamente
git stash apply <sha>
```

### Prevenção (REGRA DE OURO)
```bash
# SEMPRE antes de operação destrutiva:

# 1. Branch de backup
git branch backup/$(date +%Y%m%d-%H%M)

# 2. Stash explícito com nome claro
git stash push -m "Backup pré-reset - $(date +%H:%M)"

# 3. Lista stashes pra confirmar
git stash list

# SÓ ENTÃO faz o que precisa
```
