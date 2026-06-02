# ADR-003 — Convenções de REST API

**Status:** Accepted  
**Data:** 2026-06-02  
**Autores:** Sprint 11 (Issues #86, #87)  
**Contexto:** PR #98 estabeleceu o primeiro CRUD REST completo do projeto.
Este ADR formaliza as convenções antes de Sprint 12 (M365 Graph) multiplicar
o número de endpoints.

---

## Problema

Sem convenções explícitas, cada namespace Flask-RESTX tende a adotar
padrões ligeiramente diferentes de response format, paginação, error codes
e soft delete. Isso torna a API imprevisível para consumers (frontend, scripts).

---

## Decisão

### 1. Versionamento de URL

Todos os endpoints sob `/api/v1/`. Mudanças breaking → `/api/v2/`.
Coexistência de versões por no mínimo 1 sprint antes de deprecar.

### 2. Estrutura de namespace

| Elemento | Localização |
|---------|-------------|
| Namespace Flask-RESTX | `itgov/api/v1/<dominio>.py` |
| Swagger models | definidos no mesmo arquivo |
| Session factory | `itgov/db/session.py` via `_svc()` |

### 3. Convenções de response

#### Sucesso

| Operação | HTTP | Body |
|----------|------|------|
| GET (item) | 200 | objeto serializado |
| GET (lista) | 200 | `{items, total, limit, offset}` |
| POST (criação) | 201 | objeto criado |
| PATCH (atualização) | 200 | objeto atualizado |
| DELETE | 204 | body vazio |
| GET (stats/agregados) | 200 | objeto de stats |

#### Erro — formato canônico

```json
{
  "error": "mensagem legível por humano",
  "code":  "ENUM_LEGÍVEL_POR_MÁQUINA"
}
```

#### Códigos de erro padronizados

| `code` | HTTP | Quando |
|--------|------|--------|
| `NOT_FOUND` | 404 | Recurso não existe ou soft-deleted |
| `DUPLICATE` | 409 | Violação de unique constraint |
| `INVALID_PAYLOAD` | 400 | Falha na validação Pydantic |
| `INVALID_UUID` | 400 | UUID malformado no path param |
| `CONFLICT` | 409 | Update violaria constraint |
| `UNAUTHORIZED` | 401 | Sem token ou token inválido |
| `FORBIDDEN` | 403 | Token válido mas sem permissão |
| `RATE_LIMITED` | 429 | Rate limit atingido |
| `UPSTREAM_ERROR` | 502 | Falha em API externa (Graph, Zabbix…) |

### 4. Paginação

Query params: `limit` (default 100, max 1000) e `offset` (default 0).

Response envelope obrigatório em listagens:

```json
{
  "items":  [...],
  "total":  42,
  "limit":  100,
  "offset": 0
}
```

Para APIs com cursor (ex: Graph): incluir `next_cursor` opaco quando `has_more=true`.

### 5. Soft delete (alinha com ADR-0003)

- `DELETE /resource/{id}` → soft delete por padrão (`deleted_at = now()`)
- `DELETE /resource/{id}?hard=true` → remoção física (futuro: role admin)
- `GET /resources` → exclui soft-deleted por padrão
- `GET /resources?include_deleted=true` → inclui soft-deleted

### 6. Padrão de implementação Flask-RESTX

Usar `@ns.response(code, desc, model)` para documentação + tuplas `(dict, status_code)`
para controle de runtime em rotas com múltiplos status codes.

**Não usar** `@ns.marshal_with` em rotas com caminhos de erro (ver LL-001).

```python
# Rota sem caminho de erro — marshal_with OK
@ns.route("/stats")
class StatsResource(Resource):
    @ns.marshal_with(stats_model)
    def get(self):
        return svc.get_stats()

# Rota COM caminhos de erro — usar @ns.response + tuplas
@ns.route("/<string:id>")
class ItemResource(Resource):
    @ns.response(200, "Sucesso", item_model)
    @ns.response(404, "Não encontrado", error_model)
    def get(self, id: str):
        try:
            return _serialize(svc.get(UUID(id))), 200
        except NotFoundError as exc:
            return {"error": str(exc), "code": "NOT_FOUND"}, 404
```

### 7. Validação de input

Pydantic v2 via service layer — não usar `validate=True` no `@ns.expect()`
(incompatível com jsonschema na versão atual).

---

## Consequências

**Positivas:**
- Consumers do frontend sabem exatamente o que esperar
- Testes de API têm asserções uniformes (`.status_code` + `.get_json()["code"]`)
- Onboarding de novos endpoints é previsível

**Negativas / Trade-offs:**
- Cria overhead para endpoints triviais (ex: soft delete + include_deleted em toda coleção)
- `LL-001` exige padrão de código mais verboso que `@marshal_with` sozinho

---

## Revisão prevista

Revisar antes de Sprint 12 se M365 Graph exigir padrões de cursor-pagination
ou autenticação que conflitem com as convenções acima.

---

## Referências

- [LL-001](../lessons-learned/001-flask-restx-marshal-with.md) — Quirk do marshal_with
- `itgov/api/v1/ativos.py` — implementação de referência
- ADR-0003 — LGPD e soft delete
- ADR-002 — Verification rigor (checklist de PR)
