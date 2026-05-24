# Exceções de Segurança e Lint — Documentação

> **Princípio:** Toda exceção a regras de lint/security deve ter justificativa
> rastreável. Esta documentação serve para auditoria SOC2/ISO 27001.

## Bandit — Skips Configurados

### B104 — Hardcoded bind all interfaces (`0.0.0.0`)
- **Local:** `app.py:378` (Flask host)
- **Justificativa:** Configurável via env var `FLASK_HOST`. Default em dev é
  `127.0.0.1`. Em produção, o bind `0.0.0.0` é necessário pois o container
  expõe a porta apenas para a rede privada interna.
- **Mitigação:** Firewall + Network Policy do K8s + reverse proxy nginx.
- **Aprovador:** @robertoandr

### B108 — Hardcoded /tmp directory
- **Local:** `cftv-setup/06_import_hikvision_template.py`
- **Justificativa:** Script one-shot de provisionamento, executado manualmente
  via runbook. Arquivo temporário é descartado após import do template.
- **Mitigação:** Script não roda em runtime de produção.

### B310 — Audit URL open
- **Local:** `cftv-setup/*.py`, `collectors/*.py`
- **Justificativa:** URLs validadas em runtime contra allowlist
  (`config.ALLOWED_URL_SCHEMES`). Não há entrada de usuário direta.
- **Mitigação:** `validators.url()` antes de `urlopen()`.

## Ruff — Exclusões

### `cftv-setup/` — Scripts de Provisionamento
- **Categoria:** Utility scripts (não runtime de produção)
- **Justificativa:** Executados manualmente uma única vez por ambiente para
  configurar templates/macros/triggers do Zabbix. Não fazem parte do
  pipeline de execução do dashboard.
- **Controle compensatório:** Code review obrigatório + execução controlada
  via runbook documentado em `docs/runbooks/cftv-setup.md`.

## Revisão Periódica

Esta lista deve ser revisada **trimestralmente** pelo Tech Lead.

| Revisão | Data | Responsável | Mudanças |
|---------|------|-------------|----------|
| Inicial | 2026-05-23 | @robertoandr | Criação |
