#!/usr/bin/env bash
# Teste de restauração: prova que o backup VOLTA, não só que existe.
#
# Baixa do remoto (OneDrive) o arquivo mais recente de cada tipo e restaura
# cada um num ambiente descartável — nada toca a produção:
#   - zabbix_*.sql.gz        → Postgres 16 temporário (sem rede), conta hosts e
#                              a última coleta de histórico
#   - app_/govti_*.db.gz     → PRAGMA integrity_check + nº de tabelas
#   - influxdb_*.tar.gz      → InfluxDB 2.7 temporário, restaura o bucket
#                              governance_raw e conta pontos dos últimos 7 dias
#   - mapacameras_*.tar.gz   → cameras.json presente e JSON válido
#   - compartilhado_*.tar.gz → lista o conteúdo
#   - config_*.tar.gz.gpg    → decifra com a senha e confere que o .env está lá
#
# Uso: testar_restauracao.sh [--local]   (--local usa backups/ em vez do remoto)
# Sai com 0 só se todos os itens passarem. Resultado também em logs/restore_test.log.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
ENV_FILE="$PROJECT_ROOT/.env"
LOG_FILE="$PROJECT_ROOT/logs/restore_test.log"
BACKUP_PASSPHRASE_FILE="${BACKUP_PASSPHRASE_FILE:-$HOME/.config/itgov-backup/passphrase}"
ORIGEM="remoto"
[ "${1:-}" = "--local" ] && ORIGEM="local"

RCLONE_REMOTE="${RCLONE_REMOTE:-}"
if [ -z "$RCLONE_REMOTE" ] && [ -f "$ENV_FILE" ]; then
    RCLONE_REMOTE="$(grep -m1 '^RCLONE_REMOTE=' "$ENV_FILE" | cut -d '=' -f2-)"
fi

ID="$$"
PG="itgov-restore-pg-$ID"
IFX="itgov-restore-influx-$ID"
WORK="$(mktemp -d)"
mkdir -p "$(dirname "$LOG_FILE")"

limpar() {
    docker rm -f "$PG" "$IFX" >/dev/null 2>&1 || true
    rm -rf "$WORK"
}
trap limpar EXIT

FALHAS=0
RESUMO=()
registrar() {  # registrar OK|FALHA item detalhe
    RESUMO+=("$(printf '%-6s %-14s %s' "$1" "$2" "$3")")
    [ "$1" = "OK" ] || FALHAS=$((FALHAS + 1))
}
log() {
    echo "[$(date '+%F %T')] $*" | tee -a "$LOG_FILE"
}

# ── Escolhe o arquivo mais recente de cada tipo (nomes têm timestamp
#    ordenável) e traz para $WORK ──────────────────────────────────────
log "Teste de restauração — origem: $ORIGEM"
if [ "$ORIGEM" = "remoto" ]; then
    [ -n "$RCLONE_REMOTE" ] || { log "ERRO: RCLONE_REMOTE não configurado"; exit 1; }
    LISTA="$(rclone lsf --files-only "$RCLONE_REMOTE")" || { log "ERRO: não consegui listar $RCLONE_REMOTE"; exit 1; }
else
    LISTA="$(ls -1 "$PROJECT_ROOT/backups")"
fi

declare -A ARQ
for prefixo in zabbix_ app_ govti_ influxdb_ mapacameras_ compartilhado_ config_; do
    nome="$(grep "^${prefixo}" <<<"$LISTA" | sort | tail -n1)"
    if [ -z "$nome" ]; then
        registrar FALHA "${prefixo%_}" "nenhum arquivo encontrado na origem"
        continue
    fi
    if [ "$ORIGEM" = "remoto" ]; then
        rclone copyto "$RCLONE_REMOTE/$nome" "$WORK/$nome" || { registrar FALHA "${prefixo%_}" "download falhou: $nome"; continue; }
    else
        cp "$PROJECT_ROOT/backups/$nome" "$WORK/$nome"
    fi
    ARQ[${prefixo%_}]="$WORK/$nome"
