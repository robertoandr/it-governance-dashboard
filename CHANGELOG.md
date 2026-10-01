# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Início da V2.0 "Governança de TI 360" (revisão página por página de 30/09/2026).

### Changed
- Nome do sistema passa a ser **Governança de TI 360** (títulos, menu, rodapé, login)
- Versão com fonte única no `pyproject.toml` (`2.0.0.dev0`); `APP__VERSION`/`APP__NAME` não precisam mais estar no `.env`
- Home: "Calculado em" (sempre o horário do acesso) trocado por **Última atualização em**, com a coleta real mais recente do InfluxDB em horário de Brasília, e botão **Atualizar**
- Página `/gov/backup` renomeada para **Cibersegurança** em `/gov/ciberseguranca` (o endereço antigo redireciona com 301)

### Fixed
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
