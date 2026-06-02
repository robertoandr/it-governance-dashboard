# LL-001 — Flask-RESTX 1.3.x: @marshal_with não respeita objetos Response

**Data:** 2026-06-02  
**Descoberto em:** PR #98 (Issue #86 — REST API /ativos)  
**Severidade:** Média — silent failure sem mensagem de erro clara  
**Contexto:** Rotas Flask-RESTX com múltiplos status codes (200 + 4xx)

---

## Problema

`@ns.marshal_with(model)` no Flask-RESTX 1.3.x tenta serializar
**qualquer** valor retornado pelo método — incluindo objetos `flask.Response`
já construídos. Quando o método retorna um Response (ex: `make_response(jsonify(...), 404)`),
o decorator envolve o objeto novamente, descartando o status code original e
retornando 200 com o payload distorcido.

```python
# ❌ Código que parece correto mas quebra silenciosamente
@ns.marshal_with(ativo_model)
def get(self, ativo_id):
    try:
        return _serialize(svc.get(UUID(ativo_id)))  # ← ok
    except AtivoNotFoundError:
        return make_response(jsonify({"error": "not found"}), 404)  # ← 200 na prática
```

**Sintoma:** `resp.status_code == 200` quando se espera 404.  
Nenhuma exceção, nenhum warning — falha silenciosa.

---

## Causa raiz

Flask-RESTX 1.3.x não verifica `isinstance(resp, flask.Response)` antes
de aplicar o marshal. O comportamento correto (pass-through de Response objects)
estava presente em versões anteriores mas foi alterado.

Verificar o issue tracker da lib antes de qualquer upgrade de versão minor.

---

## Solução adotada

Padrão dual: `@ns.response()` para documentação OpenAPI,
retorno de **tuplas** `(dict, status_code)` para controle de runtime.

```python
# ✅ Padrão correto — sem marshal_with em rotas com múltiplos status codes
@ns.doc("get_ativo")
@ns.response(200, "Sucesso", ativo_model)   # ← apenas documentação
@ns.response(404, "Não encontrado", error_model)
def get(self, ativo_id: str):
    try:
        return _serialize(svc.get(UUID(ativo_id))), 200  # ← tuple
    except AtivoNotFoundError as exc:
        return {"error": str(exc), "code": "NOT_FOUND"}, 404  # ← tuple
```

`@ns.marshal_with` continua válido em rotas que retornam **apenas** 200
(ex: `GET /stats` que nunca falha com 4xx business error).

---

## Padrão de detecção precoce

1. **Smoke test por status code:** após implementar uma rota, fazer `curl -i`
   em cada caminho (happy path + cada erro) antes de commitar.

2. **Teste de integração que valida `.status_code` E `.get_json()`:**
   não basta testar que retornou 200; testar que 404 retorna 404.

3. **Regra de code review:** se um método tem `@marshal_with` E `try/except`
   com returns de erro — flag imediato para revisar.

---

## Implementação de referência

- `itgov/api/v1/ativos.py` — todos os Resources usam `@ns.response()` + tuples
- `tests/services/test_ativos_api_namespace.py` — cada classe de teste verifica `.status_code`

---

## Referências externas

- Flask-RESTX GitHub: buscar issues com "marshal_with Response"
- PR #98 desta codebase — discussão original do problema
