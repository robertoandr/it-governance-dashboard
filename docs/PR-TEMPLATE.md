# Fluxo Padrão de PR

Este documento formaliza o fluxo padrão para levar uma mudança até `main`
sem contornar a branch protection. `main` é protegida (`CLAUDE.md` §1) e
exige PR + aprovação — o bypass via admin (usado no lote de 7 branches de
22/09/2026) deve ser exceção justificada, não rotina, exatamente porque ele
pula a espera pelos status checks obrigatórios e permite merge commit sem CI
verde ter sido confirmado antes do push.

## Passo a passo

1. **Criar a branch em um worktree, a partir de `origin/main`**

   No `itgov-dev` o checkout principal (`~/projects/it-governance-dashboard`)
   é o que roda em produção — **nunca trocar de branch nele**. Trabalhar
   sempre em um worktree separado:

   ```bash
   cd ~/projects/it-governance-dashboard
   git fetch origin
   git worktree add -b feat/nome-da-feature ../itgov-nome origin/main   # ou fix/, chore/, docs/, test/
   cd ../itgov-nome
   ```

2. **Commitar com mensagens convencionais** (`CLAUDE.md` §7, item 10)

   ```bash
   git add <arquivos especificos>   # nunca -A nem .
   git commit -m "feat(modulo): descricao curta

   Corpo explicando o que e por que (nao o como).

   Refs #ISSUE"
   ```

3. **Push da branch**

   ```bash
   git push -u origin feat/nome-da-feature
   ```

4. **Abrir PR pela interface web do GitHub**

   Título e descrição seguindo o checklist de [ADR-002](adr/ADR-002-verification-rigor.md).
   O corpo do PR é pré-preenchido pelo template em
   [`.github/PULL_REQUEST_TEMPLATE.md`](../.github/PULL_REQUEST_TEMPLATE.md)
   — preencher todas as seções, não deixar placeholders em branco.

5. **Aguardar os status checks obrigatórios passarem**

   A branch protection exige 7 checks (Lint & Format, Mypy, Tests 3.11 e
   3.12, Security Scan, Smoke Test, CodeQL `Analyze Python`) e branch
   atualizada com `main` (strict). O CI roda outros checks além desses
   (Trivy, Gitleaks, Semgrep, E2E) — conferir todos, não só os obrigatórios.
   Não mergear com check vermelho ou pendente — se um check falhar, corrigir
   e re-push antes de pedir review, não depois. Se `main` andou, atualizar
   com `git merge origin/main` + push (sem rebase/force push).

6. **Rodar `/code-review` e `/security-review` antes do merge**

   Mesmo em projeto solo — captura problemas que o CI não cobre
   (simplificação, reuso, padrões de segurança fora do escopo do Bandit/
   Semgrep/Trivy já automatizados). Como parte do `/security-review`,
   confirmar explicitamente:

   ```bash
   git ls-files | grep -iE "\.key$|\.pem$|\.crt$|\.p12$|\.pfx$|^\.env$|\.env\.[a-z]"
   git check-ignore .env   # deve retornar .env (confirma que esta ignorado)
   ```

   Nenhum resultado real de segredo (arquivos `.example`/`.md`/scripts que
   só mencionam o *nome* de uma variável não contam) — se aparecer algo,
   não mergear até remover do histórico e rotacionar a credencial.

7. **Merge pelo GitHub — nunca `git merge` local + push em `main`**

   Merge local seguido de push direto contorna a branch protection (foi o
   que aconteceu no lote de 22/09/2026, autorizado explicitamente pelo
   autor por ele ter permissão de admin). Usar o botão de merge do GitHub
   ou o `gh`, que passam pela mesma proteção:

   ```bash
   ~/.local/bin/gh pr merge <N> --repo robertoandr/it-governance-dashboard \
     --merge --match-head-commit <sha-completo-do-head>
   ```

   `--match-head-commit` garante que o commit mesclado é o mesmo que teve o
   CI verde (exige o SHA completo de 40 caracteres).

8. **Limpar e fazer o deploy após o merge**

   ```bash
   cd ~/projects/it-governance-dashboard
   git worktree remove ../itgov-nome
   git branch -D feat/nome-da-feature   # o GitHub já apaga a branch remota no merge
   ```

   O deploy (`git pull --ff-only` no checkout principal + `docker compose
   pull app && docker compose up -d app`) só depois do workflow *Docker Build
   & Publish* ficar verde no commit de merge. Nunca usar `--remove-orphans`:
   os containers `zabbix_*` que aparecem como órfãos são o Zabbix de produção.

## Quando o bypass é aceitável

Só em hotfix P0/P1 com produção impactada, seguindo a cláusula de bypass
da [ADR-002](adr/ADR-002-verification-rigor.md#clausula-de-bypass-para-hotfixes-p0p1)
— exige tag `hotfix` no título, campo de smoke test preenchido, e post-mortem
em até 48h. Fora desse cenário, seguir os 8 passos acima.

## Template de PR

O corpo do PR usa o template automático do GitHub em
[`.github/PULL_REQUEST_TEMPLATE.md`](../.github/PULL_REQUEST_TEMPLATE.md).
Não duplicar o conteúdo aqui — se o template mudar, editar só naquele
arquivo para não haver duas versões divergentes.
