#!/usr/bin/env bash
# Backup diário dos 3 armazenamentos reais do sistema (nenhum deles é git):
#   - zabbix_db  (Postgres do Zabbix, container zabbix_db)
#   - app.db     (SQLite — usuários/auth, dentro do volume da app)
#   - govti.db   (SQLite — vendors/contracts/assets/governança, mesmo volume)
# Cada dump vai comprimido pra backups/, com retenção local, e então
# sync_cloud.sh sobe tudo pro OneDrive. Uma falha aqui é grave (perdemos o
# dump do dia), então -e; a falha do sync externo é tratada à parte por
# sync_cloud.sh, que nunca deixa a etapa de retenção de rodar.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="$PROJECT_ROOT/backups"
ENV_FILE="$PROJECT_ROOT/.env"
RETENTION_DAYS=7
TIMESTAMP="$(date +%Y-%m-%d_%H%M%S)"

APP_CONTAINER="itgov-app"
ZABBIX_DB_CONTAINER="zabbix_db"
ZABBIX_DB_NAME="zabbix"
ZABBIX_DB_USER="zabbix"

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

# ── 3. Retenção local ─────────────────────────────────────────────────────
log "Removendo backups com mais de $RETENTION_DAYS dias..."
find "$BACKUP_DIR" \( -name 'zabbix_*.sql.gz' -o -name 'app_*.db.gz' -o -name 'govti_*.db.gz' \) \
    -type f -mtime "+$RETENTION_DAYS" -print -delete

# ── 4. Sync externo (OneDrive via rclone) — falha aqui não é fatal para
#      este script; sync_cloud.sh já loga e retorna código próprio ────────
log "Sincronizando com armazenamento externo..."
BACKUP_FILE_PATTERN="*.gz" "$PROJECT_ROOT/scripts/sync_cloud.sh" || log "AVISO: sync externo falhou — ver logs/backup_external.log"

log "Backup diário concluído."
