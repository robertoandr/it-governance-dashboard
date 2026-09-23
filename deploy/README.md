# Deploy — IT Governance Dashboard

Migração do Flask dev server (`python3 app.py`) para Gunicorn gerenciado por systemd.

## Pré-requisitos

```bash
# Verificar venv e gunicorn
/opt/it-gov-dashboard/.venv/bin/python --version   # Python 3.11+
/opt/it-gov-dashboard/.venv/bin/gunicorn --version  # 21+

# Se gunicorn não estiver instalado:
/opt/it-gov-dashboard/.venv/bin/pip install "gunicorn[gthread]>=21"
# Adicionar ao requirements.txt:
echo 'gunicorn[gthread]>=21' >> /opt/it-gov-dashboard/requirements.txt

# Garantir que logs/ e data/sessions/ existem
mkdir -p /opt/it-gov-dashboard/logs
mkdir -p /opt/it-gov-dashboard/data/sessions
```

## Aplicar a Migração

```bash
# 1. Snapshot de segurança
sudo cp -a /opt/it-gov-dashboard /opt/it-gov-dashboard.bkp-$(date +%Y%m%d-%H%M)

# 2. Instalar o systemd unit
sudo cp /opt/it-gov-dashboard/deploy/it-gov-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload

# 3. Habilitar para iniciar no boot
sudo systemctl enable it-gov-dashboard

# 4. Parar o processo atual (python3 app.py)
OLD_PID=$(ps aux | grep "python3.*app.py" | grep -v grep | awk '{print $2}')
sudo kill -TERM $OLD_PID
sleep 3

# 5. Iniciar o Gunicorn via systemd
sudo systemctl start it-gov-dashboard
sudo systemctl status it-gov-dashboard

# 6. Smoke test
curl -s -o /dev/null -w ":8091 -> %{http_code}\n" http://127.0.0.1:8091/
curl -s -o /dev/null -w ":8080 -> %{http_code}\n" http://127.0.0.1:8080/
```

## Operação Diária

```bash
# Status
sudo systemctl status it-gov-dashboard

# Logs em tempo real
sudo journalctl -u it-gov-dashboard -f

# Logs das últimas 100 linhas
sudo journalctl -u it-gov-dashboard -n 100 --no-pager

# Restart (aguarda workers drenarem requests em curso)
sudo systemctl restart it-gov-dashboard

# Reload zero-downtime (SIGHUP — workers reiniciam gradualmente)
sudo systemctl reload it-gov-dashboard

# Stop
sudo systemctl stop it-gov-dashboard
```

## Ajustar Workers

Editar `deploy/gunicorn.conf.py`, alterar a fórmula ou o cap:

```python
workers = min(_cpu * 2 + 1, 9)  # padrão: 9 em host de 4 CPUs
```

Depois reiniciar (não é zero-downtime — workers precisam ser recriados):

```bash
sudo systemctl restart it-gov-dashboard
```

## Rollback

```bash
# 1. Parar o Gunicorn
sudo systemctl stop it-gov-dashboard
sudo systemctl disable it-gov-dashboard

# 2. Remover o unit (opcional — pode deixar desabilitado)
sudo rm /etc/systemd/system/it-gov-dashboard.service
sudo systemctl daemon-reload

# 3. Restaurar python3 app.py
cd /opt/it-gov-dashboard
nohup .venv/bin/python app.py >> logs/healthcheck.log 2>&1 &
echo "Processo iniciado: PID=$!"

# 4. Verificar
curl -s -o /dev/null -w ":8091 -> %{http_code}\n" http://127.0.0.1:8091/
```

Se precisar restaurar do backup:

```bash
sudo systemctl stop it-gov-dashboard
sudo rm -rf /opt/it-gov-dashboard
sudo cp -a /opt/it-gov-dashboard.bkp-YYYYMMDD-HHMM /opt/it-gov-dashboard
# reiniciar o processo manualmente (ver passo 3 acima)
```

## Coleta diária + backup (`governanca-ti-coleta`)

O timer `governanca-ti-coleta.timer` roda `deploy/governanca-ti-coleta` (coleta Zabbix/M365 +
`scripts/backup_governanca.sh`). O **código** vem de um checkout de operação separado, fixo no
`main`; os **dados** (`.env`, `backups/`, stack Docker no ar) continuam no checkout de
desenvolvimento, indicado por `PROJECT_DIR` no unit:

| Papel                         | Caminho                                          |
|-------------------------------|--------------------------------------------------|
| Código da coleta/backup       | `/opt/itgov-backup`                              |
| Instância (`PROJECT_DIR`)     | `/home/zabbix/projects/it-governance-dashboard`  |

Motivo: o unit apontava direto para o checkout de desenvolvimento, e trocar de branch ali (uma
branch sem `deploy/governanca-ti-coleta`) fazia o backup noturno falhar sem ninguém perceber.

Não rode `docker compose` a partir de `/opt/itgov-backup`: o nome do projeto compose sairia
`itgov-backup` e criaria containers/volumes paralelos (Conflict + volumes órfãos). O wrapper faz
`cd "$PROJECT_DIR"` e o unit usa `WorkingDirectory` na instância pelo mesmo motivo.

Instalação (uma vez):

```bash
sudo install -d -o zabbix -g zabbix /opt/itgov-backup
git clone https://github.com/robertoandr/it-governance-dashboard.git /opt/itgov-backup
sudo cp /opt/itgov-backup/deploy/systemd/governanca-ti-coleta.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start governanca-ti-coleta.service   # teste manual; acompanhe com journalctl
```

Atualizar o código em produção (depois do merge no `main`):

```bash
git -C /opt/itgov-backup pull --ff-only
```
