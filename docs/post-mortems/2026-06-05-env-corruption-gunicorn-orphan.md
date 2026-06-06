# Post-Mortem: Corrupção de .env + Gunicorn Órfão

**Data:** 2026-06-05
**Severidade:** P2 — degradação silenciosa (sem downtime, com perda de funcionalidade)
**Duração do impacto:** Indeterminado → descoberto às ~20:00 BRT
**Resolvido em:** 2026-06-05 ~21:00 BRT
**Autor:** Roberto / Claude Sonnet 4.6

---

## Sumário Executivo

Durante uma sessão de debug do alert rule `m365-collector-down` (#130), foram descobertas duas corrupções silenciosas no arquivo `.env` de produção e um processo gunicorn órfão rodando em uma porta não monitorada. Nenhuma das anomalias havia disparado alertas. A descoberta foi 100% acidental.

---

## Linha do Tempo

| Hora (BRT) | Evento |
|------------|--------|
| Indeterminado | `TEAMS_WEBHOOK_URL` corrompida (4× concatenada, 1229 chars) |
| Indeterminado | `ZABBIX_FRONT_URL` duplicada no `.env` |
| 14:36 | Gunicorn sobe em `:8092` (causa: conflito de porta durante restart) |
| 15:33 | `python3 app.py` executado manualmente — Flask dev server ocupa `:8091`, causa race condition no systemd |
| 18:03 | Último erro `AADSTS7000215` (Azure secret antigo) — serviço reiniciado com secret correto |
| ~20:00 | Descoberta das corrupções durante debug do alert rule #130 |
| 20:01 | Backup `.env.bak.frankenstein.20260605-200135` criado |
| 20:10 | `.env` cirurgicamente corrigido (remoção `MSAL_*`, inserção `AZURE_*` com aspas) |
| 20:10 | Serviço reiniciado, novo secret Azure carregado |
| 20:30 | Gunicorn órfão (PID 814526, porta :8092) identificado e eliminado |
| 20:42 | Unit file corrigido (`KillMode=mixed`, `Type=simple`, `worker_tmp_dir`) |
| 21:00 | Validação completa — zero erros, dashboard respondendo |

---

## Incidente 1: TEAMS_WEBHOOK_URL Frankenstein

### O que aconteceu

A variável `TEAMS_WEBHOOK_URL` foi gravada 4× concatenada na mesma linha do `.env`, sem aspas, com fragmentos da query string embaralhados:

```
# Antes (linha 54, 1229 chars):
TEAMS_WEBHOOK_URL=https://...invoke?api-version=1TEAMS_WEBHOOK_URL=https://...&sp=...&sv=1.0&sig=...sp=...TEAMS_WEBHOOK_URL=https://...sv=1.0TEAMS_WEBHOOK_URL=https://...sig=...
```

Consequências:
- `source .env` disparava 9 jobs em background (`[1] Done sp=...`) a cada execução
- A variável era truncada silenciosamente pelo shell — webhook nunca funcionou após a corrupção
- `$TEAMS_WEBHOOK_URL` continha apenas o prefixo da URL, sem query string

### Causa raiz

Script de migração não-idempotente rodado múltiplas vezes durante o PR #128 (consolidação de env vars Azure). O script usava `>>` (append) em vez de substituição, e a URL contém `&` não escapado, que o shell interpreta como "rodar em background".

### Fix aplicado

```bash
sed -i '/^TEAMS_WEBHOOK_URL=/d' .env
cat >> .env <<'EOF'
TEAMS_WEBHOOK_URL="https://...invoke?api-version=1&sp=%2F...&sv=1.0&sig=91xiMA..."
EOF
```

Aspas duplas envolvendo o valor inteiro evitam que `&` seja interpretado pelo shell.

### Diagnóstico que teria pego antes

```bash
# Detecta linhas suspeitas
awk 'length > 500 {print NR": "length" chars"}' .env

# Detecta jobs em background ao fazer source
OUTPUT=$(set -a; source .env; set +a 2>&1); [ -z "$OUTPUT" ] || echo "ALERTA: $OUTPUT"
```

---

## Incidente 2: ZABBIX_FRONT_URL Duplicada

### O que aconteceu

`ZABBIX_FRONT_URL` aparecia duas vezes no `.env`. O shell usa o último valor definido, então não havia impacto funcional imediato — mas indica drift acumulado no arquivo.

### Causa raiz

Múltiplos `echo VAR=value >> .env` executados manualmente em sessões diferentes, sem verificação de idempotência.

### Fix

```bash
# Detecta duplicatas
grep -E '^[A-Z_]+=' .env | cut -d= -f1 | sort | uniq -c | awk '$1 > 1'
```

---

## Incidente 3: Gunicorn Órfão em :8092

### O que aconteceu

Um processo gunicorn master (PID 814526) estava rodando em `127.0.0.1:8092` há ~6 horas sem que nenhum nginx apontasse para ele. O VS Code Remote-SSH estava conectado a essa porta, provavelmente por alguma extensão com configuração hard-coded.

### Causa raiz — reconstrução forense

```
14:36 → systemctl restart it-gov-dashboard
         ├─ KillMode=control-group (padrão) — mata cgroup mas pode haver race
         └─ novo gunicorn sobe, mas encontra :8091 ocupado (?)
              └─ GUNICORN_BIND ou outra causa → fallback para :8092

15:33 → python3 app.py (Flask dev server) executado MANUALMENTE em :8091
         └─ conflito de porta → gunicorn seguinte vai para :8092

20:10 → restart limpo → novo master em :8091
         └─ master de :8092 (814526) NÃO foi morto → ficou órfão
```

**Regra violada:** nunca executar `python3 app.py` em produção enquanto o systemd gerencia o serviço.

### Fix aplicado

```bash
kill -TERM 814526   # SIGTERM — não respondeu em 10s
kill -KILL 814526   # SIGKILL — processo eliminado
```

### Fix estrutural (unit file)

```ini
# Antes
Type=notify
KillMode=control-group  # padrão implícito

# Depois
Type=simple
KillMode=mixed          # SIGTERM no master + todos filhos do cgroup
KillSignal=SIGTERM
TimeoutStopSec=30
SendSIGKILL=yes         # garante morte após timeout
```

---

## Incidente 4: `Permission denied: /home/zabbix` (Cosmético)

### O que aconteceu

A cada restart, o gunicorn logava:
```
[ERROR] Control server error: [Errno 13] Permission denied: '/home/zabbix'
```

### Causa raiz

O gunicorn usa `$HOME` como fallback para o socket de controle quando `worker_tmp_dir` não está configurado. Com `ProtectHome=true` no unit file, `/home` é somente-leitura para o processo — a criação do socket falha.

### Fix aplicado

`deploy/gunicorn.conf.py`:
```python
worker_tmp_dir = "/tmp"  # com PrivateTmp=true → /tmp privado do serviço
```

Com `PrivateTmp=true`, o serviço tem seu próprio namespace de mount para `/tmp`. Os `wgunicorn-*` são criados lá, fora do alcance de `ProtectHome=true`.

---

## Itens de Ação

| # | Ação | Responsável | Prazo | Issue |
|---|------|-------------|-------|-------|
| 1 | Implementar `scripts/validate-env.sh` com checks C1–C7 | Dev | Sprint 12 | #133 |
| 2 | Hook pre-commit que roda o validator | Dev | Sprint 12 | #133 |
| 3 | CI job `env-lint.yml` para validar `.env.example` em PRs | Dev | Sprint 12 | #133 |
| 4 | Nunca usar `python3 app.py` em produção — documentar no RUNBOOK | Ops | Imediato | — |
| 5 | Aplicar `cp` + `daemon-reload` do unit file (KillMode=mixed) | Roberto | Imediato | — |
| 6 | Habilitar Flow do Teams no Power Automate | Roberto | Imediato | — |
| 7 | Rotacionar Grafana token expirado | Roberto | Imediato | — |
| 8 | Confirmar deleção do Azure secret antigo no Portal | Roberto | Imediato | — |

---

## O Que Funcionou Bem

- Pre-commit hooks bloquearam commits com secrets (`detect private key`) durante toda a sessão
- `git log` e journal permitiram reconstrução forense completa sem gaps
- Backups criados antes de cada operação destrutiva
- Dashboard nunca ficou offline — todos os incidentes foram silenciosos/cosméticos

## O Que Precisa Melhorar

- Nenhum alerta disparou para nenhum dos 4 incidentes — todos descobertos acidentalmente
- `.env` não tem validação automática em nenhum ponto do ciclo de deploy
- Procedimentos de migração de env vars não são idempotentes por padrão
- `python3 app.py` não deveria ser possível em produção (considerar remoção do PATH ou wrapper)

---

## Arquivos de Evidência

```
/opt/it-gov-dashboard/.env.bak.frankenstein.20260605-200135  — .env antes da cirurgia
/tmp/orphan-814526-snapshot.txt                              — snapshot do gunicorn órfão
/tmp/rule-before.json                                        — alert rule antes do fix
/tmp/rule-after.json                                         — alert rule corrigida (eq→lt)
```

---

## Commits Relacionados

| SHA | Descrição |
|-----|-----------|
| `4ae9256` | refactor(config): consolidate Azure env vars + add contract test |
| `e4a9cc7` | fix(deploy): resolve gunicorn orphan + ProtectHome conflict |

## Issues Abertas

| # | Título |
|---|--------|
| #129 | tech-debt: decidir destino do app/config.py (Pydantic Settings) |
| #130 | Alert rule 'm365-collector-down' usa expressão 'eq' inválida |
| #131 | Configurar contact point Teams webhook |
| #132 | Logs cosméticos VS Code Remote-SSH token antigo |
| #133 | Validador de .env no pre-commit + CI (prevenção de Frankenstein) |
