# Sprint 14 — IT Governance Dashboard
**Data:** 2026-06-30
**Projeto:** `/home/zabbix/projects/it-governance-dashboard` (porta 5000, gunicorn)
**Stack:** Flask factory app (`app/__init__.py:create_app()`), Alpine.js, Tailwind, Chart.js
**Blueprints ativos:** `dashboards_bp` prefixo `/gov`, `auth_bp` prefixo `/gov`, `users_bp` prefixo `/gov`

---

## Hotfixes e Segurança (2026-07-07/08)

Trabalho operacional fora do backlog de features abaixo, executado entre 2026-07-07 e 2026-07-08.

**Rotação de credenciais**
- `ZABBIX_PASSWORD`, `GRAFANA_ADMIN_PASSWORD` e `INFLUX_TOKEN` rotacionados em produção (`.env`).
  Backups pré-rotação preservados: `.env.bak-pre-zabbix-admin-rotation-20260707-154017` e `.env.bak.20260707200558`.
- Token antigo do InfluxDB revogado (não apenas substituído no `.env` — a credencial anterior deixou de ser válida no InfluxDB).
- `.env` de produção higienizado: chaves criptográficas antigas substituídas por novos valores gerados.

**Correção do backup automatizado (`/usr/local/bin/backup-dashboard.sh`)**
- `SOURCE_DIR` corrigido para apontar ao diretório de produção real (`/home/zabbix/projects/it-governance-dashboard`), que havia divergido do caminho antigo.
- Incluído dump do InfluxDB via `docker cp` dos diretórios de dados do container `itgov-influxdb` (evita depender de token com escopo operator).
- Incluído dump do Postgres via `pg_dump` dentro do container `itgov-postgres`, quando presente.
- Cron em produção confirmado: `0 2 * * * /usr/local/bin/backup-dashboard.sh >> /var/log/backup-dashboard.log 2>&1`; permissões do log corrigidas (proprietário `zabbix:zabbix`).

**Erradicação do ambiente órfão**
- Diretório legado `/opt/it-gov-dashboard` removido.
- Contentor isolado `itgov-postgres` (fora do stack docker-compose ativo) eliminado, junto de redes e volumes órfãos associados.
- Serviço systemd legado `dashboard-ti.service` desativado (substituído por V1.1 via Docker, ver `dashboard-ti/DEPRECATED.txt`).
- Cron jobs obsoletos removidos; crontab de produção contém apenas os jobs ativos (coletor M365, status M365, backup diário, lembrete de rotação de PAT).

**Modernização do LDAP / resolução de débito técnico**
- Serviço LDAP refatorado de `collectors/ldap_collector.py` (removido) para `app/services/ldap_service.py`, documentado em `docs/MIGRATION.md`.
- Autenticação alterada para NTLM explícito (`ldap3.NTLM`) — bind `SIMPLE` era rejeitado pelo AD para usuários no formato `DOMAIN\user`.
- Corrigido erro de hash MD4 no `patch_collector`: OpenSSL 3.x desabilita MD4 por padrão, usado pelo `ldap3` no NTOWFv2 do bind NTLM. Resolvido com `pycryptodome==3.21.0` em `collector/requirements.txt` (implementação própria de MD4).

---

## Auditoria de Dados Reais (Tarefa 2)

### Score de Saude Geral — como e calculado

O score exibido na Visao Geral (`/gov/`) **NAO e o mesmo** do Score de Saude Geral do `app.py` legado.
O `/gov/` usa o blueprint `dashboards_bp` → `app/views/dashboards.py:_get_governance()` → `MetricsAggregator` → **`MockMetricsProvider`** (seed=42, jitter aleatorio).

Formula do mock (`app/services/mock_data.py`):
```
Strategic Alignment (w=0.15): jitter sobre base [74..82..68..79]
Value Delivery     (w=0.20): jitter sobre base [91..85..72..76]
Risk Management    (w=0.25): jitter sobre base [58..74..88..95..61]
Resource Management(w=0.15): jitter sobre base [69..74..82..78]
Performance Measure(w=0.25): jitter sobre base [99..73..87..83]
```
Global = soma(pillar.score * pillar.weight). O "68.9" e resultado deterministico do seed 42.
**Nao ha dados reais conectados aos Pilares COBIT ainda.**

