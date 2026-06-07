# 📚 Lições Aprendidas — IT Governance Dashboard

Registro de insights operacionais, decisões arquiteturais e aprendizados
relevantes do projeto. Cada lição segue formato padrão: problema, causa raiz,
mitigação, princípio.

## Índice

| ID | Título | Data | Categoria |
|----|--------|------|-----------|
| [LL-001](./001-flask-restx-marshal-with.md) | Flask-RESTX marshal_with | — | 🐍 Backend |
| LL-002 | _(reservado)_ | — | — |
| [LL-003](./003-hardcoded-secret-incident.md) | Hardcoded secret incident | — | 🛡️ Segurança |
| LL-004 | _(reservado)_ | — | — |
| [LL-005](./LL-005-caminho-b-github-pr-collector.md) | Caminho B: coletor real de PRs do GitHub | 2026-06-06 | 📊 Dados |
| [LL-006](./LL-006-restart-vs-build.md) | `docker compose restart` não atualiza código em imagens read-only | 2026-06-07 | 🐳 Deploy |
| [LL-007](./LL-007-deploy-topology.md) | Mapeamento da topologia completa antes de debugar produção | 2026-06-07 | 🌐 Infra |
| [LL-008](./LL-008-edge-device-masquerade.md) | Edge devices podem responder como se fossem sua aplicação | 2026-06-07 | 🛡️ Segurança |

## Categorias

- 🐳 **Deploy** — pipeline, build, container
- 🌐 **Infra** — rede, topologia, DNS
- 🛡️ **Segurança** — hardening, exposição, vulns
- 🐍 **Backend** — Flask, Python, async
- 📊 **Dados** — InfluxDB, queries, modelagem
- 🎨 **Frontend** — templates, UX, assets

## Convenções

- IDs sequenciais e imutáveis (LL-XXX)
- Arquivo: `LL-XXX-slug-curto.md`
- Sempre incluir: problema, causa raiz, mitigação, princípio
- Cross-referenciar lições relacionadas
