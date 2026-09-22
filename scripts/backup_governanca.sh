#!/usr/bin/env bash
# Backup diário dos armazenamentos reais do servidor (nenhum deles é git):
#   - zabbix_db  (Postgres do Zabbix, container zabbix_db)
#   - app.db     (SQLite — usuários/auth, dentro do volume da app)
#   - govti.db   (SQLite — vendors/contracts/assets/governança, mesmo volume)
#   - MapaCameras (/var/lib/mapa-cameras — serviço systemd à parte, não é
#     container: cameras.json, fotos, plantas e usuários do mapa)
# Cada dump vai comprimido pra backups/, com retenção local, e então
# sync_cloud.sh sobe tudo pro OneDrive. Uma falha aqui é grave (perdemos o
# dump do dia), então -e; a falha do sync externo é tratada à parte por
# sync_cloud.sh, que nunca deixa a etapa de retenção de rodar.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# PROJECT_ROOT = onde ficam .env, backups/ e logs/ (dados da instância). Por
# padrão é o checkout que contém este script; o systemd sobrescreve para
# rodar o código de um checkout de operação separado (ver deploy/README.md).
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
BACKUP_DIR="$PROJECT_ROOT/backups"
ENV_FILE="$PROJECT_ROOT/.env"
RETENTION_DAYS=7
TIMESTAMP="$(date +%Y-%m-%d_%H%M%S)"

APP_CONTAINER="itgov-app"
ZABBIX_DB_CONTAINER="zabbix_db"
ZABBIX_DB_NAME="zabbix"
ZABBIX_DB_USER="zabbix"

# Pasta de dados do MapaCameras. O serviço roda como "mapacameras" com
# UMask=0027, então basta o usuário que roda este backup estar no grupo
# mapacameras (sudo gpasswd -a zabbix mapacameras) para conseguir ler.
MAPA_CAMERAS_DIR="${MAPA_CAMERAS_DIR:-/var/lib/mapa-cameras}"

log() {
    echo "[$(date '+%F %T')] $*"
}

mkdir -p "$BACKUP_DIR"

# ── Pré-condições ────────────────────────────────────────────────────────
for c in "$APP_CONTAINER" "$ZABBIX_DB_CONTAINER"; do
    if ! docker ps --format '{{.Names}}' | grep -qx "$c"; then
        log "ERRO: container '$c' não está em execução — abortando."
        exit 1
    fi
done

# ── 1. Postgres do Zabbix ────────────────────────────────────────────────
ZABBIX_DB_PASSWORD="zabbix"
if [ -f "$ENV_FILE" ]; then
    ENV_PASSWORD="$(grep -m1 '^ZABBIX_DB_PASSWORD=' "$ENV_FILE" | cut -d '=' -f2- || true)"
    if [ -n "$ENV_PASSWORD" ]; then
        ZABBIX_DB_PASSWORD="$ENV_PASSWORD"
    fi
fi

ZABBIX_BACKUP_FILE="$BACKUP_DIR/zabbix_${TIMESTAMP}.sql.gz"
log "Backup Postgres (zabbix): iniciando..."
docker exec -e PGPASSWORD="$ZABBIX_DB_PASSWORD" "$ZABBIX_DB_CONTAINER" \
    pg_dump -U "$ZABBIX_DB_USER" -d "$ZABBIX_DB_NAME" | gzip >"$ZABBIX_BACKUP_FILE"
log "Backup Postgres (zabbix): salvo em $ZABBIX_BACKUP_FILE ($(du -h "$ZABBIX_BACKUP_FILE" | cut -f1))"

# ── 2. SQLite (app.db + govti.db) via sqlite3.backup() — consistente mesmo
#      com o app rodando (não é uma cópia bruta do arquivo, que arriscaria
#      pegar um WAL a meio de escrita) ────────────────────────────────────
for db in app govti; do
    # /app/tmp é um volume real (ext4); /tmp do container é tmpfs e "docker cp"
    # não consegue ler arquivos de lá (limitação conhecida do Docker).
    OUT="/app/tmp/${db}_${TIMESTAMP}.db"
    log "Backup SQLite ($db.db): iniciando..."
    docker exec "$APP_CONTAINER" python3 -c "
import sqlite3
src = sqlite3.connect('/app/data/${db}.db')
dst = sqlite3.connect('${OUT}')
src.backup(dst)
dst.close()
src.close()
"
    docker cp "$APP_CONTAINER:$OUT" "$BACKUP_DIR/${db}_${TIMESTAMP}.db"
    docker exec "$APP_CONTAINER" rm -f "$OUT"
    gzip "$BACKUP_DIR/${db}_${TIMESTAMP}.db"
    log "Backup SQLite ($db.db): salvo em $BACKUP_DIR/${db}_${TIMESTAMP}.db.gz"
done

# ── 3. MapaCameras — tar da pasta de dados inteira. Não é fatal: se o
#      serviço não estiver instalado ou faltar permissão, loga e segue (os
#      dumps acima já foram gerados e ainda precisam de retenção + sync).
#      backups/ interno do app (cópias pré-atualização) fica de fora — é
#      redundante com este dump e só cresce. ─────────────────────────────
if [ ! -d "$MAPA_CAMERAS_DIR" ]; then
    log "Backup MapaCameras: $MAPA_CAMERAS_DIR não existe — pulando."
elif [ ! -r "$MAPA_CAMERAS_DIR" ] || [ ! -x "$MAPA_CAMERAS_DIR" ]; then
    log "AVISO: sem permissão para ler $MAPA_CAMERAS_DIR — pulando MapaCameras (rode: sudo gpasswd -a $(id -un) mapacameras)."
else
    MAPA_BACKUP_FILE="$BACKUP_DIR/mapacameras_${TIMESTAMP}.tar.gz"
    log "Backup MapaCameras: iniciando..."
    if tar czf "$MAPA_BACKUP_FILE" --exclude='./backups' -C "$MAPA_CAMERAS_DIR" .; then
        log "Backup MapaCameras: salvo em $MAPA_BACKUP_FILE ($(du -h "$MAPA_BACKUP_FILE" | cut -f1))"
    else
        rm -f "$MAPA_BACKUP_FILE"
        log "AVISO: Backup MapaCameras falhou — seguindo com os demais."
    fi
fi

# ── 4. Retenção local ─────────────────────────────────────────────────────
log "Removendo backups com mais de $RETENTION_DAYS dias..."
find "$BACKUP_DIR" \( -name 'zabbix_*.sql.gz' -o -name 'app_*.db.gz' -o -name 'govti_*.db.gz' \
    -o -name 'mapacameras_*.tar.gz' \) \
    -type f -mtime "+$RETENTION_DAYS" -print -delete

# ── 5. Sync externo (OneDrive via rclone) — falha aqui não é fatal para
#      este script; sync_cloud.sh já loga e retorna código próprio ────────
log "Sincronizando com armazenamento externo..."
BACKUP_FILE_PATTERN="*.gz" "$SCRIPT_DIR/sync_cloud.sh" || log "AVISO: sync externo falhou — ver logs/backup_external.log"

log "Backup diário concluído."
