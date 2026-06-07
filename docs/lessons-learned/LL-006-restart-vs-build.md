# LL-006 — `docker compose restart` não atualiza código em imagens read-only

**Data:** 2026-06-07
**Contexto:** Bug do timestamp ("Calculado em" → "Atualizado em") aplicado no repo, mas restart do container não refletiu em produção.

## 🔴 Problema

Após corrigir um template Jinja e executar `docker compose restart app`,
o template antigo continuou sendo servido pelo container.

## 🧠 Causa Raiz

O `docker-compose.yml` do projeto usa `read_only: true` e **não tem bind-mount**
do código-fonte. O código está embutido na imagem durante o build.

| Comando | Efeito | Quando funciona |
|---------|--------|-----------------|
| `restart` | Reinicia processo com a MESMA imagem | Apenas se houver bind-mount de código |
| `build`   | Reconstrói imagem com código novo | Sempre |
| `up -d`   | Recria container usando imagem disponível | Após build |

## ✅ Workflow Correto

```bash
git pull
docker compose build app
docker compose up -d app
```

## 🛡️ Mitigações Implementadas

1. **Endpoint `/api/health` com `GIT_SHA`** para detectar drift entre código e container
2. **Imagem imutável** (`read_only: true`) é decisão arquitetural correta para produção

## 🎯 Princípio

> Container imutável (`read_only: true`) é **decisão arquitetural correta** para produção.
> Trade-off: perde hot reload. Compensação: tooling explícito de deploy.

## Referências

- [LL-007](./LL-007-deploy-topology.md) — topologia de deploy
- `docker-compose.yml` — configuração do serviço `app`
