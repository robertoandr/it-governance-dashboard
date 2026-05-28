# IT Governance Dashboard

**Usuário de execução:** zabbix
**Servidor:** <DEV_SERVER_IP>
**Caminho:** <DEPLOY_PATH>

## Stack
- Python 3.11+, Flask[async], Flask-RESTX, Pydantic v2
- **TimescaleDB** (PostgreSQL 16 + extensão TimescaleDB 2.x) — armazenamento único
- SQLAlchemy 2.x (async, asyncpg), Alembic, structlog
- Docker, Docker Compose, Kubernetes
- Auth: Microsoft Entra ID (OAuth2)

## Arquitetura
- **Flask** — camada de negócio: Fornecedores, Contratos, Hub de Governança, Service Desk
- **Grafana** — observabilidade embedada: métricas M365, infraestrutura (kiosk iframe)
- **Nginx** — gateway único: TLS, JWT validation, roteamento `/app/*` → Flask, `/grafana/*` → Grafana

## Módulo Hero
**Fornecedores & Contratos** (Sprints 9-10) — núcleo do sistema.
- Schema: `db/migrations/001_fornecedores_contratos.sql`
- Hypertable: `contratos_eventos` (TimescaleDB) para alertas de vencimento e SLA breach
- ADRs relevantes: ADR-0004 (storage), ADR-0005 (Flask+Grafana), ADR-0003 (LGPD)

## Integrações
- GitHub API, Zabbix JSON-RPC, Zendesk API, MS Graph

## Estrutura
- app/api/ — Endpoints Flask-RESTX
- app/models/ — Pydantic models
- app/services/ — Lógica de negócio
- app/utils/ — Helpers
- db/migrations/ — SQL migrations (TimescaleDB)
- docs/adr/ — Architectural Decision Records
- docker/, k8s/, tests/

## Regras OBRIGATÓRIAS
1. Type hints em TODAS as funções
2. Docstrings Google style
3. Pydantic para validação
4. structlog (NUNCA print)
5. async/await para I/O
6. Secrets via env vars
7. Try/except tipado
8. **Sempre validar saída com git status após cada commit**

## ⚠️ Cuidados específicos deste servidor
- NÃO mexer em <ZABBIX_CONFIG_PATH> sem autorização
- NÃO reiniciar serviço zabbix-server sem aviso
- Logs do Zabbix em <ZABBIX_LOG_PATH> — apenas leitura

## 🔐 Variáveis de ambiente locais
Valores reais de servidores e paths estão em `.env.local` (não versionado).
Veja `.env.local.example` para o formato esperado.
