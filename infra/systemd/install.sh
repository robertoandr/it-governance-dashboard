#!/usr/bin/env bash
# Instala/atualiza o serviço dashboard-ti.service no host
#
# Uso:
#   sudo bash install.sh              — instala/atualiza
#   bash install.sh --dry-run         — substitui variáveis + verifica, sem instalar
#
# Variáveis de ambiente (opcional, têm defaults):
#   SERVICE_USER, WORKDIR, VENV_PATH, ENV_FILE, LOG_DIR, DATA_DIR

set -euo pipefail

TEMPLATE_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_NAME="dashboard-ti"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
BACKUP_FILE="${SERVICE_FILE}.bak-$(date +%Y%m%d-%H%M%S)"
DRY_RUN=false

[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

# Valores padrão — ajustar conforme ambiente
SERVICE_USER="${SERVICE_USER:-dashboard-ti}"
WORKDIR="${WORKDIR:-/opt/dashboard-ti-legacy}"
VENV_PATH="${VENV_PATH:-/opt/dashboard-ti-legacy/venv}"
ENV_FILE="${ENV_FILE:-/etc/dashboard-ti/secrets.env}"
LOG_DIR="${LOG_DIR:-/var/log/dashboard-ti}"
DATA_DIR="${DATA_DIR:-/opt/dashboard-ti-legacy/data}"

if $DRY_RUN; then
    echo "[DRY-RUN] Variáveis que serão aplicadas:"
    echo "  SERVICE_USER = $SERVICE_USER"
    echo "  WORKDIR      = $WORKDIR"
    echo "  VENV_PATH    = $VENV_PATH"
    echo "  ENV_FILE     = $ENV_FILE"
    echo "  LOG_DIR      = $LOG_DIR"
    echo "  DATA_DIR     = $DATA_DIR"
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
    -e "s|{{ DATA_DIR }}|$DATA_DIR|g" \
    "${TEMPLATE_DIR}/dashboard-ti.service.template" > /tmp/dashboard-ti.service

echo "      Gerado em /tmp/dashboard-ti.service"
if $DRY_RUN; then
    echo "      Conteúdo:"
    cat /tmp/dashboard-ti.service
    echo ""
fi

echo "[3/5] Validando com systemd-analyze verify..."
systemd-analyze verify /tmp/dashboard-ti.service || {
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
cp /tmp/dashboard-ti.service "$SERVICE_FILE"
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
