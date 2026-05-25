# Postmortem — 2026-05-25 · Secrets Audit Day

**Severidade:** P0 (3 incidentes simultâneos)
**Duração total:** ~13h (InfluxDB) / ~2h (ZABBIX_TOKEN) / ~1h (Teams webhook)
**Autores:** Roberto (operador)
**Status:** ✅ Resolvido — P1 encerrado às 11:04 BRT | P2s pendentes (ver Action Items)

---

## Resumo Executivo

Durante uma auditoria de secrets nos arquivos `.env` do servidor `itgov-dev`, três
incidentes P0 foram identificados e tratados simultaneamente na manhã de 2026-05-25.
Nenhum dado externo foi comprometido. O impacto foi restrito ao ambiente interno:
dashboard indisponível por 13h, coleta Zabbix parcialmente comprometida durante
a rotação, webhook Teams permanentemente desativado.

---

## Timeline

| Horário | Evento |
|---------|--------|
| **2026-05-24 18:45** | InfluxDB faz último ciclo de retention policy normalmente |
| **2026-05-24 19:01** | Grafana começa a retornar 401 (GRAFANA_TOKEN já inválido) |
| **2026-05-24 19:02:44** | **[INCIDENTE 1]** Operador roda `sudo systemctl stop influxdb && sudo systemctl disable influxdb` manualmente |
| **2026-05-24 19:02–19:34** | App continua rodando (PID 303525) sem dados do InfluxDB; collectors.influx retornando erros silenciosos |
| **2026-05-24 19:34** | `.env` modificado (rotação em progresso) |
| **2026-05-25 manhã** | Auditoria de secrets detecta: InfluxDB disabled, ZABBIX_TOKEN destruído, TEAMS_WEBHOOK_URL vazada |
| **2026-05-25 08:20** | App reiniciado (PID 2138435) com `.env` pré-rotação — tokens desatualizados em memória |
| **2026-05-25 08:30** | InfluxDB restartado e habilitado: `systemctl start influxdb && systemctl enable influxdb` |
| **2026-05-25 08:35** | `.env` atualizado com novo ZABBIX_TOKEN (64 chars hex) após restauração de backup |
| **2026-05-25 09:20** | App reiniciado novamente (PID 2178435) — carregou `.env` atualizado. InfluxDB OK |
| **2026-05-25 09:20** | Claude Code: tokens InfluxDB temporários revogados (3 operator-recovery tokens) |
| **2026-05-25 09:27** | Claude Code: `GRAFANA_TOKEN_OLD` removida do `.env` ativo |
| **2026-05-25 09:32** | Healthcheck instalado como cron `*/5 * * * *` |

---

## Incidente 1 — InfluxDB DOWN por 13h

### Causa Raiz (confirmada)

| Campo | Valor |
|-------|-------|
| **Quem** | Roberto (operador) |
| **Quando** | 2026-05-24 19:02:44 (domingo à noite) |
| **Ação** | `sudo systemctl stop influxdb && sudo systemctl disable influxdb` |
| **Motivo original** | [Roberto: preencha — manutenção de token? rotação? teste?] |
| **Falha** | Operação não documentada em changelog, esquecida até 25/05 08:30 quando a auditoria de secrets revelou o serviço inativo |

Evidência no `journalctl`:
```
May 24 19:02:44 itgov-dev sudo[1611324]: zabbix : COMMAND=/usr/bin/systemctl stop influxdb
May 24 19:02:44 itgov-dev sudo[1611329]: zabbix : COMMAND=/usr/bin/systemctl disable influxdb
```

**Não foi crash, OOM, nem disco cheio.** Estado do servidor no momento:
- Disco: 24G usados / 983G total (3%)
- RAM: 4.6G usados / 17G total — sem pressão de memória
- InfluxDB saiu normalmente (`Deactivated successfully`) após SIGTERM do systemd

### Impacto
- Dashboard sem dados de métricas por ~13h
- Collectors continuaram tentando queries sem sucesso (sem crash explícito no app)
- Nenhum dado perdido — InfluxDB estava apenas parado, dados preservados

