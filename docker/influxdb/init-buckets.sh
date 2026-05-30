#!/bin/sh
# Bootstrap idempotente: cria buckets adicionais e Flux tasks.
# Executado pelo serviço influxdb-init após healthcheck OK.
set -eu

INFLUX_HOST="${INFLUX_HOST:-http://influxdb:8086}"
INFLUX_ORG="${INFLUX_ORG:-grupogadens}"

# Wrapper com flags comuns
influx_cmd() {
    influx --host "$INFLUX_HOST" --token "$INFLUX_TOKEN" "$@"
}

wait_ready() {
    echo "[init] Aguardando InfluxDB..."
    i=0
    while [ "$i" -lt 30 ]; do
        if influx_cmd ping > /dev/null 2>&1; then
            echo "[init] InfluxDB pronto."
            return 0
        fi
        i=$((i + 1))
        echo "[init]   tentativa $i/30..."
        sleep 2
    done
    echo "[init] ERRO: InfluxDB não respondeu após 60s."
    exit 1
}

# governance_raw é criado pelo DOCKER_INFLUXDB_INIT_MODE com 2160h.
# Este script cria os dois buckets restantes.
create_bucket() {
    name="$1"
    retention="$2"

    if influx_cmd bucket list \
            --org "$INFLUX_ORG" \
            --name "$name" \
            --hide-headers > /dev/null 2>&1; then
        echo "[init]   bucket '$name' já existe, pulando."
    else
        influx_cmd bucket create \
            --name "$name" \
            --retention "$retention" \
            --org "$INFLUX_ORG"
        echo "[init]   bucket '$name' criado (retention: $retention)."
    fi
}

create_task() {
    task_file="$1"
    task_name="$2"

    if influx_cmd task list --org "$INFLUX_ORG" 2>/dev/null | grep -q "$task_name"; then
        echo "[init]   task '$task_name' já existe, pulando."
    else
        influx_cmd task create \
            --file "$task_file" \
            --org "$INFLUX_ORG"
        echo "[init]   task '$task_name' criada."
    fi
}

main() {
    wait_ready

    echo "[init] === Criando buckets ==="
    create_bucket "governance_hourly"  "8760h"
    create_bucket "governance_monthly" "0"

    echo "[init] === Criando Flux tasks ==="
    create_task "/scripts/tasks/downsample_hourly.flux"  "governance_downsample_hourly"
    create_task "/scripts/tasks/downsample_monthly.flux" "governance_downsample_monthly"

    echo "[init] === Bootstrap concluído ==="
}

main
