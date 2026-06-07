# Postmortem: Zabbix Credentials + :8082 Crash Loop

**Data:** 2026-06-07
**Severidade:** P1 (security) + P2 (silent prod incident)
**Duração:** ~4h (detecção → resolução)
**Autor:** Roberto
**Status:** Resolvido

## Resumo Executivo

Durante recon do host para migração do :8080 (épico D.0), foram descobertos
dois problemas críticos não relacionados ao escopo original:

1. **P1 Security:** `ZABBIX_PASSWORD=zabbix` (credencial default) hardcoded
   em `/etc/systemd/system/dashboard-ti.service`
2. **P2 Incidente Silencioso:** serviço `dashboard-ti` em crash loop desde
   que Docker tomou a porta :5000 (data desconhecida, possivelmente dias)

## Timeline

| Tempo  | Evento |
|--------|--------|
| T+0    | Recon revela credencial default em unit systemd |
| T+30m  | Issue P1 aberta, remediação iniciada |
| T+1h   | `EnvironmentFile` (640) implementado, senha rotacionada para `svc_dashboard` |
| T+2h   | Audit git history — leak encontrado em commit de docs `280d94d` |
| T+2h30 | Commit reescrito via amend + `--force-with-lease` no PR #151 |
| T+3h   | Conflito :5000 (Docker vs systemd) identificado via journald |
| T+3h30 | Serviço migrado para :8082, crash loop resolvido |
| T+4h   | Validação E2E: login `svc_dashboard` OK, token retornado |

## Root Cause Analysis

### Credenciais hardcoded

- **Causa direta:** projeto nasceu usando defaults do Zabbix, nunca rotacionados
- **Causa raiz:** ausência de security review no onboarding do serviço +
  sem pre-commit hooks para detectar secrets em arquivos de configuração

### Crash loop silencioso

- **Causa direta:** Docker subiu serviço em `0.0.0.0:5000` que bloqueou
  `127.0.0.1:5000` do `dashboard-ti.service`; systemd reiniciava indefinidamente
- **Causa raiz:** ausência de healthcheck monitorado pelo Zabbix +
  porta hardcoded no unit sem verificação de conflito

## Action Items

### Concluídos

- [x] Rotacionar senha Zabbix (usuário `svc_dashboard` com permissões mínimas)
- [x] `EnvironmentFile=/etc/dashboard-ti/secrets.env` com perm `640`, owner `root:zabbix`
- [x] Purgar histórico git (amend + force-with-lease no PR #151)
- [x] Migrar porta `:5000` → `:8082` no `dashboard-ti.service`
- [x] Documentar topologia real em `docs/migration/8080-inventory.md`

### Pendentes (épico D.X — Hardening)

- [ ] Pre-commit com `gitleaks` + `detect-secrets` para arquivos `.service`
- [ ] CI/CD security scan no GitHub Actions
- [ ] Healthcheck systemd com `Type=notify` + `WatchdogSec`
- [ ] Alerting Zabbix para auto-monitoramento de serviços críticos no host
- [ ] Migração de secrets para Vault ou SOPS
- [ ] Auditoria do TeamViewer em `/opt/teamviewer` (P2 pendente)

## Licoes Aprendidas

1. **Recon estruturado salva vidas** — incidente silencioso só foi detectado
   por acaso durante D.0; sem o inventário, poderia durar semanas
2. **Default credentials = P1 sempre** — independente do contexto ou idade do serviço
3. **systemd sem healthcheck é caixa preta** — falhas viram tribal knowledge
4. **Commit amend + force-push exige atenção** — verificar `--force-with-lease`
   e comunicar no PR antes de reescrever histórico compartilhado

## Referencias

- PR #150: inventário forense inicial do :8080
- PR #151: topologia corrigida + postmortem
- CWE-798: Use of Hard-coded Credentials
- `docs/migration/8080-inventory.md` secao 10.5
