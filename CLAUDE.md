# IT Governance Dashboard

**Versao atual:** V1.0 (Sprint 10H — concluida)
**Proximo marco:** V1.1 Go-Live — 30/06/2026
**Status:** Producao estavel + Evolucao V1.1 em andamento

---

## 1. Ambiente

| Parametro            | Valor                                        |
|----------------------|----------------------------------------------|
| Usuario de execucao  | `zabbix`                                     |
| Servidor DEV         | `itgov-dev` (172.29.2.11)                    |
| Caminho do projeto   | `/home/zabbix/projects/it-governance-dashboard` |
| Repositorio          | `robertoandr/it-governance-dashboard`        |
| Branch padrao        | `main` (protegida — requer PR + aprovacao)   |

Valores sensiveis (DSNs, tokens, caminhos de producao) estao em `.env.local` (nao versionado).
Consulte `.env.local.example` para o formato esperado.

---

## 2. Stack Tecnica

### Backend

| Componente     | Tecnologia                                              |
|----------------|---------------------------------------------------------|
| Linguagem      | Python 3.11+                                            |
| Framework      | Flask + Flask-RESTX (namespaces, Swagger auto-gerado)   |
| Validacao I/O  | Pydantic v2 (modelos tipados obrigatorios)              |
| ORM / Migrations | SQLModel + Alembic                                    |
| Observabilidade | structlog (JSON estruturado; `print` proibido)         |

### Persistencia Hibrida

| Papel                  | Tecnologia DEV   | Tecnologia PROD       |
|------------------------|------------------|-----------------------|
| Dados relacionais CRUD | SQLite (WAL)     | PostgreSQL 16         |
| Time-series / metricas | InfluxDB 2.7     | InfluxDB 2.7 (Cloud)  |

> **Nota ADR:** A ADR-0004 (TimescaleDB como storage unico) foi **SUPERSEDED** durante o
> planejamento da V1.1. A estrategia vigente e a ADR-0001: dual storage
> SQLite/PostgreSQL para CRUD + InfluxDB para series temporais.
> O InfluxDB ja estava em producao para metricas Zabbix, tornando o
> dual storage mais eficiente que uma migracao para TimescaleDB unificado.

### Observabilidade e Infra

| Componente       | Tecnologia                              |
|------------------|-----------------------------------------|
| Dashboards       | Grafana 13 (embedado via kiosk iframe)  |
| Monitoramento    | Zabbix 7.0                              |
| Containers       | Docker + Docker Compose                 |
| Orquestracao     | Kubernetes 1.28+                        |
| Gateway          | Nginx (TLS, JWT validation, roteamento) |
| Autenticacao     | Microsoft Entra ID (OAuth2/OIDC)        |

---

## 3. Integracoes

| Sistema          | Protocolo          | Proposito                                  |
|------------------|--------------------|--------------------------------------------|
| GitHub           | REST API v3        | Metricas de repositorios e PRs             |
| Zabbix           | JSON-RPC 2.0       | Alertas e disponibilidade de infraestrutura|
| Zendesk          | REST API v2        | SLA e CSAT de service desk                 |
| Microsoft Graph  | REST + OAuth2      | Dados M365: usuarios, MFA, Secure Score    |

---

## 4. Modulo Hero V1.0 — Fornecedores e Contratos (Sprints 9-10)

Nucleo do sistema entregue na V1.0:

- Cadastro completo de fornecedores com dados contratuais
- Controle de vencimento com alertas antecipados (30d/7d)
- SLA tracking com calculo automatico de breach
- CSAT por fornecedor via integracao Zendesk
- Schema: `db/migrations/001_fornecedores_contratos.sql`
- ADRs relevantes: ADR-0001 (storage), ADR-0005 (Flask+Grafana), ADR-0003 (LGPD)

---

## 5. Baseline V1.0

| Indicador           | Valor                          |
|---------------------|-------------------------------|
| Testes              | 215                            |
| Coverage            | 88.21%                         |
| PRs entregues       | #75, #79, #80, #81             |
| Hotfix              | `4933654` (flaky collector test)|
| Issues P3 residuais | #73, #74, #76, #78 (para V1.1) |

---

## 6. Roadmap V1.1 (02/06 - 30/06/2026)

| Sprint | Periodo      | Foco                                    | KPI chave                       |
|--------|--------------|-----------------------------------------|---------------------------------|
| 11     | 02-08/06     | Debito Tecnico + Inventario de Ativos   | ADRs 001/002, CRUD /ativos      |
| 12     | 09-22/06     | Governanca M365 (6 Pilares)             | Secure Score integrado, cache   |
| 13     | 23-27/06     | Hub SharePoint + 8 Triggers SMTP        | 8 alertas configurados          |
| 14     | 28-30/06     | Polimento + Go-Live                     | Tag v1.1.0, manifests k8s       |

### Metas globais V1.1

