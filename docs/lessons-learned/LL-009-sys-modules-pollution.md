# LL-009: Poluição de sys.modules em testes com mocks de módulos

**Data:** 2026-06-07
**PR:** #144
**Sprint:** 11

---

## Sintoma

14 testes falhavam com `AttributeError: module 'config' has no attribute 'ZABBIX_URL'`
e `AttributeError: module 'config' has no attribute 'ZENDESK_MAX_PAGES'`, mas **apenas
quando o suite completo era executado**. Rodando os arquivos individualmente, todos passavam.

Erros de coleta (não de execução): os testes nem chegavam a rodar.

## Causa raiz

`tests/collector/test_github_pats.py` (e 3 outros) injetavam um mock de `config`
no nível de módulo com `sys.modules.setdefault()` e **nunca o removiam**:

```python
# Padrão problemático — persiste no cache para sempre
sys.modules.setdefault("config", _mock_config)
from collector.jobs.github_pats import ...
```

O mock tinha apenas `settings` (para `from config import settings`), sem os atributos
planos `ZABBIX_URL`, `ZENDESK_MAX_PAGES` etc. do `config.py` legado.

Como `tests/collector/` é coletado antes de `tests/collectors/` e `tests/services/`
(ordem alfabética), quando os testes posteriores tentavam `import config`, recebiam o
mock do cache em vez do módulo real.

## Diagnóstico

**Indicador clássico de poluição de estado global:** testes passam isolados, quebram em conjunto.

```bash
# Passa
pytest tests/collectors/test_zabbix_collector.py

# Quebra
pytest  # (suite completo)
```

Confirmar executando com `--collect-only` e observando a ordem de coleta dos módulos.

## Solução

Salvar e restaurar **apenas a chave afetada** em `sys.modules`, sem tocar nos módulos
coletores recém-importados (necessários para `patch()` nos testes):

```python
# Padrão correto — save/restore cirúrgico
_config_before = sys.modules.get("config")
sys.modules["config"] = _mock_config

from collector.jobs.github_pats import _build_point, collect_github_pats

if _config_before is None:
    sys.modules.pop("config", None)
else:
    sys.modules["config"] = _config_before
```

> **Por que não `patch.dict`?**
> `patch.dict(sys.modules, {"config": mock})` restaura o dict inteiro ao estado anterior,
> removendo também os módulos recém-importados dentro do bloco (`collector.jobs.github_pats`).
> Isso quebra chamadas subsequentes a `patch("collector.jobs.github_pats.datetime")` nos testes,
> pois o módulo não está mais em `sys.modules`.

## Princípio

**Fixtures e setup de testes NUNCA devem modificar `sys.modules` globalmente sem cleanup.**

Se um teste precisa de um módulo falso, use um dos padrões abaixo:

| Situação | Padrão recomendado |
|---|---|
| Mock de módulo de terceiro sem import at-module-level | `@pytest.fixture` com `patch()` |
| Mock necessário antes de import at-module-level | Save/restore cirúrgico (este LL) |
| Mock de módulo inteiro em múltiplos testes | `conftest.py` com `autouse` fixture |

## Impacto

- **Antes:** 16 erros de coleta, suite incompleto
- **Depois:** 637 testes passando, 0 erros de coleta
