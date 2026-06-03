# 🔐 Gitleaks & Pre-commit — Guia de Configuração

## Visão Geral

Defense-in-depth contra secrets vazados, em 3 camadas independentes:

| Camada | Trigger | Ferramenta | Bypass possível |
|--------|---------|------------|-----------------|
| 1 — Local commit | `git commit` | pre-commit + gitleaks | `--no-verify` (não usar) |
| 2 — Local push | `git push` | pre-push hook | `--no-verify` (não usar) |
| 3 — CI/CD | PR / push / schedule | GitHub Actions | ❌ Sem bypass |

**Contexto:** implementado após LL-003 — secret M365 hardcoded por 24 dias
sem detecção. Ver `docs/incidents/LL-003-secret-rotation.md`.

---

## Setup em Novo Ambiente

### 1. Instalar Gitleaks

```bash
GITLEAKS_VERSION="8.21.2"
cd /tmp
wget https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz
tar -xzf gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz
sudo mv gitleaks /usr/local/bin/
sudo chmod +x /usr/local/bin/gitleaks
rm gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz LICENSE README.md

# Validar
gitleaks version
```

### 2. Instalar pre-commit

```bash
pip install pre-commit==4.0.1
# ou via requirements-dev.txt:
pip install -r requirements-dev.txt
```

### 3. Configurar no repositório

```bash
cd /home/zabbix/projects/it-governance-dashboard
./scripts/setup-precommit.sh
```

O script:
- Valida pré-requisitos
- Instala hooks (pre-commit + pre-push)
- Roda scan inicial em todos os arquivos
- Escaneia histórico completo do git

---

## Configuração Atual (`.gitleaks.toml`)

### Regras herdadas (useDefault = true)
Todas as ~150 regras built-in do Gitleaks v8:
AWS keys, GCP keys, GitHub tokens, Stripe, Twilio, JWT, RSA keys, etc.

### Regras customizadas do projeto

| ID | O que detecta |
|----|---------------|
| `influxdb-token` | InfluxDB v2 API tokens |
| `zabbix-api-token` | Zabbix API tokens (hex 64 chars) |
| `zendesk-api-token` | Zendesk API tokens |
| `entra-client-secret` | Entra ID / Azure AD client secrets (padrão `~Q`) |
| `msal-client-secret-inline` | `client_credential="..."` hardcoded em Python |
| `github-pat-classic` | `ghp_` tokens |
| `github-pat-fine-grained` | `github_pat_` tokens |
| `ldap-password-inline` | LDAP passwords em código |

### O que está no allowlist (ignorado intencionalmente)

| Padrão | Motivo |
|--------|--------|
| `.env`, `.env.local` | Nunca commitados (estão no .gitignore) |
| `.env.example` | Template — valores vazios ou placeholder |
| `tests/conftest.py` | Valores de CI mock conhecidos |
| `docs/**`, `*.md` | Documentação educativa |
| `__pycache__/`, `*.pyc` | Bytecode compilado |
| `htmlcov/`, `coverage.xml` | Artefatos de cobertura |
| `alembic/versions/` | Migrations geradas |
| `.venv/`, `venv/` | Dependências |

---

## Uso Diário

### Workflow normal

```bash
# Editar código → adicionar → commitar
git add arquivo.py
git commit -m "feat: ..."  # hooks rodam automaticamente
```

### Comandos manuais

```bash
# Scan completo de todos os arquivos
pre-commit run --all-files

# Só gitleaks
pre-commit run gitleaks --all-files

# Gitleaks direto (mais verboso)
gitleaks detect --source . --config .gitleaks.toml --verbose

# Scan histórico completo (para auditoria)
gitleaks detect --source . --config .gitleaks.toml
```

### Bypass de emergência

```bash
# ATENÇÃO: use só em situações excepcionais documentadas
git commit --no-verify -m "emergency: ..."
```

> Bypasses devem ser registrados em retrospectiva de sprint. Nunca usar
> para "agilizar" commits com secrets — o CI bloqueará de qualquer forma.

---

## CI/CD — `.github/workflows/security-scan.yml`

### Jobs ativos

| Job | Trigger | O que faz |
|-----|---------|-----------|
| `gitleaks` | PR, push main, schedule semanal | Scan completo com report SARIF |
| `pre-commit` | PR, push main, schedule semanal | Todos os hooks no código |
| `env-inventory` | Schedule semanal, workflow_dispatch | Verifica nenhum .env commitado |

### Schedule semanal (segunda 06:00 UTC)
Varredura profunda do histórico. Garante que secrets adicionados
em commits antigos sejam detectados mesmo fora de PRs.

---

## Quando Gitleaks Detecta um Secret

### Se for em commit local (pre-commit)

```
[ERROR] (gitleaks) commit rejected: detected secret
  Rule: entra-client-secret
  File: app/services/m365.py
  Line: 42
```

**Ações:**
1. Remover o secret do código
2. Mover para variável de ambiente / `.env`
3. `git add` + `git commit` novamente

### Se for em histórico existente

```
Finding: ... Secret: xyz
RuleID: entra-client-secret
Commit: abc123def
```

**Ações imediatas:**
1. Rotacionar o secret AGORA (antes de qualquer outra coisa)
2. Verificar logs de acesso (sign-in audit)
3. Limpar o histórico com BFG Repo-Cleaner
4. Documentar como LL-NNN em `docs/lessons-learned/`

```bash
# Limpeza com BFG (exemplo)
java -jar bfg.jar --replace-text replacements.txt .git
git reflog expire --expire=now --all && git gc --prune=now --aggressive
git push --force
```

> ⚠️ Force push em main requer coordenação com o time.

### Se for falso positivo

Adicionar ao allowlist em `.gitleaks.toml`:

```toml
[allowlist]
# Arquivo específico
paths = ['''caminho/do/arquivo\.py''']

# Padrão regex (todas as ocorrências)
regexes = ['''padrão-que-não-é-secret''']

# Commit específico
commits = ["abc123def456"]
```

> Documentar o motivo em comentário inline no `.gitleaks.toml`.

---

## Rotação de Secrets (Calendário)

| Secret | Próxima rotação | Frequência |
|--------|-----------------|------------|
| `AZURE_CLIENT_SECRET` (Entra ID) | Dez/2026 | Trimestral |
| `INFLUX_TOKEN` | A definir | Trimestral |
| `ZENDESK_API_TOKEN` | A definir | Semestral |
| `GITHUB_TOKEN` (CI) | Auto-renovado | — |

> Criar lembrete no calendário em: configurações → Alerts

---

## Referências

- [Gitleaks GitHub](https://github.com/gitleaks/gitleaks)
- [Gitleaks Action](https://github.com/gitleaks/gitleaks-action)
- [BFG Repo-Cleaner](https://rtyley.github.io/bfg-repo-cleaner/)
- `docs/incidents/LL-003-secret-rotation.md` — Incident example
- `docs/adr/ADR-009-m365-graph-integration.md` — Seção 2 (auth pattern)
