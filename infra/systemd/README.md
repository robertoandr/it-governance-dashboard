# systemd Service Templates

## dashboard-ti.service

Serviço gunicorn do dashboard legado na porta 8082.

### Instalação

```bash
# Substitui variáveis antes de copiar
SERVICE_USER=dashboard-ti
WORKDIR=/opt/dashboard-ti-legacy
VENV_PATH=/opt/dashboard-ti-legacy/venv
ENV_FILE=/etc/dashboard-ti/secrets.env
LOG_DIR=/var/log/dashboard-ti
DATA_DIR=/opt/dashboard-ti-legacy/data

sed -e "s|{{ SERVICE_USER }}|$SERVICE_USER|g" \
    -e "s|{{ SERVICE_GROUP }}|$SERVICE_USER|g" \
    -e "s|{{ WORKDIR }}|$WORKDIR|g" \
    -e "s|{{ VENV_PATH }}|$VENV_PATH|g" \
    -e "s|{{ ENV_FILE }}|$ENV_FILE|g" \
    -e "s|{{ LOG_DIR }}|$LOG_DIR|g" \
    -e "s|{{ DATA_DIR }}|$DATA_DIR|g" \
    dashboard-ti.service.template > /tmp/dashboard-ti.service

sudo systemd-analyze verify /tmp/dashboard-ti.service
sudo cp /tmp/dashboard-ti.service /etc/systemd/system/dashboard-ti.service
sudo systemctl daemon-reload
sudo systemctl restart dashboard-ti
sudo systemctl status dashboard-ti
```

### Validação pós-instalação

```bash
# Verifica watchdog e limites
systemctl show dashboard-ti -p WatchdogUSec,RestartUSec,StartLimitBurst,MemoryMax

# Teste de crash loop (deve reiniciar em <10s)
sudo kill -9 $(pgrep -f gunicorn | head -1)
sleep 5 && systemctl is-active dashboard-ti

# Health check
curl -s http://127.0.0.1:8082/health | python3 -m json.tool
```

### Mudanças vs versão anterior

- `WatchdogSec=60`: detecta travamento mesmo sem crash explícito
- `StartLimitBurst=3`: evita crash loop silencioso
- `NoNewPrivileges/ProtectSystem/PrivateTmp`: hardening de segurança
- `MemoryMax=512M / CPUQuota=50%`: proteção de recursos do host