done

# ── Postgres do Zabbix ───────────────────────────────────────────────────
if [ -n "${ARQ[zabbix]:-}" ]; then
    inicio=$SECONDS
    if gzip -t "${ARQ[zabbix]}" \
        && docker run -d --name "$PG" --network none -e POSTGRES_USER=zabbix -e POSTGRES_DB=zabbix \
            -e POSTGRES_PASSWORD="restore-$ID" postgres:16-alpine >/dev/null; then
        for _ in $(seq 60); do docker exec "$PG" pg_isready -U zabbix -q 2>/dev/null && break; sleep 2; done
        # O entrypoint reinicia o postgres uma vez após o initdb — espera o definitivo.
        sleep 3
        for _ in $(seq 30); do docker exec "$PG" pg_isready -U zabbix -q 2>/dev/null && break; sleep 2; done
        if gunzip -c "${ARQ[zabbix]}" | docker exec -i "$PG" psql -U zabbix -d zabbix -q -v ON_ERROR_STOP=1 >/dev/null 2>"$WORK/pg.err"; then
            hosts="$(docker exec "$PG" psql -U zabbix -d zabbix -tAc 'select count(*) from hosts')"
            ultima="$(docker exec "$PG" psql -U zabbix -d zabbix -tAc "select to_char(to_timestamp(max(clock)),'DD/MM/YYYY HH24:MI') from history")"
            registrar OK zabbix "$(basename "${ARQ[zabbix]}") → $hosts hosts, última coleta $ultima UTC ($((SECONDS - inicio)) s)"
        else
            registrar FALHA zabbix "psql: $(head -c 300 "$WORK/pg.err")"
        fi
    else
        registrar FALHA zabbix "arquivo corrompido ou Postgres temporário não subiu"
    fi
fi

# ── SQLite ───────────────────────────────────────────────────────────────
for db in app govti; do
    [ -n "${ARQ[$db]:-}" ] || continue
    if gunzip -c "${ARQ[$db]}" >"$WORK/$db.db" 2>/dev/null \
        && res="$(python3 -I -c 'import sqlite3,sys
c=sqlite3.connect(sys.argv[1]); ok=c.execute("pragma integrity_check").fetchone()[0]
n=c.execute("select count(*) from sqlite_master where type=\"table\"").fetchone()[0]
print(ok, n)' "$WORK/$db.db")" && [ "${res%% *}" = "ok" ]; then
        registrar OK "$db" "$(basename "${ARQ[$db]}") → integridade ok, ${res##* } tabelas"
    else
        registrar FALHA "$db" "integrity_check falhou: ${res:-arquivo ilegível}"
    fi
done

# ── InfluxDB ─────────────────────────────────────────────────────────────
if [ -n "${ARQ[influxdb]:-}" ]; then
    TOK="restore-$ID-$(date +%s)"
    mkdir -p "$WORK/influx"
    if tar xzf "${ARQ[influxdb]}" -C "$WORK/influx" \
        && docker run -d --name "$IFX" --network none \
            -e DOCKER_INFLUXDB_INIT_MODE=setup -e DOCKER_INFLUXDB_INIT_USERNAME=restore \
            -e DOCKER_INFLUXDB_INIT_PASSWORD="restore-$ID-senha" -e DOCKER_INFLUXDB_INIT_ORG=restore \
            -e DOCKER_INFLUXDB_INIT_BUCKET=vazio -e DOCKER_INFLUXDB_INIT_ADMIN_TOKEN="$TOK" \
            influxdb:2.7-alpine >/dev/null; then
        for _ in $(seq 60); do
            docker exec "$IFX" influx bucket list -t "$TOK" -o restore >/dev/null 2>&1 && break
            sleep 2
        done
        dir="$WORK/influx/$(ls "$WORK/influx")"
        # A org de origem (a de produção) vem do manifesto do próprio backup:
        # sem ela o restore "funciona" (rc 0) mas não restaura nada.
        org="$(python3 -I -c 'import glob,json,sys