### Ação corretiva
```bash
sudo systemctl start influxdb
sudo systemctl enable influxdb
```

---

## Incidente 2 — ZABBIX_TOKEN destruído durante rotação

### Causa Raiz
O operador usou `read -s ZABBIX_TOKEN` para capturar o novo token via terminal,
mas digitou um valor de exemplo literal (`1***4@@@`) em vez do token real.
O valor foi escrito no `.env` e o app ficou com token inválido.

### Impacto
- Coleta Zabbix falhou durante o período com token corrompido
- O app não crashou (erros tratados como warnings)
- Dados de disponibilidade Zabbix podem estar incompletos no período

### Ação corretiva
1. Identificar backup mais recente com token válido
2. Restaurar token do backup
3. Gerar novo token no Zabbix UI (Settings > API Tokens)
4. Atualizar `.env` com novo token (64 chars hex)

### Pendência
- ⬜ Revogar token antigo `22a8...8628` no Zabbix UI após validação E2E confirmada

---

## Incidente 3 — TEAMS_WEBHOOK_URL vazada via `source .env`

### Causa Raiz
O operador executou `source .env` em uma shell com o arquivo contendo a
`TEAMS_WEBHOOK_URL` (1211 chars com `&` na query string). O `&` fez o shell
interpretar o restante como background job, expondo `sig=<valor>` no stdout
e possivelmente no histórico do terminal.

### Impacto
- URL completa do webhook (incluindo `sig=`) ficou visível no terminal
- Webhook imediatamente desativado como contenção

### Ação corretiva
```bash
# NUNCA fazer:
source .env

# Alternativas seguras:
set -a; . .env; set +a          # Funciona mas ainda sujeito a chars especiais
export $(grep -v '^#' .env | grep -v '&' | xargs)   # Arriscado

# Melhor: usar python-dotenv no código, não no shell
python3 -c "from dotenv import dotenv_values; print(dotenv_values('.env')['KEY'])"
```

### Pendência
- ⬜ Criar novo webhook no Teams (Connectors > Incoming Webhook)
- ⬜ Atualizar `.env` de forma segura: `python3 scripts/set_token_safe.sh TEAMS_WEBHOOK_URL`

---

## Descobertas Adicionais (auditoria)

### GRAFANA_TOKEN inválido (pré-existente)
- Grafana retornando 401 desde pelo menos **2026-05-24 19:01** (antes dos incidentes)
- Token no `.env` (`glsa_Lzb...2ee6cccb`) foi revogado ou nunca foi válido no Grafana
- `GRAFANA_TOKEN_OLD` (`glsa_Vc2I...8a345328`) removida do `.env` em 2026-05-25 09:27
- ⬜ Ação pendente: gerar novo token no Grafana UI (Service Accounts)

### OPS_PIN hardcoded no systemd unit
- `/etc/systemd/system/it-gov-dashboard.service` contém `Environment="OPS_PIN=..."` em plaintext
- Arquivos de unit são world-readable (`644`): qualquer usuário do sistema pode ler
- ⬜ Ação pendente: mover para `EnvironmentFile` com permissão restrita

### Token m365 collector desatualizado
- `/home/zabbix/governanca-m365/.env` contém `INFLUX_TOKEN` (`OzNDus0P...`) inexistente no InfluxDB
- Token ativo para m365 foi recriado como `m365-collector-write-20260517` mas o `.env` não foi atualizado
- ⬜ Ação pendente: atualizar `/home/zabbix/governanca-m365/.env` com o token `m365-collector-write-20260517`

### Tokens InfluxDB temporários (revogados)
- 3 tokens `operator-recovery*` e `operator-final*` do dia 2026-05-17 revogados em 2026-05-25 09:20

---

## Action Items para Prevenção

### Imediato (desta sessão)

