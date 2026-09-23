#!/usr/bin/env bash
# Sincroniza backups locais com um remote rclone (Google Drive, OneDrive, S3, etc).
# Genérico: não depende do tipo de banco nem do nome do serviço, só do diretório de
# backups e do padrão de arquivo.
#
# Não usa `set -e`: cada falha é registrada em logs/backup_external.log e o script
# sai com status != 0, mas sem estourar um traceback de shell — quem chama (ex.:
# backup_governanca.sh) decide o que fazer com o resultado, e o backup local nunca
# fica refém do sync externo.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# PROJECT_ROOT = onde ficam .env, backups/ e logs/ (dados da instância). Por
# padrão é o checkout que contém este script; o systemd sobrescreve para
# rodar o código de um checkout de operação separado (ver deploy/README.md).
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
BACKUP_DIR="$PROJECT_ROOT/backups"
LOG_DIR="$PROJECT_ROOT/logs"
LOG_FILE="$LOG_DIR/backup_external.log"
ENV_FILE="$PROJECT_ROOT/.env"

# Este projeto guarda 3 tipos de dump (zabbix_*.sql.gz, app_*.db.gz, govti_*.db.gz)
BACKUP_FILE_PATTERN="${BACKUP_FILE_PATTERN:-*.gz}"

mkdir -p "$LOG_DIR"

log() {
    local status="$1"
    shift
    echo "$(date '+%F %T') [$status] $*" >>"$LOG_FILE"
}

# RCLONE_REMOTE vem do ambiente ou do .env, ex: RCLONE_REMOTE=gov-onedrive:Backups/172.29.2.11
RCLONE_REMOTE="${RCLONE_REMOTE:-}"
if [ -z "$RCLONE_REMOTE" ] && [ -f "$ENV_FILE" ]; then
    RCLONE_REMOTE="$(grep -m1 '^RCLONE_REMOTE=' "$ENV_FILE" | cut -d '=' -f2-)"
fi

if ! command -v rclone >/dev/null 2>&1; then
    log "ERRO" "rclone não está instalado — instale com 'curl https://rclone.org/install.sh | sudo bash'."
    exit 1
fi

if [ -z "$RCLONE_REMOTE" ]; then
    log "ERRO" "RCLONE_REMOTE não configurado (defina em .env, ex: RCLONE_REMOTE=gov-onedrive:Backups/172.29.2.11)."
    exit 1
fi

LATEST_BACKUP="$(ls -t "$BACKUP_DIR"/$BACKUP_FILE_PATTERN 2>/dev/null | head -n1)"
if [ -z "$LATEST_BACKUP" ]; then
    log "ERRO" "Nenhum backup ($BACKUP_FILE_PATTERN) encontrado em $BACKUP_DIR; upload cancelado."
    exit 1
fi

if rclone copy "$BACKUP_DIR" "$RCLONE_REMOTE" --include "$BACKUP_FILE_PATTERN" >>"$LOG_FILE" 2>&1; then
    log "SUCESSO" "$(basename "$LATEST_BACKUP") e demais backups locais sincronizados para $RCLONE_REMOTE"
    exit 0
else
    log "ERRO" "rclone copy falhou ao sincronizar com $RCLONE_REMOTE"
    exit 1
fi