| Meta               | Valor alvo    |
|--------------------|---------------|
| Coverage           | >= 90%        |
| Testes             | >= 350        |
| Secure Score M365  | >= 60%        |
| MFA administradores| 100%          |

---

## 7. Regras de Codigo (obrigatorias)

1. **Type hints** em TODAS as funcoes — parametros e retorno
2. **Docstrings Google style** em classes e metodos publicos
3. **Pydantic v2** para validacao de I/O em endpoints e servicos
4. **structlog** para logging — `print()` e `logging.getLogger()` sao proibidos
5. **async/await** para todas as operacoes de I/O (BD, HTTP, filesystem)
6. **Secrets via env vars** — nenhuma credencial em codigo ou arquivos versionados
7. **Try/except tipado** — capturar excecoes especificas, nunca `except Exception` nuo
8. **git status** apos cada commit — validar que nao ha arquivos esquecidos
9. **Testes obrigatorios** — todo novo modulo/endpoint exige testes; PRs sem testes sao bloqueados
10. **Conventional Commits** — `feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `test:`

---

## 8. Cuidados Especificos do Servidor itgov-dev

- **NAO** modificar configuracoes do Zabbix Agent sem autorizacao explicita do time de infra
- **NAO** reiniciar o servico `zabbix-server` sem aviso previo — afeta monitoramento de producao
- Logs do Zabbix em `/var/log/zabbix/` — apenas leitura
- Hotfixes direto em `main` exigem aprovacao conforme ADR-002 (processo de verificacao)
- Servicos Docker em producao: parar/reiniciar apenas apos confirmar janela de manutencao

---

## 9. ADRs (Architectural Decision Records)

Localizacao: `docs/adr/`

| ADR    | Titulo                              | Status             | Data       |
|--------|-------------------------------------|--------------------|------------|
| 0001   | Dual Storage — SQLite + InfluxDB    | **Ativa**          | 2026-01-15 |
| 0002   | RBAC — 3 niveis (admin/manager/ro)  | **Ativa**          | 2026-01-20 |
| 0003   | LGPD Compliance — PII e Erasure     | **Ativa**          | 2026-01-25 |
| 0004   | TimescaleDB Single Storage          | **SUPERSEDED**     | 2026-02-05 |
| 0005   | Flask + Grafana Coexistence         | **Ativa**          | 2026-02-10 |
| 0010   | Frontend Strategy                   | **Ativa**          | 2026-05-31 |
| ADR-001| Coverage Policy (>= 85% piso)       | **Ativa**          | 2026-06-02 |
| ADR-002| Verification Rigor (PR checklist)   | **Ativa**          | 2026-06-02 |

> ADR-0004 foi superseded durante o planejamento da V1.1. Ver `docs/adr/0004-timescaledb-single-storage.md`.

---

## 10. Skills do Claude Code Recomendadas

| Skill                | Quando usar                                               |
|----------------------|-----------------------------------------------------------|
| `/zabbix-api`        | Automacao de hosts, triggers e items no Zabbix            |
| `/influxdb-cloud`    | Consultas e ingestao de metricas no InfluxDB              |
| `/grafana-dashboards`| Criacao e manutencao de dashboards de observabilidade     |
| `/postgresql-optimization` | Queries avancadas, JSONB, indices e performance    |
| `/docker-expert`     | Otimizacao de imagens, compose e seguranca de containers  |
| `/kubernetes-specialist` | Manifests, RBAC, NetworkPolicy e troubleshooting    |
| `/code-review`       | Review de PRs com niveis de profundidade (low/ultra)      |
| `/security-review`   | Auditoria de seguranca antes de merges em main            |
| `/git-workflow`      | Padronizacao de branches, commits e estrategia de merge   |
| `/python-executor`   | Scripts de analise de dados e automacao                   |

---

## 11. Comandos Frequentes

### Desenvolvimento

```bash
# Ambiente virtual
source venv/bin/activate

# Servidor de desenvolvimento
flask run --debug --port 5000

# Testes com coverage
pytest --cov=itgov --cov-report=term-missing -v

# Lint + format
ruff check . && ruff format --check .

# Migrations
alembic upgrade head
alembic revision --autogenerate -m "descricao"
```

### Git

```bash
# Criar branch de feature
git checkout -b feat/nome-da-feature

# Commit semantico
git commit -m "feat(modulo): descricao curta

Body explicando o que e por que (nao o como).

Refs #ISSUE"

# Verificar estado apos commit
git status && git log --oneline -5
```

### Deploy

```bash
# Build da imagem
docker build -t itgov:latest .

# Subir servicos
docker compose up -d

# Logs em tempo real
docker compose logs -f app

# Aplicar migrations em producao
docker compose exec app alembic upgrade head
```

---

*Ultima atualizacao: 02/06/2026 - Inicio Sprint 11 / V1.1*