O score legado (exibido em `dashboard.html` na rota `/`) e calculado em `app.py:_compute_health_score()`:
- Secure Score (peso 30): `secure_score.pct` via Microsoft Graph
- Hosts UP (peso 25): `hosts.up_pct` via Zabbix
- Triggers nao-criticas (peso 20): contagem de triggers severity>=4 via Zabbix
- MFA habilitada (peso 15): `mfa.pct` via InfluxDB
- Service Health (peso 10): contagem "operational" via InfluxDB

### Pilares COBIT — estado atual

**Todos os 5 pilares usam MockMetricsProvider.** Nao ha fonte de dados real wired.
O `app/services/influxdb_provider.py` existe e tem metodos parcialmente implementados para conectar ao InfluxDB, mas o `MetricsAggregator` nao usa — ainda instancia `MockMetricsProvider` como default.

### Compliance — fonte de dados

`GET /gov/governance/compliance` → `governance_compliance.py:_buscar_do_graph()` → `SecureScoreGraphClient`
Endpoints Graph chamados:
- `GET /security/secureScores?$top=1` — score atual + historico
- `GET /security/secureScoreControlProfiles` — controles e recomendacoes

**Dado real disponivel quando `AZURE_CLIENT_ID` configurado.** Cache TTL=300s em memoria.

### Dispositivos — limiar de inatividade

`itgov/services/device_service.py:_STALE_DAYS = 90`
O modelo retorna campo `stale_90d`. O usuario quer **45 dias**. Requer mudanca em 2 arquivos:
1. `device_service.py` linha 9: `_STALE_DAYS = 45`
2. `governance_devices.html`: renomear label e campo `stale_90d` → `stale_45d`
3. `itgov/models/governance_devices.py`: renomear campo no modelo Pydantic

### Licencas — anomalia "ACIMA DO LIMITE"

Dados reais no InfluxDB (`m365_licenses`):
```
EXCHANGEENTERPRISE: consumed=16, total=10  -> over_provisioned=True -> "ACIMA DO LIMITE"
O365_BUSINESS:      consumed=80, total=80  -> available=0 (no limit)
POWER_BI_PRO:       consumed=8,  total=6   -> over_provisioned=True
```
A anomalia "ACIMA DO LIMITE" e **real e correta** — o tenant tem mais usuarios atribuidos do que licencas compradas.
O bug "Total=0" relatado anteriormente foi corrigido. Os custos aparecem R$0 porque o campo `cost_per_unit_brl` ainda nao foi preenchido manualmente no UI.
Friendly names ja estao mapeados em `app/data/license_costs.json`:
- `O365_BUSINESS_ESSENTIALS` → "Microsoft 365 Business Basic" ✅
- `O365_BUSINESS_PREMIUM` → "Microsoft 365 Business Standard" ✅
- `O365_BUSINESS` → "Microsoft 365 Apps for Business" ✅

### Vulnerabilidades — por que esta desabilitada

Sidebar linha 79: `soon=true` — sem rota, sem blueprint, sem collector.
Nenhum scanner de vulnerabilidades esta integrado (Tenable, Qualys, etc.).
Campo reservado para integracao futura.

---

## Backlog Sprint 14 — 20 Itens

### GOV — Governanca (nucleo)

---

**GOV-00 — Wirear InfluxDBMetricsProvider no MetricsAggregator**
Status: ✅ CONCLUÍDO — já estava implementado
Findings da verificação em produção (01/07/2026):
- InfluxDBMetricsProvider está ativo (itgov-app rodando desde 2026-06-29)
- Score 68.9 é dado real — fontes: `gov_zabbix_summary`, `gov_m365_secure_score`, `gov_entra_summary`, `gov_github_pr`
- 5/5 pilares retornam `data_source: "live"`
- Log startup confirma `kind: influxdb`
- A auditoria anterior analisou código desatualizado (versão pre-Docker)

