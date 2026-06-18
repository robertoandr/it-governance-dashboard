#!/usr/bin/env bash
# Instala/atualiza o serviço it-gov-dashboard.service no host
#
# Uso:
#   sudo bash install.sh              — instala/atualiza
#   bash install.sh --dry-run         — substitui variáveis + verifica, sem instalar
#
# Variáveis de ambiente (opcional, têm defaults):
#   SERVICE_USER, WORKDIR, VENV_PATH, ENV_FILE, LOG_DIR

set -euo pipefail

TEMPLATE_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_NAME="it-gov-dashboard"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
BACKUP_FILE="${SERVICE_FILE}.bak-$(date +%Y%m%d-%H%M%S)"
DRY_RUN=false

[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

# Valores padrão — ajustar conforme ambiente
SERVICE_USER="${SERVICE_USER:-zabbix}"
WORKDIR="${WORKDIR:-/opt/it-gov-dashboard}"
VENV_PATH="${VENV_PATH:-/opt/it-gov-dashboard/.venv}"
ENV_FILE="${ENV_FILE:-/opt/it-gov-dashboard/.env}"
LOG_DIR="${LOG_DIR:-/var/log/it-gov-dashboard}"

if $DRY_RUN; then
    echo "[DRY-RUN] Variáveis que serão aplicadas:"
    echo "  SERVICE_USER = $SERVICE_USER"
    echo "  WORKDIR      = $WORKDIR"
    echo "  VENV_PATH    = $VENV_PATH"
    echo "  ENV_FILE     = $ENV_FILE"
    echo "  LOG_DIR      = $LOG_DIR"
    echo "  LOG_DIR      = $LOG_DIR"
    echo ""
fi

echo "[1/5] Backup do service atual..."
if [[ -f "$SERVICE_FILE" ]] && ! $DRY_RUN; then
    cp "$SERVICE_FILE" "$BACKUP_FILE"
    echo "      Backup: $BACKUP_FILE"
elif $DRY_RUN; then
    echo "      [DRY-RUN] Pulando backup."
fi

echo "[2/5] Gerando service file a partir do template..."
sed -e "s|{{ SERVICE_USER }}|$SERVICE_USER|g" \
    -e "s|{{ SERVICE_GROUP }}|$SERVICE_USER|g" \
    -e "s|{{ WORKDIR }}|$WORKDIR|g" \
    -e "s|{{ VENV_PATH }}|$VENV_PATH|g" \
    -e "s|{{ ENV_FILE }}|$ENV_FILE|g" \
    -e "s|{{ LOG_DIR }}|$LOG_DIR|g" \
    "${TEMPLATE_DIR}/it-gov-dashboard.service.template" > /tmp/it-gov-dashboard.service

echo "      Gerado em /tmp/it-gov-dashboard.service"
if $DRY_RUN; then
    echo "      Conteúdo:"
    cat /tmp/it-gov-dashboard.service
    echo ""
fi

echo "[3/5] Validando com systemd-analyze verify..."
systemd-analyze verify /tmp/it-gov-dashboard.service || {
    echo "ERRO: systemd-analyze verify falhou. Abortando."
    exit 1
}
echo "      OK — sem erros de sintaxe."

if $DRY_RUN; then
    echo ""
    echo "[DRY-RUN] Validação concluída. Sem alterações no sistema."
    echo "Para instalar: sudo bash install.sh"
    exit 0
fi

echo "[4/5] Instalando e recarregando systemd..."
cp /tmp/it-gov-dashboard.service "$SERVICE_FILE"
systemctl daemon-reload
systemctl restart "$SERVICE_NAME"

echo "[5/5] Verificando status..."
sleep 2
systemctl is-active "$SERVICE_NAME" || {
    echo "ERRO: serviço não ficou ativo."
    journalctl -u "$SERVICE_NAME" -n 20 --no-pager
    exit 1
}

echo ""
echo "OK — $SERVICE_NAME instalado e ativo."
echo "Watchdog:     $(systemctl show $SERVICE_NAME -p WatchdogUSec | cut -d= -f2)"
echo "RestartBurst: $(systemctl show $SERVICE_NAME -p StartLimitBurst | cut -d= -f2)"
echo "MemoryMax:    $(systemctl show $SERVICE_NAME -p MemoryMax | cut -d= -f2)"
echo "CPUQuota:     $(systemctl show $SERVICE_NAME -p CPUQuota | cut -d= -f2)"
echo ""
echo "Valide: curl -s http://127.0.0.1:8082/health"
