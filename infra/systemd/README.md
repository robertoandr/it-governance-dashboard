# systemd Service Templates

## it-gov-dashboard.service (ativo — V1.1)

Serviço gunicorn do IT Governance Dashboard V1.1 na porta 8091.

### Instalação

```bash
# Dry-run (valida sem instalar)
bash infra/systemd/install.sh --dry-run

# Instalação real
sudo bash infra/systemd/install.sh

# Sobrescrever variáveis padrão
SERVICE_USER=zabbix \
WORKDIR=/opt/it-gov-dashboard \
VENV_PATH=/opt/it-gov-dashboard/.venv \
ENV_FILE=/opt/it-gov-dashboard/.env \
LOG_DIR=/var/log/it-gov-dashboard \
sudo -E bash infra/systemd/install.sh
```

### Validação pós-instalação

```bash
# Verifica watchdog e limites
systemctl show it-gov-dashboard \
  -p WatchdogUSec,RestartUSec,StartLimitBurst,MemoryMax,CPUQuota

# Teste de crash (deve reiniciar em <10s)
OLD_PID=$(pgrep -f "gunicorn.*wsgi_prod" | head -1)
sudo kill -9 $OLD_PID
sleep 10 && systemctl is-active it-gov-dashboard

# Health check
curl -s http://127.0.0.1:8091/health | python3 -m json.tool
```

### Mudanças vs dashboard-ti V1.0

- `WatchdogSec=60`: detecta travamento mesmo sem crash explícito
- `StartLimitBurst=3`: evita crash loop silencioso (parava no 664º restart)
- `NoNewPrivileges/ProtectSystem=full/PrivateTmp`: hardening de segurança
- `MemoryMax=512M / CPUQuota=50%`: proteção de recursos do host

## dashboard-ti.service (DEPRECADO — 2026-06-07)

O serviço `dashboard-ti` foi deprecado em 2026-06-07.
Ver `docs/architecture/DEPRECATION-dashboard-ti-v1.md` para histórico completo.

Arquivos preservados em quarentena no servidor:
- `/opt/_deprecated/dashboard-ti-v1.0-20260607/`
- `/etc/systemd/system/dashboard-ti.service.deprecated-20260607`

Remoção definitiva: 2026-07-07