Nenhuma alteracao necessaria. Avancar direto para Bloco 1 — Quick Wins.

---

**GOV-01 — Icone mais bonito no sidebar e favicon**
Rota afetada: todas (layout global)
Arquivos: `app/templates/partials/sidebar.html` (bloco Brand, linhas 1-10), `app/static/favicon.ico`
Fonte de dados: nenhuma (puro HTML/SVG)
Dependencias: nenhuma
Status atual: ✅ sidebar funciona, icone e um grafico de barras SVG generico
Esforco estimado: **0,5d** — trocar SVG por icone de escudo/governanca ou logo Gadens

---

**GOV-02 — Visao Geral: conectar dados reais**
Rota: `GET /gov/` (`app/views/dashboards.py:overview`)
Arquivos a modificar:
- `app/services/metrics_aggregator.py` — trocar `MockMetricsProvider` por `InfluxDBMetricsProvider`
- `app/services/influxdb_provider.py` — implementar `get_strategic_metrics()`, `get_value_metrics()`, etc. com queries Flux reais
Fonte de dados real:
- Strategic: ClickUp (projetos alinhados) + configuracao manual
- Value: Zendesk (SLA/resolucao) + InfluxDB `gov_zabbix_disponibilidade`
- Risk: Graph API (MFA `entra_summary`, patch `intune_compliance`) + InfluxDB `gov_backup_*`
- Resource: Graph API `m365_licenses` + InfluxDB `gov_zabbix_summary`
- Performance: InfluxDB `gov_zabbix_disponibilidade` (uptime) + Zendesk (MTTR)
Dependencias: GOV-03
Status atual: ❌ MOCK
Esforco estimado: **4d**

---

**GOV-03 — Pilares COBIT: conectar a dados coletados**
Rota: `GET /gov/pillars`, `GET /gov/pillars/<id>` (`app/views/dashboards.py:pillars`, `pillar_detail`)
Arquivos a modificar:
- `app/services/influxdb_provider.py` — implementar todos os `get_*_metrics()` com Flux real
- `app/services/metrics_aggregator.py` — remover dependencia de `MockMetricsProvider`
Fonte de dados: InfluxDB `governance_raw` bucket (measurements: `gov_m365_secure_score`, `gov_entra_summary`, `gov_zabbix_summary`, `gov_zabbix_disponibilidade`, `m365_licenses`)
Dependencias: GOV-02
Status atual: ❌ MOCK (seed=42)
Esforco estimado: **3d** (incluido em GOV-02)

---

### M365 — Microsoft 365

---

**M365-01 — Governanca M365: remover bloco de reajuste expirado**
Rota: `GET /gov/m365` (`app/views/dashboards.py:m365_overview`)
Arquivos a modificar:
- `app/views/dashboards.py` linhas 556-558: remover variaveis `reajuste_iso`, `dias_reajuste`, `reajuste_date` do contexto
- `app/templates/dashboards/m365_overview.html`: remover bloco de countdown e tabela comparativa de precos
Fonte de dados: nenhuma (era dado hardcoded para 2026-07-01, expirou)
Dependencias: nenhuma
Status atual: 🚧 dado expirado (01/07/2026 = amanha), campo ficara negativo
Esforco estimado: **0,5d**

---

**M365-02 — Licencas: interface de custo em BRL**
Rota: `GET /gov/licenses`, `POST /gov/licenses/update`
Arquivos: `app/templates/dashboards/m365_licenses.html`, `itgov/api/v1/m365_licenses.py`
Fonte de dados: `app/data/license_costs.json` (edicao manual via UI)
Estado atual: endpoint `POST /gov/licenses/update` existe e funciona. UI tem modal de edicao mas nao e intuitivo para custo por usuario.
Tarefa: adicionar campo de custo por unidade (R$) e renovacao com datepicker no modal existente, tornar visivel sem scroll.
Dependencias: nenhuma
Status atual: 🚧 funcional mas UX ruim
Esforco estimado: **1d**

---