m=json.load(open(glob.glob(sys.argv[1]+"/*.manifest")[0]))
print(next(b["organizationName"] for b in m["buckets"] if b["bucketName"]=="governance_raw"))' "$dir" 2>/dev/null)"
        docker cp "$dir" "$IFX:/tmp/restaurar" >/dev/null
        if [ -n "$org" ] && docker exec "$IFX" influx restore /tmp/restaurar --org "$org" --bucket governance_raw \
            --new-org restore --new-bucket restaurado -t "$TOK" >/dev/null 2>"$WORK/ifx.err"; then
            # count por série e soma: um group() antes do count mistura campos
            # de tipos diferentes e a consulta falha.
            pontos="$(docker exec "$IFX" influx query -t "$TOK" -o restore --raw \
                'from(bucket:"restaurado") |> range(start:-7d) |> count() |> group() |> sum()' 2>/dev/null \
                | grep -vE '^#|^,result|^\s*$' | tail -n1 | awk -F, '{print $NF}' | tr -d '\r')"
            if [ -n "$pontos" ] && [ "$pontos" -gt 0 ] 2>/dev/null; then
                registrar OK influxdb "$(basename "${ARQ[influxdb]}") → governance_raw restaurado, $pontos pontos nos últimos 7 dias"
            else
                registrar FALHA influxdb "bucket restaurado mas sem pontos nos últimos 7 dias"
            fi
        else
            registrar FALHA influxdb "influx restore: $(head -c 300 "$WORK/ifx.err")"
        fi
    else
        registrar FALHA influxdb "arquivo corrompido ou InfluxDB temporário não subiu"
    fi
fi

# ── MapaCameras ──────────────────────────────────────────────────────────
if [ -n "${ARQ[mapacameras]:-}" ]; then
    mkdir -p "$WORK/mapa"
    if tar xzf "${ARQ[mapacameras]}" -C "$WORK/mapa" 2>/dev/null \
        && python3 -I -c 'import json,sys; json.load(open(sys.argv[1]))' "$WORK/mapa/cameras.json" 2>/dev/null; then
        registrar OK mapacameras "$(basename "${ARQ[mapacameras]}") → cameras.json válido, $(find "$WORK/mapa" -type f | wc -l) arquivos"
    else
        registrar FALHA mapacameras "tar ilegível ou cameras.json ausente/inválido"
    fi
fi

# ── Pasta compartilhada ──────────────────────────────────────────────────
if [ -n "${ARQ[compartilhado]:-}" ]; then
    if n="$(tar tzf "${ARQ[compartilhado]}" 2>/dev/null | grep -vc '/$')"; then
        registrar OK compartilhado "$(basename "${ARQ[compartilhado]}") → $n arquivos"
    else
        registrar FALHA compartilhado "tar ilegível"
    fi
fi

# ── Pacote de configuração ───────────────────────────────────────────────
if [ -n "${ARQ[config]:-}" ]; then
    if [ ! -r "$BACKUP_PASSPHRASE_FILE" ]; then
        registrar FALHA config "senha ausente em $BACKUP_PASSPHRASE_FILE"
    elif conteudo="$(gpg --batch --quiet --pinentry-mode loopback --passphrase-file "$BACKUP_PASSPHRASE_FILE" \
            -d "${ARQ[config]}" 2>/dev/null | tar tzf - 2>/dev/null)" && grep -q '\.env$' <<<"$conteudo"; then
        registrar OK config "$(basename "${ARQ[config]}") → decifrado, $(grep -vc '/$' <<<"$conteudo") arquivos, .env presente"
    else
        registrar FALHA config "não decifrou com a senha ou .env ausente"
    fi
fi

log "Resultado:"
for linha in "${RESUMO[@]}"; do log "  $linha"; done
if [ "$FALHAS" -eq 0 ]; then
    log "TESTE DE RESTAURAÇÃO: OK"
    exit 0
fi
log "TESTE DE RESTAURAÇÃO: $FALHAS item(ns) com FALHA"
exit 1