- [ ] **Preencher motivo real** do `stop + disable` do InfluxDB em 2026-05-24 19:02 no postmortem
- [x] **Criar `CHANGELOG-OPS.md`** com entrada retroativa do incidente InfluxDB
- [x] **Adicionar aliases `sysstop`/`sysdisable`** ao `.bashrc` como guardrail cognitivo
- [ ] **Política de mudança manual**: toda operação `systemctl disable` ou `stop` em produção
      fora do horário comercial requer entrada no `CHANGELOG-OPS.md` **antes** de executar

### Curto prazo (esta semana)

- [ ] **Resolver conflito porta 8080** — reconfigurar `zabbix-mcp-server` para porta `8082`
      em `/etc/zabbix-mcp/config.toml`, reiniciar ambos os serviços, atualizar config Claude Desktop
- [ ] **Implementar verificação de `enabled+failed` e restart storms no healthcheck.sh**
      ```bash
      # Exemplo de verificação a adicionar:
      systemctl list-units --state=failed --no-legend | while read unit _; do
        echo "WARN: $unit está em estado failed"
      done
      ```
- [ ] **Auditoria mensal de serviços habilitados** — `systemctl list-unit-files --state=enabled`
      para detectar serviços instalados mas não funcionando
- [ ] **Revogar ZABBIX_TOKEN antigo** (`22a8...8628`) no Zabbix UI após validar E2E
- [ ] **Criar novo webhook Teams** e atualizar `.env` de forma segura (sem `source`)
- [ ] **Gerar novo GRAFANA_TOKEN** no Grafana UI (Service Accounts)
- [ ] **Atualizar `.env` do m365** com token `m365-collector-write-20260517`
- [ ] **Mover OPS_PIN** do systemd unit para `EnvironmentFile` com `chmod 600`
- [ ] **Salvar chave age** (`/root/.age-archive-key.txt`) em password manager externo

### Médio prazo (próximo sprint)

- [ ] **Implementar validação de formato de secrets no CI/CD**
  - Regex: `^[A-Fa-f0-9]{64}$` para tokens Zabbix
  - Regex: `^glsa_[A-Za-z0-9]{32}_[A-Fa-f0-9]{8}$` para tokens Grafana
  - Bloquear valores de exemplo: `changeme`, `***`, `@@@`, `example`

- [ ] **Migrar de `source .env` para `python-dotenv`**
  - Substituir qualquer uso de `source .env` / `export $(cat .env | xargs)` por leitura via Python
  - Adicionar linter no pre-commit: `grep -r "source.*\.env" scripts/` deve retornar vazio

- [ ] **Configurar alerta Zabbix para serviços críticos**
  - Adicionar item HTTP em `http://172.29.2.11:8086/health` com trigger se `status != "pass"`
  - Adicionar item processo `influxd` (mínimo 1 processo)
  - Considerar integrar com healthchecks.io como dead-man switch

- [ ] **Documentar política de rotação de secrets (90 dias)**
  - INFLUX_TOKEN: a cada 90 dias
  - ZABBIX_TOKEN: a cada 90 dias ou ao sair de colaborador
  - AZURE_CLIENT_SECRET: conforme política do Entra ID (recomendado 90 dias)
  - GRAFANA_TOKEN: a cada 90 dias
  - Criar reminder no calendário ou cron que loga aviso 14 dias antes do vencimento

### Longo prazo

- [ ] **Migrar secrets para Azure Key Vault**
  - Usar identidade gerenciada do servidor ou Service Principal dedicado
  - App lê secrets via SDK (`azure-keyvault-secrets`) em runtime, sem arquivo `.env`
  - Eliminar risco de secrets em arquivos de backup

- [ ] **Implementar auditoria automática de permissões**
  - Cron semanal verificando que arquivos `.env` são sempre `600`
  - Alerta se qualquer `.env` for `664` ou mais permissivo

---

## Lições Aprendidas