**M365-03 — Licencas: verificar contagem real vs M365**
Rota: `GET /gov/licenses`
Arquivos: `itgov/api/v1/m365_licenses.py`, `app/data/license_costs.json`
Fonte de dados: InfluxDB `m365_licenses` (escrito pelo `m365_collector.py`)
Situacao verificada:
- EXCHANGEENTERPRISE: 16 consumidas / 10 licencas → over-provisioned (comprar mais ou reatribuir)
- POWER_BI_PRO: 8 consumidas / 6 licencas → over-provisioned
- O365_BUSINESS: 80/80 → limite exato
Tarefa: adicionar alerta visual destacando over-provisioned e recomendacao de acao. Nao e bug de codigo.
Dependencias: nenhuma
Status atual: ✅ dados corretos, falta destaque visual
Esforco estimado: **0,5d**

---

**M365-04 — Dispositivos: limiar de inatividade 90d → 45d**
Rota: `GET /gov/governance/devices`
Arquivos a modificar:
1. `itgov/services/device_service.py` linha 9: `_STALE_DAYS = 45`
2. `itgov/models/governance_devices.py`: renomear campo `stale_90d` → `stale_45d`
3. `itgov/api/v1/governance_devices.py`: atualizar model field description
4. `app/templates/dashboards/governance_devices.html`: atualizar label e hint de acao
Fonte de dados: Microsoft Graph `GET /devices` → campo `approximateLastSignInDateTime`
Dependencias: nenhuma
Status atual: 🚧 limiar errado (90d hardcoded)
Esforco estimado: **0,5d**

---

**M365-05 — Aplicativos: mostrar uso de apps com licenca e descricao**
Rota: `GET /gov/governance/apps`
Arquivos a modificar:
- `itgov/api/v1/governance_apps.py`: adicionar endpoint de lista completa de apps com descricao
- `app/templates/dashboards/governance_apps.html`: adicionar secao "Apps com Licenca" + descricao de cada servico
Fonte de dados: Microsoft Graph `GET /subscribedSkus` + `GET /servicePlans` (lista servicos habilitados por licenca)
Atual: mostra App Registrations (SPNs/secrets expirando) — e diferente de "apps M365 com licenca"
Tarefa: adicionar nova secao com apps do tenant (Teams, Exchange, SharePoint, etc.) e o que cada um faz.
Dependencias: M365-02 (custos)
Status atual: 🚧 funcional mas scope diferente do solicitado
Esforco estimado: **2d**

---

**M365-06 — Compliance: triggers para melhorar Secure Score**
Rota: `GET /gov/governance/compliance`
Arquivos a modificar:
- `app/templates/dashboards/governance_compliance.html`: adicionar lista de acoes priorizadas com impacto estimado no score
- `itgov/services/compliance_service.py`: incluir campo `acoes_prioritarias` no resumo
Fonte de dados: `GET /security/secureScoreControlProfiles` — ja coletado, campo `rank` indica prioridade
Dependencias: nenhuma
Status atual: 🚧 exibe score mas nao lista acoes acionaveis ordenadas por impacto
Esforco estimado: **1d**

---

**M365-07 — Dados/Labels: triggers para criar DLPs**
Rota: `GET /gov/governance/data`
Arquivos a modificar:
- `app/templates/dashboards/governance_data.html`: adicionar checklist de DLPs recomendadas
- `itgov/api/v1/governance_data.py`: incluir campo `dlp_recomendacoes` no resumo
Fonte de dados: Microsoft Graph `GET /beta/informationProtection/sensitivityLabels` + `GET /beta/compliance/ediscovery/cases`
Atual: exibe sensitivity labels aplicados. Nao mostra DLPs faltantes.
Dependencias: nenhuma
Status atual: 🚧 dados parciais, falta orientacao de DLP
Esforco estimado: **1,5d**

---

**M365-08 — Alertas Defender: verificar coleta e triggers de score**
Rota: `GET /gov/governance/security-alerts`
Arquivos: `itgov/api/v1/governance_security_alerts.py`, `app/templates/dashboards/governance_security_alerts.html`
Fonte de dados: Microsoft Graph `GET /security/alerts_v2` (Defender for Endpoint/Identity/Cloud Apps)
Permissao necessaria: `SecurityAlert.Read.All`
Tarefa: verificar se permissao esta concedida no App Registration. Adicionar coluna "acao para fechar" e link para portal Defender.
Dependencias: M365-06
Status atual: 🚧 blueprint existe, verificar se coleta esta funcionando
Esforco estimado: **1d**

