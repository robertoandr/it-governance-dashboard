# ADR-002: Verification Rigor — Criterios Obrigatorios de Verificacao de PRs

**Status:** Aceita
**Data:** 02/06/2026
**Autor:** Roberto Andrade
**Issue:** #74
**Revisores:** (a preencher no PR)

---

## Contexto

O hotfix `4933654` (02/06/2026) foi necessario para eliminar um teste flaky causado
por interacao entre `importlib.reload` e a biblioteca `responses` no suite de testes
dos collectors. O teste era tecnicamente valido, mas nao estavel em todos os ambientes
de execucao do CI.

Esse incidente revelou uma lacuna de processo: nao havia criterios formais definindo
o que constitui um PR "pronto para merge". Em um projeto de bus factor 1, a ausencia
de checklist formal aumenta o risco de regressoes silenciosas e de deploys com
qualidade inconsistente.

Problemas especificos identificados:

- PRs de hotfix podem bypassar revisao humana sob pressao de tempo
- Nao havia declaracao explicita de que smoke test manual foi realizado
- O changelog e docs atualizacao eram opcionais na pratica

---

## Decisao

**Todo PR mergeado em `main` deve satisfazer obrigatoriamente os 5 criterios abaixo:**

| # | Criterio                   | Descricao                                                       |
|---|----------------------------|-----------------------------------------------------------------|
| 1 | Testes verdes              | `pytest` verde no CI, sem skips nao justificados                |
| 2 | Lint + format              | `ruff check .` e `ruff format --check .` sem erros             |
| 3 | Smoke manual               | Autor declarou explicitamente que testou o fluxo afetado       |
| 4 | Review humano              | Pelo menos 1 aprovacao no GitHub (mesmo sendo solo dev)        |
| 5 | Docs + changelog           | CHANGELOG.md e docstrings atualizados se interface publica mudou|

---

## Aplicacao por Tipo de PR

| Tipo de PR  | C1 Testes | C2 Lint | C3 Smoke | C4 Review | C5 Docs | Observacao                              |
|-------------|-----------|---------|----------|-----------|---------|------------------------------------------|
| Feature     | Obrig     | Obrig   | Obrig    | Obrig     | Obrig   | Caminho padrao completo                  |
| Bugfix      | Obrig     | Obrig   | Obrig    | Obrig     | Opcional| Docs so se interface publica mudar       |
| Hotfix P0/P1| Obrig*    | Obrig   | Obrig    | Recomendado| Obrig  | *Ver clausula de bypass abaixo           |
| Docs        | N/A       | N/A     | N/A      | Obrig     | Obrig   | Apenas revisao e docs                    |
| Refactor    | Obrig     | Obrig   | Obrig    | Obrig     | Opcional| Smoke cobre regressao comportamental     |

### Clausula de Bypass para Hotfixes P0/P1

Em situacoes de incidente ativo (producao impactada), um hotfix P0 ou P1 pode ser
mergeado com criterio C4 (review humano) relaxado para "self-approve com justificativa",
desde que:

1. O PR inclua a tag `hotfix` no titulo
2. O campo "Smoke Test" da descricao esteja preenchido
3. Um post-mortem seja aberto em ate 48 horas apos o merge documentando:
   - Causa raiz
   - Por que bypass foi necessario
   - Acoes preventivas para evitar recorrencia

---

## Implementacao

### Template de PR

Criar `.github/pull_request_template.md` com a seguinte estrutura:

```markdown
## Descricao

<!-- O que este PR faz e por que -->

## Tipo de Mudanca

- [ ] feat — nova funcionalidade
- [ ] fix — correcao de bug
- [ ] hotfix — correcao urgente em producao
- [ ] docs — apenas documentacao
- [ ] refactor — sem mudanca de comportamento
- [ ] chore — manutencao (deps, CI, etc.)

## Checklist de Verificacao (ADR-002)

- [ ] C1: `pytest` verde no CI (sem skips nao justificados)
- [ ] C2: `ruff check .` e `ruff format --check .` sem erros
- [ ] C3: Smoke test manual realizado — fluxo afetado testado localmente
- [ ] C4: Aprovacao de review recebida (ou self-approve com justificativa para hotfix)
- [ ] C5: CHANGELOG.md atualizado (se interface publica mudou)

## Smoke Test Realizado

<!-- Descreva brevemente o que foi testado manualmente -->

## Issues Relacionadas

Closes #
```

### CI Workflow

Adicionar step no `ci.yml` que falha se o PR nao incluir ao menos um dos labels
`feat`, `fix`, `hotfix`, `docs`, `refactor` ou `chore`. Isso forca categorizacao
e facilita geracao automatica de changelog.

### Declaracao Manual

O criterio C3 (smoke manual) e verificado por declaracao do autor no template do PR.
Nao e automatizavel completamente — e uma declaracao de responsabilidade profissional.
Em sprints futuras, testes E2E com Playwright (previstos para Sprint 14) complementarao
esse criterio com validacao automatizada.

---

## Consequencias

### Positivas

- Processo explicito reduz incidentes como o hotfix `4933654`
- Template de PR padroniza comunicacao e facilita code review
- Clausula de bypass para hotfixes mantem agilidade sem eliminar responsabilidade
- Post-mortems criam base de conhecimento sobre incidentes

### Negativas

- Adiciona atrito a PRs pequenos (docs, chores) que precisam preencher o template
- Review humano obrigatorio em projeto solo significa self-review — reduz o valor
  do criterio ate que o time cresça
- Post-mortem em 48h pode ser dificil de cumprir em periodos de alta demanda

---

## Alternativas Consideradas

| Alternativa                           | Motivo da Rejeicao                                            |
|---------------------------------------|---------------------------------------------------------------|
| Apenas CI automatizado (sem checklist)| Nao cobre smoke manual nem atualizacao de docs                |
| Checklist opcional (sem enforcement)  | Historico mostrou que opcionais sao ignorados sob pressao     |
| Branch protection com 2 reviewers     | Inviavel para projeto solo — travaria todos os PRs            |

---

## Historico de Revisoes

| Data       | Autor           | Alteracao              |
|------------|-----------------|------------------------|
| 02/06/2026 | Roberto Andrade | Versao inicial aceita  |
