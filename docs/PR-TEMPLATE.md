# Fluxo Padrão de PR

Este documento formaliza o fluxo padrão para levar uma mudança até `main`
sem contornar a branch protection. `main` é protegida (`CLAUDE.md` §1) e
exige PR + aprovação — o bypass via admin (usado no lote de 7 branches de
22/09/2026) deve ser exceção justificada, não rotina, exatamente porque ele
pula a espera pelos status checks obrigatórios (5/5 e 7/7) e permite merge
commit sem CI verde ter sido confirmado antes do push.

## Passo a passo

1. **Criar a branch a partir de `main` atualizada**

   ```bash
   git checkout main
   git pull origin main
   git checkout -b feat/nome-da-feature   # ou fix/, chore/, docs/, test/
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

   5 de 5 e 7 de 7 (ver branch protection do repo). Não mergear com check
   vermelho ou pendente — se um check falhar, corrigir e re-push antes de
   pedir review, não depois.

6. **Rodar `/code-review` e `/security-review` antes do merge**

   Mesmo em projeto solo — captura problemas que o CI não cobre
   (simplificação, reuso, padrões de segurança fora do escopo do Bandit/
   Semgrep/Trivy já automatizados).

7. **Merge via interface web do GitHub — nunca `git merge` local + push**

   Merge local seguido de push direto contorna a branch protection (foi o
   que aconteceu no lote de 22/09/2026, autorizado explicitamente pelo
   autor por ele ter permissão de admin). Usar o botão de merge do GitHub
   garante que os status checks foram de fato aguardados e que o histórico
   de PR fica navegável.

8. **Atualizar `main` local após o merge**

   ```bash
   git checkout main
   git pull origin main
   git branch -d feat/nome-da-feature   # limpar branch local mergeada
   ```

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