---

### SUP — Suporte

---

**SUP-01 — Zendesk MTTR: verificar coleta**
Rota: `GET /gov/zendesk`
Arquivos: `itgov/api/v1/zendesk.py` (funcoes `get_cached_mttr_summary`, `get_cached_volume_by_status`)
Fonte de dados: Zendesk REST API `GET /api/v2/tickets` com paginacao
Nota: esta rota NAO requer login_required (linha 89-106 de `dashboards.py`). Corrigir.
Dependencias: nenhuma
Status atual: ✅ coleta funcionando (ZENDESK_SUBDOMAIN configurado)
Esforco estimado: **0,5d** (fix do login_required + validacao)

---

**SUP-02 — SLA/Chamados: filtrar por equipe TI/Infra do Grupo Gadens**
Rota: `GET /gov/sla`
Arquivos: `itgov/api/v1/zendesk.py` funcao `get_cached_sla_detail()`
Fonte de dados: Zendesk API `GET /api/v2/tickets?group_id=...` ou por tag
Tarefa: identificar IDs dos grupos "TI" e "Infra" no Zendesk do tenant e filtrar `get_cached_sla_detail()` por esses grupos.
Dependencias: SUP-01
Status atual: 🚧 traz todos os chamados, sem filtro de equipe
Esforco estimado: **1d**

---

**SUP-03 — Monitoramento Suporte: trazer dados dos agentes Zabbix**
Rota: `GET /gov/zabbix` (painel Zabbix existente)
Arquivos: `itgov/api/v1/zabbix_monitoring.py`
Fonte de dados: Zabbix API `host.get` com group "Agentes" + `item.get` para CPU/mem/disco
Tarefa: adicionar secao "Agentes de Suporte" no painel Zabbix mostrando status dos hosts de helpdesk/agentes.
Dependencias: nenhuma
Status atual: 🚧 painel Zabbix existe, falta secao de agentes
Esforco estimado: **1d**

---

### ZBX — Zabbix / Monitoramento

---

**ZBX-01 — Infraestrutura: dois paineis (cadastro + busca Fortigate por unidade/tipo)**
Rota: `GET /gov/infra`
Arquivos a modificar:
- `itgov/api/v1/infra_monitoring.py`: adicionar endpoint de busca por grupo Fortigate + tipo de ativo
- `app/templates/dashboards/infra_monitoring.html`: adicionar aba "Cadastro" (form para registrar ativo) e aba "Buscar" (filtro por unidade Sede/Shopping/Fabrica e tipo: Impressora/Servidor/VM/etc.)
Fonte de dados: Zabbix API `host.get` (com tags de tipo) + tabela `ativos` no SQLite (via `itgov/db/`)
Dependencias: nenhuma
Status atual: 🚧 painel existe, sem cadastro nem filtro por Fortigate/tipo
Esforco estimado: **3d**

---

**ZBX-02 — CFTV: template Intelbras + busca equipamentos nos Fortigates**
Rota: `GET /gov/cftv`
Arquivos: `itgov/api/v1/cftv_monitoring.py`, `app/templates/dashboards/cftv_monitoring.html`
Fonte de dados:
- Zabbix: hosts com template Intelbras NVR/Camera (SNMP)
- Fortigate: ARP tables das 3 unidades para descoberta de MACs Intelbras
Scripts existentes: `cftv-setup/` (blueprints de setup, nao de monitoramento continuo)
Tarefa: adicionar collector que varre ARP dos Fortigates (via SSH/API) e cruza com OUIs Intelbras (MACs 00:11:32, 4C:EB:42, etc.)
Dependencias: ZBX-01 (integracao Fortigate)
Status atual: 🚧 painel existe mas depende de templates Hikvision, Intelbras pendente
Esforco estimado: **2d**

