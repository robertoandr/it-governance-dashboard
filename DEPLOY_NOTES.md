# Deploy Notes — Hot-patch e remoção de bind mounts

## Contexto

O serviço `app` em `docker-compose.yml` usa dois bind mounts de hot-patch para
sobrescrever arquivos da imagem publicada sem precisar de rebuild:

```yaml
# docker-compose.yml — serviço app, seção volumes
- ./app/services/influxdb_provider.py:/app/app/services/influxdb_provider.py:ro
- ./app/templates/dashboards/overview.html:/app/app/templates/dashboards/overview.html:ro
```

Esses mounts existem porque a imagem `ghcr.io/robertoandr/it-governance-dashboard:latest`
ainda não inclui as alterações abaixo (introduzidas após o último push):

| Arquivo hot-patcheado | O que mudou |
|---|---|
| `app/services/influxdb_provider.py` | `_acronis_stats()` + 3 componentes de backup (score/noise/health_24h) + formula tiered |
| `app/templates/base.html` | Alpine CDN trocado para CSP build (`@alpinejs/csp`); `layout` component registrado via `Alpine.data()` |
| `app/templates/partials/topbar.html` | `@click="sidebarOpen = !sidebarOpen"` → `@click="toggleSidebar"` (CSP) |
| `app/templates/dashboards/overview.html` | `kioskDashboard` refatorado para `Alpine.data()` + todos os getters computados (sem eval) |

### TODO — Sprint 13 (registrado aqui, não fazer agora)

- `backup_health_24h`: requer coletor que consulte `/api/alert_manager/v1/alerts?since=<24h>` no Acronis e grave em `gov_acronis_backup_health_24h` no InfluxDB. Até lá, componente fica `source=coming_soon, is_estimated=True`.
- nginx `/gov/` fix: aplicado via hot-patch em `/tmp/noc-nginx-modified.conf` (ver TAREFA 1). Precisa de `sudo cp + nginx -t + systemctl reload`.

---

## Checklist de remoção dos bind mounts

Executar **na ordem** quando estiver pronto para publicar a imagem nova:

- [ ] Confirmar que todos os testes passam localmente:
  ```bash
  cd /home/zabbix/projects/it-governance-dashboard
  python -m pytest tests/ -x -q
  ```

- [ ] Build da imagem com as alterações já incorporadas:
  ```bash
  docker build \
    -f docker/Dockerfile \
    -t ghcr.io/robertoandr/it-governance-dashboard:latest \
    -t ghcr.io/robertoandr/it-governance-dashboard:$(date +%Y%m%d) \
    .
  ```

- [ ] Login no GHCR (se necessário):
  ```bash
  echo $GITHUB_TOKEN | docker login ghcr.io -u robertoandr --password-stdin
  ```

- [ ] Push da imagem:
  ```bash
  docker push ghcr.io/robertoandr/it-governance-dashboard:latest
  docker push ghcr.io/robertoandr/it-governance-dashboard:$(date +%Y%m%d)
  ```

- [ ] Remover os dois bind mounts de hot-patch do `docker-compose.yml` (bloco
  comentado como "HOT-PATCH temporário" no serviço `app`).

- [ ] Recriar o container com a imagem nova:
  ```bash
  docker compose pull app
  docker compose up -d --force-recreate app
  ```

- [ ] Validar que `/api/overview` continua retornando `backup_success_rate` com
  `source: "acronis"` e `is_estimated: false`:
  ```bash
  curl -sk https://172.29.2.11:8443/api/overview | \
    python3 -m json.tool | grep -A 8 backup_success_rate
  ```

- [ ] Validar `zendesk_trigger.py` (dry-run) após a troca de imagem:
  ```bash
  ZENDESK_DRY_RUN=true python3 /opt/zabbix/zendesk_trigger.py
  ```

---

## Referência rápida — o que o `_acronis_stats()` lê

O `influxdb_provider.py` hot-patcheado lê do InfluxDB o measurement
`gov_acronis_risk_summary` (gravado pelo `acronis_risk_collector` a cada ciclo):

| Campo InfluxDB | Significado | Usado em |
|---|---|---|
| `sem_plano` | Máquinas sem plano de proteção ativo | `raw_value` do componente `backup_success_rate` |
| `backup_noise_total` | Alertas `BackupFailed` acumulados | Penalidade no score (`-5 pts` cada, cap 50) |
| `offline_gt_20d` | Máquinas offline > 20 dias | Informativo (Zabbix é a fonte primária desse check) |
