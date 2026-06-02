# ADR-001: Coverage Policy — Piso e Metas de Cobertura de Testes

**Status:** Aceita
**Data:** 02/06/2026
**Autor:** Roberto Andrade
**Issue:** #73
**Revisores:** (a preencher no PR)

---

## Contexto

Durante a Sprint 10H (V1.0), o projeto atingiu 88.21% de cobertura de testes com 215 casos.
Esse resultado foi positivo, mas evidenciou a ausencia de uma politica formal que:

- Defina um piso minimo que bloqueie o CI em caso de regressao
- Estabeleca uma meta de melhoria continua para V1.1
- Oriente como tratar PRs que reduzem coverage
- Especifique exclusoes legitimas (scripts de migracao, codigo gerado, etc.)

Sem essa politica, e possivel que coverage regrida silenciosamente entre sprints, o que
foi identificado como risco no code review que gerou as issues #73-#78.

O hotfix `4933654` (eliminacao de teste flaky) reacendeu a discussao: um teste removido
sem substituto adequado reduz coverage. A politica precisa cobrir esse cenario.

---

## Decisao

**Dois niveis de enforcement:**

| Nivel   | Threshold | Acao                                    |
|---------|-----------|-----------------------------------------|
| Piso    | 85%       | Bloqueia CI — PR nao pode ser mergeado  |
| Alvo    | 90%       | Meta da V1.1 — verificada manualmente   |

O piso de 85% e aplicado via `pytest --fail-under=85` no workflow de CI (ja parcialmente
implementado pelo PR #81, que elevou o threshold de 60% para 80%; este ADR formaliza
o piso definitivo em 85% e estabelece o alvo em 90%).

---

## Regras Complementares

### Novos modulos

Todo novo modulo Python introduzido por um PR deve atingir >= 90% de coverage
individualmente antes do merge. O racional e que novos modulos nao herdam divida tecnica
e devem entrar ja no nivel alvo.

### PRs que reduzem coverage

PRs que reduzam a coverage global em mais de 0.5 pontos percentuais exigem justificativa
explicita na descricao do PR. A justificativa deve indicar:
- Por que o codigo adicionado e dificil de testar (ex: integracao externa sem mock)
- Qual e o plano para cobrir o codigo em sprint subsequente

### Branch coverage

O CI verifica coverage de linhas (`--cov-report=term-missing`). Branch coverage (decisoes
condicionais) nao e obrigatoria no CI, mas e recomendada para modulos criticos
(autenticacao, validacao de contratos, calculo de SLA).

### Exclusoes permitidas

As seguintes categorias podem ser excluidas da medicao via `# pragma: no cover` ou
configuracao em `pyproject.toml`:

| Categoria                        | Justificativa                                      |
|----------------------------------|----------------------------------------------------|
| Scripts de migracao Alembic      | Executados em ambiente de BD, nao em pytest        |
| Bloco `if __name__ == "__main__"` | Ponto de entrada, nao logica de negocio            |
| Stubs de tipo (`.pyi`)           | Nao contem logica executavel                       |
| Handlers de sinal (SIGTERM, etc.)| Requerem simulacao de processo complexa            |

O uso de `# pragma: no cover` deve ser justificado em comentario na mesma linha.

---

## Consequencias

### Positivas

- CI bloqueia regressoes de coverage antes do merge — feedback rapido ao desenvolvedor
- Meta de 90% eleva a qualidade geral dos testes da V1.1
- Politica documentada reduz discussoes ad-hoc sobre "quanto coverage e suficiente"
- Novos modulos entram com alta coverage, melhorando a media ao longo do tempo

### Negativas

- PRs com integracao de sistemas externos (Graph API, Zabbix, Zendesk) podem exigir
  mais esforco para mockar adequadamente
- O piso de 85% e mais restritivo que o CI anterior (60% → 80% → 85%), o que pode
  atrasar PRs que introduzam codigo complexo de testar
- Exclusoes via `pragma: no cover` precisam ser revisadas em code review para evitar abuso

---

## Alternativas Consideradas

| Alternativa                          | Motivo da Rejeicao                                              |
|--------------------------------------|-----------------------------------------------------------------|
| Manter threshold em 80%              | Nao cria pressao suficiente para atingir 90% na V1.1           |
| Threshold unico em 90% (piso = alvo) | Muito restritivo para a transicao — bloquearia PRs legitimos    |
| Sem threshold no CI (apenas relatorio)| Historico mostrou que metricas sem enforcement sao ignoradas   |

---

## Implementacao

O CI guard foi parcialmente implementado pelo PR #81 (`chore(ci): raise coverage threshold
from 60% to 80%`). Esta ADR formaliza a intencao e determina:

1. Elevar o `--fail-under` de 80% para 85% no workflow `ci.yml` (Sprint 11)
2. Adicionar comentario no workflow referenciando este ADR
3. Documentar exclusoes permitidas em `pyproject.toml` (secao `[tool.coverage.report]`)
4. Revisar PRs #73-#78 a luz desta politica

---

## Historico de Revisoes

| Data       | Autor           | Alteracao              |
|------------|-----------------|------------------------|
| 02/06/2026 | Roberto Andrade | Versao inicial aceita  |