---

**ZBX-03 — Rede: analise da rede interna**
Rota: `GET /gov/rede`
Arquivos: `itgov/api/v1/rede_monitoring.py`, `app/templates/dashboards/rede_monitoring.html`
Fonte de dados: Zabbix `drule.get` (discovery rules) + SNMP dos switches + InfluxDB `gov_network_*`
Tarefa: incluir latencia entre sites (ICMP Zabbix item), taxa de utilizacao de interfaces dos switches, mapa topologico simplificado.
Dependencias: nenhuma
Status atual: 🚧 painel existe, escopo a ampliar
Esforco estimado: **2d**

---

**ZBX-04 — Triggers/Alertas: abrir/fechar chamado Zendesk a partir de trigger Zabbix**
Rota: `GET /gov/triggers`, `POST /gov/triggers/<eventid>/ack`
Arquivos a modificar:
- `app/templates/dashboards/zabbix_triggers.html`: adicionar botao "Abrir Chamado" por trigger + campo de historico
- `itgov/api/v1/zabbix_triggers.py`: adicionar funcao `criar_chamado_zendesk(eventid, trigger_name)` e `fechar_chamado_zendesk(ticket_id)`
- `itgov/services/zendesk_service.py`: `create_ticket(subject, body, tags)` + `close_ticket(id)`
Fluxo:
1. Operator clica "Abrir Chamado" na trigger ativa
2. Sistema cria ticket Zendesk (grupo TI) + grava mapping trigger↔ticket no SQLite
3. Quando trigger e resolvida no Zabbix (ack), fecha ticket Zendesk automaticamente
4. Historico mostra status: Aberto / Resolvido / Ticket#
Dependencias: SUP-01, SUP-02
Status atual: ❌ nao existe
Esforco estimado: **3d**

---

### SEG — Seguranca

---

**SEG-01 — Vulnerabilidades: investigar escopo e planejar integracao**
Rota: nenhuma (soon=true, href='#')
Arquivos: `app/templates/partials/sidebar.html` linha 79
Fonte de dados potencial: Intune `GET /deviceManagement/managedDevices` (patch compliance) ou Defender Vulnerability Management `GET /security/vulnerabilities`
Tarefa: definir fonte (Defender vs Intune vs outro) e criar blueprint/namespace. Por ora manter "em breve".
Dependencias: M365-08 (Defender alerts ja configurado)
Status atual: ❌ nao existe (marcado "em breve" intencionalmente)
Esforco estimado: **5d** (spike 1d + implementacao 4d)

---

**SEG-02 — Backup/Acronis: renomear para "Cyber Acronis"**
Rota: `GET /gov/backup`
Arquivos a modificar:
- `app/templates/partials/sidebar.html` linha 80: mudar label de `'Backup / Acronis'` para `'Cyber Acronis'`
- `app/templates/dashboards/acronis_backup.html`: atualizar titulo da pagina (`<h1>`, `<title>`)
Fonte de dados: Acronis Cyber Protect API (endpoint existente em `itgov/api/v1/acronis_backup.py`)
Dependencias: nenhuma
Status atual: 🚧 funcional, so renomear label
Esforco estimado: **0,25d**

---

### GES — Gestao

---

**GES-01 — Contratos TI: novo modulo**
Rota: nova `GET /gov/contratos`
Arquivos a criar:
- `itgov/api/v1/contratos.py` — CRUD de contratos (fornecedor, objeto, valor mensal/anual, inicio, vencimento, renovacao automatica, contato)
- `app/templates/dashboards/contratos.html` — tabela com alertas de vencimento
- `alembic/versions/xxxx_create_contratos_table.py` — migracao SQLite
- `app/models/contrato.py` — model SQLAlchemy (ou adicionar na tabela `ativos`)
- Adicionar item no sidebar (grupo Gestao)
Campos sugeridos: fornecedor, descricao, modalidade (SaaS/Hardware/Servico), valor_mensal_brl, data_inicio, data_vencimento, renovacao_automatica (bool), contato_fornecedor, observacoes, arquivo_contrato (path)
Alertas: highlight vermelho se vencimento <= 30 dias, amarelo <= 90 dias
Dependencias: GES-01 e primeiro item novo, sem dependencias
Status atual: ❌ nao existe
Esforco estimado: **3d**

