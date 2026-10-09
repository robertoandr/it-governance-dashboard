# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [2.1.0] - 2026-10-09

Versão final da V2 "Governança de TI 360": revisão página por página de 30/09 e
conferência final de 08–09/10/2026.

### Added
- **Usuários:** super admin que aprova alterações de cadastro e configuração (#251), aviso para quem pediu quando o pedido vai para aprovação e quando é decidido (#278) e permissões por usuário, página a página (#279)
- **Rede:** descoberta pelo Zabbix com novos na rede, nomenclatura e latência por unidade (#253); FortiGates pela API, com SD-WAN, WANs e DHCP/ARP (#254); topologia lógica por unidade e geral (#255)
- **CFTV:** drill-down por sede, renomear e unir gravador duplicado (#249), histórico de quedas (#250), SNMP v2c/v3 dos gravadores no Zabbix (#266) e cadastro de gravador novo
- **Alertas:** trigger vira chamado no Zendesk e aviso no grupo de WhatsApp (#265), em português simples, das 08:00 às 19:00, com resumo da manhã (#276)
- **Painel TV:** modelos v1–v6 com dados ao vivo; v6 "Sala de Controle Bento" liberada por usuário (#269, #273, #275, #277)
- **M365:** painel com dados reais (#241), licenças com custo e alerta de esgotamento (#242, #274), uso dos apps com histórico (#257), compliance em português (#243)
- **Gestão:** plano de melhoria nos pilares (#236), PMO por responsável com trigger de atraso (#238), aba ClickUp em Tarefas (#237), relatórios temáticos e personalizados (#264), Zendesk mês a mês (#248)
- **Unidades:** endereço, CEP, logo e CNPJ (#232, #247); situação de todas as fontes na Visão Geral (#235)
- **Backup:** InfluxDB, segredos, configurações, retenção remota, teste de restauração e aviso ao Zabbix (#268, #271)

### Changed
- Visual institucional Gadens, com marca, paleta e modo claro/escuro (#272)
- Mapa geral da topologia ocupa a largura toda da página
- Nome do sistema passa a ser **Governança de TI 360** (títulos, menu, rodapé, login)
- Versão com fonte única no `pyproject.toml`; `APP__VERSION`/`APP__NAME` não precisam mais estar no `.env`
- Home: "Calculado em" (sempre o horário do acesso) trocado por **Última atualização em**, com a coleta real mais recente do InfluxDB em horário de Brasília, e botão **Atualizar**
- Página `/gov/backup` renomeada para **Cibersegurança** em `/gov/ciberseguranca` (o endereço antigo redireciona com 301)

### Fixed
- Lentidão: falha da origem que devolvia `None` não ficava em cache (M365 e Aplicativos esperavam o Graph a cada página); Compliance passa a atualizar em segundo plano; a página Rede converte as faixas de IP uma vez só
- Alternância de tema claro/escuro não funcionava: a config do Tailwind era definida antes do script e sobrescrita, ficando no modo `media` (tema do SO)
- Score de governança misturava 7 componentes sem coletor (valores fixos: cobertura de KPIs, orçamento, frequência de deploy, vulnerabilidades críticas, patches, MTTR, sucesso de mudanças) na média do pilar; agora ficam fora do cálculo e aparecem como "sem coleta" no detalhe do pilar
- Páginas sem variantes `dark:` (M365, Licenças, Compliance, Infra, Acronis, Zabbix, SLA etc.) ficavam ilegíveis no tema escuro

## [1.1.0] - 2026-06-06

### Added
- Kubernetes liveness and readiness probes (`GET /api/health`, `GET /api/health/ready`) with parallel async dependency checks and per-check timeouts (#134)
- `k8s/deployment.yaml` with `startupProbe` + `livenessProbe` + `readinessProbe` configured for Flask/Gunicorn boot characteristics
- Asset inventory CRUD endpoints (`/api/v1/ativos`) with SQLite-backed persistence
- 5-pillar COBIT governance dashboard at `/` with score, trend, and component breakdown
- Pillar detail pages at `/pillars/<id>` with 30-day simulated trend chart

### Fixed
- Tooltip ghost on pillar cards — new `app/templates/` architecture does not use `data-tip` attributes; legacy `templates/dashboard.html` isolated to old dashboard routes only

### Changed
- `docker-compose.yml` healthcheck path updated from deleted `/health` stub to `/api/health`
- `INFLUX_PORT` must be set to `18086` on dev server (`172.29.2.11`) to avoid conflict with host-native InfluxDB used by Zabbix

## [1.0.0] - 2026-05-31

### Added
- Supplier and contract management module with SLA tracking and breach alerting
- Zabbix, Zendesk, and Microsoft Graph integrations
- Flask-RESTX API with Swagger documentation at `/api/`
- Dual storage: SQLite/PostgreSQL for CRUD + InfluxDB for time-series metrics
- Grafana dashboard embedding via kiosk iframe