1. **Mudanças manuais sem trace são a causa #1 de incidentes em SRE** — O InfluxDB
   ficou 13h parado porque um `stop + disable` não foi documentado. A operação foi
   esquecida até a auditoria revelar o serviço como inativo. Se houvesse um
   CHANGELOG-OPS.md, a entrada "parei o InfluxDB às 19h por motivo X" teria
   tornado a recuperação imediata.

2. **Auditoria de secrets também funciona como health check involuntário** — Os três
   incidentes desta data foram descobertos não por alertas proativos, mas pela
   auditoria de secrets que mapeou o estado dos arquivos `.env`. Um healthcheck
   teria detectado o InfluxDB caído em ≤5 minutos; levou 13h.

3. **`stop + disable` em produção SEMPRE precisa de documentação obrigatória** —
   `disable` remove o autostart permanentemente; é uma mudança de configuração, não
   apenas operacional. A política daqui em diante: qualquer `systemctl disable` em
   produção requer uma linha no CHANGELOG-OPS.md **antes** de executar.

4. **`disable` é permanente** — `systemctl stop` + `systemctl disable` é uma combinação
   perigosa em manutenção; prefira `stop` sozinho a menos que queira impedir reinício
   permanente. Use o alias `sysstop` (veja Action Items) como guardrail.

5. **`source .env` é armadilha** — Qualquer caractere especial (`&`, `$`, backticks,
   aspas) na URL ou valor pode causar execução não-intencional de código. Use
   `python-dotenv` ou leia variáveis individualmente com `grep`.

6. **Tokens em memória ficam stale** — Flask carrega `.env` no início. Após qualquer
   atualização de secrets, o serviço **deve ser reiniciado** imediatamente ou os
   novos valores não têm efeito.

7. **Backups de `.env` salvam vidas** — O ZABBIX_TOKEN destruído foi recuperado
   graças ao backup pré-rotação. Manter backups com naming descritivo
   (`.env.bak-pre-<ação>-<timestamp>`) é essencial.

8. **Sem healthcheck = sem visibilidade** — InfluxDB ficou 13h parado sem alarme.
   Um simples `curl /health` no cron teria alertado em 5 minutos.

9. **Secrets em systemd units são riscos** — `EnvironmentFile=` com `chmod 600` é
   mais seguro que `Environment=` inline no unit file.

---

## 🎁 Achado Bônus — nginx em estado `enabled+failed` silencioso

Durante teste dos aliases `sysstop`/`sysdisable`, o `nginx.service` foi parado e não
conseguiu reiniciar: a porta 8080 foi imediatamente ocupada pelo `zabbix-mcp-server`,
que havia acumulado **14.784 reinicializações** tentando se ligar a essa porta enquanto
nginx a bloqueava.

**Como descobrimos:** Por acaso, ao tentar restaurar o nginx após o teste do guardrail.

**O problema real:** Conflito permanente de design — `zabbix-mcp-server` instalado com
`port = 8080` que já pertencia ao nginx de produção. O serviço nunca funcionou
completamente desde a instalação (14.798 NRestarts acumulados).

**Resolução (11:04 BRT):** Porta do `zabbix-mcp-server` movida de `8080` para `8082`
em `/etc/zabbix-mcp/config.toml` (linha 37). Nginx reiniciado. Produção restaurada.
Backup: `/etc/zabbix-mcp/config.toml.bak-20260525-103448`.

**Lição:** O healthcheck precisa monitorar não só serviços esperados rodando, mas também
detectar serviços em estado `enabled+failed` ou ciclos de restart anormais.

---

## Referências

- Relatório de auditoria de secrets: `/tmp/audit-secrets-20260525-080346/report.md`
- Script de auditoria: `/tmp/audit-secrets.sh`
- Script de healthcheck: `/opt/it-gov-dashboard/scripts/healthcheck.sh`
- Backups do `.env`: `/opt/it-gov-dashboard/.env.bak-pre-*`
- Arquivo de chave age: `/root/.age-archive-key.txt`
- Investigação nginx/porta 8080: `docs/ops/investigations/2026-05-25-nginx-port-8080.md`
