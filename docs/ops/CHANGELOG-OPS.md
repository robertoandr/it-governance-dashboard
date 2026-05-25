# CHANGELOG-OPS — it-gov-dashboard / itgov-dev

Registro de operações manuais realizadas em produção fora do fluxo normal de deploy.

## Regra de uso

**Toda mudança manual que afete serviços, arquivos de configuração, secrets ou
infraestrutura deve ter uma entrada aqui ANTES de ser executada.**

Especialmente obrigatório para:
- `systemctl stop/disable/mask` em qualquer serviço
- Edição direta de `.env` em produção
- Rotação manual de tokens/secrets
- Alteração de permissões em arquivos críticos
- Qualquer operação `sudo` fora do horário comercial

Formato da entrada:

```
## [DATA HORA] — [DESCRIÇÃO CURTA]
- **Quem:** nome
- **Motivo:** por que foi necessário
- **Ação:** comando(s) exato(s) executado(s)
- **Rollback:** como desfazer se der errado
- **Status:** [planejado | executado | revertido]
```

---

## Entradas

---

## [2026-05-24 19:02] — `systemctl stop + disable influxdb` ⚠️ NÃO DOCUMENTADO NA ÉPOCA

- **Quem:** Roberto (zabbix@itgov-dev)
- **Motivo:** [PREENCHER — manutenção de token? rotação? teste de conectividade?]
- **Ação:**
  ```bash
  sudo systemctl stop influxdb
  sudo systemctl disable influxdb
  ```
- **Rollback:**
  ```bash
  sudo systemctl start influxdb
  sudo systemctl enable influxdb
  ```
- **Status:** executado sem documentação → descoberto via auditoria em 2026-05-25 08:30
- **Consequência:** InfluxDB DOWN por ~13h; dashboard sem dados de métricas no período
- **Entrada retroativa criada em:** 2026-05-25 (Claude Code incident response)
- **Ref. postmortem:** `docs/incidents/2026-05-25-secrets-audit-day.md`

---

## [2026-05-25 08:30] — Restart e re-enable do InfluxDB

- **Quem:** Roberto (zabbix@itgov-dev)
- **Motivo:** Correção do incidente P0 — InfluxDB parado desde 19:02 do dia anterior
- **Ação:**
  ```bash
  sudo systemctl start influxdb
  sudo systemctl enable influxdb
  ```
- **Rollback:** N/A (operação de restauração)
- **Status:** executado ✅

---

## [2026-05-25 08:35] — Atualização ZABBIX_TOKEN no .env

- **Quem:** Roberto (zabbix@itgov-dev)
- **Motivo:** Token anterior destruído durante rotação (valor de exemplo digitado literalmente)
- **Ação:** Restauração de backup + geração de novo token 64 chars hex no Zabbix UI
- **Rollback:** restaurar `.env.bak-pre-rotation-*`
- **Status:** executado ✅

---

## [2026-05-25 09:20] — Revogação de tokens InfluxDB temporários

- **Quem:** Claude Code (Roberto@itgov-dev — sessão incident response)
- **Motivo:** Limpeza de tokens de operação temporária criados em 2026-05-17
- **Ação:**
  ```bash
  influx auth delete --id 10b9ac73a57ea000  # operator-recovery-20260517-1310
  influx auth delete --id 10b9ace7c9bea000  # operator-recovery2-20260517-1312
  influx auth delete --id 10b9ad31057ea000  # operator-final-20260517-1314
  ```
- **Rollback:** não é possível restaurar tokens revogados — recriar se necessário
- **Status:** executado ✅

---

## [2026-05-25 09:27] — Remoção de GRAFANA_TOKEN_OLD do .env

- **Quem:** Claude Code (Roberto@itgov-dev — sessão incident response)
- **Motivo:** Variável `_OLD` residual no `.env` ativo; token já inválido no Grafana
- **Ação:** Edição cirúrgica do `.env` — linha `GRAFANA_TOKEN_OLD=...` removida
- **Backup criado:** `.env.bak-pre-cleanup-20260525-092741`
- **Rollback:** `cp .env.bak-pre-cleanup-20260525-092741 .env`
- **Status:** executado ✅

---

## [2026-05-25 09:20] — Correção de permissão .env governanca-m365

- **Quem:** Claude Code (Roberto@itgov-dev — sessão incident response)
- **Motivo:** Arquivo com permissão `664` (world-readable no grupo) — deveria ser `600`
- **Ação:** `chmod 600 /home/zabbix/governanca-m365/.env`
- **Rollback:** `chmod 664 /home/zabbix/governanca-m365/.env` (não recomendado)
- **Status:** executado ✅

---

---

## [2026-05-25 09:57] — `sysstop nginx` — teste de guardrail (Roberto) ⚠️ CAUSOU IMPACTO

- **Quem:** Roberto (zabbix@itgov-dev) via alias `sysstop`
- **Motivo:** Teste dos novos aliases `sysstop`/`sysdisable` do `.bashrc`
- **Ação:** `sudo systemctl stop nginx`
- **Consequência inesperada:** O `zabbix-mcp-server` (que crashava a cada 5s tentando
  ocupar a porta 8080 bloqueada pelo nginx) imediatamente se ligou à porta 8080 ao
  nginx ser parado. Nginx não consegue mais iniciar.
- **Impacto:** nginx em estado `failed`; Zabbix UI, API JSON-RPC e HTTPS em
  noc.grupogadens.com.br inativas. Equipe completa + diretoria afetados.
- **Status:** ✅ Resolvido às 11:04 BRT (ver entrada abaixo)
- **Ref. investigação:** `docs/ops/investigations/2026-05-25-nginx-port-8080.md`

## [2026-05-25 11:04 BRT] — ✅ P1 RESOLVIDO — Conflito porta 8080: nginx × zabbix-mcp-server

- **Quem:** Roberto (itgov-dev)
- **Motivo:** noc.grupogadens.com.br fora do ar; conflito permanente de design
  — zabbix-mcp-server configurado em :8080, mesma porta do nginx de produção.
  Serviço acumulou 14.798 NRestarts. Nginx não subia depois do teste de guardrail.
- **Ação executada:**
  1. Backup: `/etc/zabbix-mcp/config.toml.bak-20260525-103448`
  2. Alterado `port = 8080` → `port = 8082` em `/etc/zabbix-mcp/config.toml` (linha 37)
  3. `sudo systemctl restart zabbix-mcp-server` — NRestarts zerado, estável
  4. `sudo systemctl start nginx` — active/running
- **Rollback:** `sudo cp /etc/zabbix-mcp/config.toml.bak-20260525-103448 /etc/zabbix-mcp/config.toml && sudo systemctl restart zabbix-mcp-server`
- **Validação:**
  - ✅ Zabbix UI: HTTP 200
  - ✅ Zabbix API v7.0.26 respondendo
  - ✅ MCP server em :8082 (uvicorn estável)
  - ✅ Flask app :8091 OK
  - ✅ InfluxDB :8086 OK
- **Status:** ✅ Produção restaurada
- **Pendências P2:**
  - [ ] Criar `docs/ops/PORT-REGISTRY.md`
  - [ ] Investigar causa raiz dos 14.798 restarts (config inválida? token?)
  - [ ] Atualizar Claude Desktop config (Windows) para MCP em :8082
  - [ ] Alerta no Zabbix para NRestarts > 10 em qualquer systemd unit
  - [ ] Renovar/verificar certificado HTTPS noc.grupogadens.com.br
  - [ ] Postmortem completo do P1

<!-- ADICIONE NOVAS ENTRADAS ACIMA DESTA LINHA -->
