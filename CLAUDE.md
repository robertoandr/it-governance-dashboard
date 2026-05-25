# IT Governance Dashboard

**Usuário de execução:** zabbix
**Servidor:** <DEV_SERVER_IP>
**Caminho:** <DEPLOY_PATH>

## Stack
- Python 3.11+, Flask, Flask-RESTX, Pydantic v2
- InfluxDB v2 (Flux), structlog
- Docker, Docker Compose, Kubernetes
- Auth: Microsoft Entra ID (OAuth2)

## Integrações
- GitHub API, Zabbix JSON-RPC, Zendesk API, MS Graph

## Estrutura
- app/api/ — Endpoints Flask-RESTX
- app/models/ — Pydantic models
- app/services/ — Lógica de negócio
- app/utils/ — Helpers
- docker/, k8s/, tests/

## Regras OBRIGATÓRIAS
1. Type hints em TODAS as funções
2. Docstrings Google style
3. Pydantic para validação
4. structlog (NUNCA print)
5. async/await para I/O
6. Secrets via env vars
7. Try/except tipado

## ⚠️ Cuidados específicos deste servidor
- NÃO mexer em <ZABBIX_CONFIG_PATH> sem autorização
- NÃO reiniciar serviço zabbix-server sem aviso
- Logs do Zabbix em <ZABBIX_LOG_PATH> — apenas leitura

## 🔐 Variáveis de ambiente locais
Valores reais de servidores e paths estão em `.env.local` (não versionado).
Veja `.env.local.example` para o formato esperado.