---

## Resumo do Status

| Item | Titulo | Status | Esforco |
|------|--------|--------|---------|
| GOV-01 | Icone mais bonito | ✅ funciona, tuning | 0,5d |
| GOV-02 | Visao Geral dados reais | ❌ MOCK | 4d |
| GOV-03 | Pilares COBIT dados reais | ❌ MOCK | 3d (dentro GOV-02) |
| M365-01 | Remover reajuste expirado | 🚧 expirou amanha | 0,5d |
| M365-02 | Licencas custo BRL | 🚧 UX fraca | 1d |
| M365-03 | Licencas over-provisioned | ✅ dado correto, tuning | 0,5d |
| M365-04 | Dispositivos 45d inatividade | 🚧 limiar errado (90d) | 0,5d |
| M365-05 | Apps com licenca + descricao | 🚧 scope diferente | 2d |
| M365-06 | Compliance triggers score | 🚧 sem acoes acionaveis | 1d |
| M365-07 | Dados/Labels triggers DLP | 🚧 dados parciais | 1,5d |
| M365-08 | Alertas Defender + triggers | 🚧 verificar permissao | 1d |
| SUP-01 | Zendesk MTTR verificar | ✅ + fix login_required | 0,5d |
| SUP-02 | SLA/Chamados filtro equipe | 🚧 sem filtro | 1d |
| SUP-03 | Monitoramento agentes Zabbix | 🚧 falta secao | 1d |
| ZBX-01 | Infra: cadastro + busca Fortigate | 🚧 sem cadastro/filtro | 3d |
| ZBX-02 | CFTV Intelbras + Fortigate ARP | 🚧 template pendente | 2d |
| ZBX-03 | Rede: analise interna | 🚧 escopo restrito | 2d |
| ZBX-04 | Triggers: abrir/fechar Zendesk | ❌ nao existe | 3d |
| SEG-01 | Vulnerabilidades: planejar | ❌ soon=true | 5d |
| SEG-02 | Renomear Backup/Acronis | 🚧 so label | 0,25d |
| GES-01 | Contratos TI novo modulo | ❌ nao existe | 3d |

**Total estimado:** ~37 dias-desenvolvedor
**Prioridade alta (quick wins):** SEG-02, M365-01, M365-04, GOV-01 (~1,75d)
**Prioridade alta (impacto):** GOV-02+03, ZBX-04, GES-01

---

## Ordem sugerida de execucao

**Semana 1 — Quick wins + fundacao**
1. SEG-02 (renomear Acronis) — 0,25d
2. M365-01 (remover reajuste) — 0,5d
3. M365-04 (dispositivos 45d) — 0,5d
4. M365-03 (over-provisioned visual) — 0,5d
5. GOV-01 (icone) — 0,5d
6. SUP-01 (fix login_required Zendesk) — 0,5d

**Semana 2 — M365 Governanca**
7. M365-06 (Compliance triggers) — 1d
8. M365-08 (Defender alerts) — 1d
9. M365-07 (DLP triggers) — 1,5d
10. M365-02 (Licencas UX) — 1d

**Semana 3 — Zabbix + Suporte**
11. ZBX-04 (Triggers → Zendesk) — 3d
12. SUP-02 (SLA filtro equipe) — 1d

**Semana 4 — Infra + CFTV + Novos**
13. ZBX-01 (Infra cadastro+Fortigate) — 3d
14. ZBX-02 (CFTV Intelbras) — 2d
15. GES-01 (Contratos TI) — 3d

**Sprint seguinte — Dados reais + Apps**
16. GOV-02+03 (Visao Geral + Pilares reais) — 4d
17. M365-05 (Apps com licenca) — 2d
18. ZBX-03 (Rede) — 2d
19. SEG-01 (Vulnerabilidades spike) — 5d
20. SUP-03 (Agentes Zabbix) — 1d
