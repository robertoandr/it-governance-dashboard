# Deprecação: dashboard-ti V1.0

**Data:** 2026-06-07
**Decisão:** Deprecar backend Flask legacy `dashboard-ti.service`
**Status:** Executado

## Contexto

O `dashboard-ti.service` foi um MVP standalone criado em maio/2026, rodando
como Flask + Gunicorn em `127.0.0.1:8082`. Servia métricas básicas do Zabbix
para um HTML estático (`/usr/share/nginx/html/dashboard-ti.html`).

## Evidências de Obsolescência

- Último log de atividade: 2026-05-14 (24 dias antes da deprecação)
- Zero referências em nginx `proxy_pass`, crons, ou outros services
- Zabbix não monitorava o endpoint
- **664 restarts em crash loop** por conflito de porta com `zabbix-mcp-server`
- Substituído pela arquitetura V1.1 (`it-gov-dashboard.service` na porta 8091)

## Ação Executada

| Recurso | Estado Anterior | Estado Atual |
|---------|----------------|--------------|
| `/opt/dashboard-ti/` | Ativo | `/opt/_deprecated/dashboard-ti-v1.0-20260607/` |
| `dashboard-ti.service` | Crash loop | `.deprecated-20260607` (arquivado) |
| `/etc/dashboard-ti/secrets.env` | Ativo | `/etc/_deprecated/dashboard-ti-20260607/` |
| Porta `:8082` | Conflitada | Livre para `zabbix-mcp-server` |
| HTML estático nginx | Ativo | **Ativo** (não afetado) |

## Plano de Remoção Definitiva

- **2026-07-07:** Remover `/opt/_deprecated/dashboard-ti-v1.0-20260607/`
- **2026-07-07:** Remover `/etc/_deprecated/dashboard-ti-20260607/`
- **2026-07-07:** Avaliar migração do `dashboard-ti.html` para V1.1 ou deprecação

## Rollback (emergência)

```bash
sudo mv /opt/_deprecated/dashboard-ti-v1.0-20260607 /opt/dashboard-ti
sudo mv /etc/_deprecated/dashboard-ti-20260607 /etc/dashboard-ti
sudo mv /etc/systemd/system/dashboard-ti.service.deprecated-20260607 \
        /etc/systemd/system/dashboard-ti.service
sudo systemctl daemon-reload
sudo systemctl enable --now dashboard-ti
```

## Issues Relacionadas

- Issue #155 — ADR-001: Arquitetura única (Docker vs systemd)
- Issue #156 — Fix: Permission denied no it-gov-dashboard
- Issue #157 — docs: port allocation registry
- Issue #126 — P3: separação de usuários (zabbix/zadmin/itgov)
- PR #154 — B.2: healthchecks systemd + Zabbix self-monitoring
