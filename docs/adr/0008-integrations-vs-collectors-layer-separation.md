# ADR-0006: Separação de camadas — app.integrations vs app.collectors

## Status: Aceito

## Contexto

Durante a Sprint 7, ao implementar o coletor GitHub, surgiu a necessidade de
organizar o código que lida com APIs externas em dois grupos distintos:

1. **Código reutilizável** que pode ser chamado por múltiplos consumidores
   (dashboard Flask, coletores, CLI de diagnóstico).
2. **Pipeline de governança** com responsabilidade única: fetch → transform →
   write em `governance_raw`.

## Decisão

Duas camadas com responsabilidades distintas:

```
app/
├── integrations/          ← clientes HTTP reutilizáveis, sem opinião de storage
│   └── github/
│       ├── client.py      # GitHubClient: paginação, auth, rate-limit, retry
│       ├── models.py      # PullRequest, WorkflowRun (imutáveis, frozen)
│       ├── auth.py        # PATAuth, (futuro) GitHubAppAuth
│       └── exceptions.py  # exceções tipadas da API
│
└── collectors/            ← pipeline governance_raw com opinião de schema
    └── github/
        ├── client.py      # GitHubCollectorClient (subclasse + since-filter)
        ├── models.py      # GitHubPR, GitHubWorkflowRun (gov schema)
        ├── transformer.py # → GovernancePoint
        └── collector.py   # BaseCollector pipeline
```

### Regra de dependência

```
collectors → integrations  (permitido)
integrations → collectors  (PROIBIDO)
```

`app.collectors.github.exceptions` re-exporta de `app.integrations.github.exceptions`
para que os collectors nunca importem de `integrations` diretamente — se a
estrutura interna mudar, apenas o re-export precisa ser atualizado.

## Trade-offs

| | |
|---|---|
| ✅ | `integrations` é reutilizável por Flask routes, CLI e outros coletores |
| ✅ | `collectors` tem schema de governança (prefixo `gov_`, bucket guards) |
| ✅ | Testes de `integrations` independem do schema InfluxDB |
| ✅ | `GitHubCollectorClient` herda infraestrutura sem duplicar código |
| ❌ | Dois conjuntos de modelos para GitHub (PullRequest vs GitHubPR) |
| ❌ | Mais diretórios para manter |

## Alternativas consideradas

**Coletor direto em `integrations/`**: misturaria responsabilidades —
`integrations` teria dependência de `storage/influxdb`, violando a direção
de dependência.

**Único diretório `app/github/`**: sem distinção de camada; mais fácil de
iniciar mas dificulta reuso quando outros consumidores precisarem do client.

## Contexto adicional

`app/integrations/github/` foi criado na Sprint 7 (não estava no repositório
antes) como fundação para múltiplos consumidores planejados: dashboard Flask
(PR counts em tempo real), coletor de governança e futuro CLI de diagnóstico.
