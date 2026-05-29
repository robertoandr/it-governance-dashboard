# Tech Debt — Sprint 10 Backlog

Itens identificados durante a Sprint 10 (Fornecedores CRUD) para resolução futura.
Cada item tem prioridade, esforço estimado e critério de aceite.

---

## TD-001 · Paginação cursor-based

| Campo | Valor |
|-------|-------|
| **Status** | Pendente |
| **Prioridade** | Alta |
| **Esforço** | 3 SP |
| **Sprint alvo** | 11 |

**Descrição:** Os endpoints `GET /fornecedores` e futuramente `GET /contratos`
usam paginação offset (`page`/`per_page`). Em tabelas com > 10 k registros,
`OFFSET` cresce linearmente e degrada a performance.

**Solução proposta:** Migrar para paginação cursor-based usando o campo
`created_at` + `id` como cursor opaco (base64), eliminando o `COUNT(*)` extra.

**Critério de aceite:**
- Parâmetro `cursor` substitui `page` na query string.
- Resposta inclui `next_cursor` e `has_more`.
- Testes de performance com 50 k registros (p95 < 50 ms).

---

## TD-002 · Endpoint de reativação de fornecedor

| Campo | Valor |
|-------|-------|
| **Status** | Pendente |
| **Prioridade** | Média |
| **Esforço** | 1 SP |
| **Sprint alvo** | 11 |

**Descrição:** O `DELETE /fornecedores/{id}` realiza soft-delete
(`deleted_at = NOW()`). Não existe endpoint para reverter a operação,
forçando intervenção direta no banco em casos de exclusão acidental.

**Solução proposta:** Implementar `POST /fornecedores/{id}/restore` que
zera `deleted_at` e registra evento de auditoria.

**Critério de aceite:**
- Retorna `200` com o recurso reativado.
- Retorna `409` se o fornecedor já estiver ativo.
- Reativação aparece no log de auditoria (`structlog`).

---

## TD-003 · Rate limiting em endpoints de escrita

| Campo | Valor |
|-------|-------|
| **Status** | Pendente |
| **Prioridade** | Alta |
| **Esforço** | 2 SP |
| **Sprint alvo** | 11 |

**Descrição:** Os endpoints `POST /fornecedores` e `PUT /fornecedores/{id}`
não possuem rate limiting. Um cliente com credenciais válidas pode realizar
bulk-insert irrestrito, impactando disponibilidade e aumentando custo de storage.

**Solução proposta:** Adicionar `Flask-Limiter` com backend Redis
(`10/minute` por IP para POSTs, `30/minute` para PUTs). Configurável via env var
`RATE_LIMIT_POST` e `RATE_LIMIT_PUT`.

**Critério de aceite:**
- `429 Too Many Requests` com header `Retry-After` ao exceder o limite.
- Limite configurável por ambiente (dev sem limite, prod restrito).
- Testes de integração cobrindo o cenário de throttle.

---

*Documento gerado em Sprint 10. Revisar prioridades no início da Sprint 11.*
