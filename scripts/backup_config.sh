#!/usr/bin/env bash
# Pacote de configuração do servidor, CRIPTOGRAFADO (gpg simétrico AES-256)
# antes de sair da máquina: .env com senhas, certificados TLS, chave da conta
# ACME, configs do Evolution e do OmniRoute, units do systemd, binário do
# MapaCameras e o crontab. Sem isso, perder a VM significa recriar à mão
# todas as credenciais e serviços — os dumps de banco sozinhos não bastam.
#
# Uso: backup_config.sh <arquivo-de-saida.tar.gz.gpg>
#
# A senha vem de BACKUP_PASSPHRASE_FILE (padrão ~/.config/itgov-backup/passphrase,
# modo 600). Sem ela o script NÃO gera nada — nunca sobe o pacote em claro.
# Guarde uma cópia da senha fora do servidor (cofre de senhas): sem ela o
# pacote não abre, e se a VM for perdida a cópia local vai junto.
#
# Caminhos ausentes ou sem permissão de leitura são registrados e pulados.
set -euo pipefail

OUT="${1:?uso: backup_config.sh <saida.tar.gz.gpg>}"
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
BACKUP_PASSPHRASE_FILE="${BACKUP_PASSPHRASE_FILE:-$HOME/.config/itgov-backup/passphrase}"

# Um caminho por linha. Sobrescrevível por ambiente (testes, outra instância).
DEFAULT_CONFIG_PATHS="$PROJECT_ROOT/.env
$PROJECT_ROOT/docker-compose.override.yml
$PROJECT_ROOT/docker/nginx/certs
$HOME/.acme.sh
$HOME/.config/rclone/rclone.conf
$HOME/evolution/.env
$HOME/evolution/docker-compose.yml
$HOME/evolution/evo.sh
$HOME/omniroute/run.sh
$HOME/omniroute/run-proxy.sh
$HOME/omniroute/backup.sh
$HOME/omniroute/client.key
$HOME/omniroute/proxy
/etc/systemd/system/governanca-ti-coleta.service
/etc/systemd/system/governanca-ti-coleta.timer
/etc/systemd/system/mapa-cameras.service
/etc/systemd/system/mapa-cameras.service.d
/opt/mapa-cameras"
CONFIG_PATHS="${CONFIG_PATHS:-$DEFAULT_CONFIG_PATHS}"
# Último backup do banco do OmniRoute (o próprio OmniRoute gera um por dia).
OMNIROUTE_BACKUP_DIR="${OMNIROUTE_BACKUP_DIR:-$HOME/omniroute/backups}"

log() {
    echo "[$(date '+%F %T')] $*"
}

if [ ! -r "$BACKUP_PASSPHRASE_FILE" ] || [ ! -s "$BACKUP_PASSPHRASE_FILE" ]; then
    log "AVISO: senha do pacote ausente em $BACKUP_PASSPHRASE_FILE — pacote de configuração NÃO gerado."
    exit 2
fi

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

# Lista de inclusão para o tar, relativa a / (o tar recusa caminhos absolutos
# sem -P e restaurar com -C / devolve cada arquivo ao lugar original).
LIST="$STAGE/lista"
: >"$LIST"
while IFS= read -r p; do
    [ -z "$p" ] && continue
    if [ -e "$p" ] && [ -r "$p" ]; then
        echo "${p#/}" >>"$LIST"
    else
        log "Config: pulando $p (ausente ou sem permissão)"
    fi
done <<<"$CONFIG_PATHS"

ULTIMO_OMNI="$(ls -t "$OMNIROUTE_BACKUP_DIR"/*.sqlite.gz 2>/dev/null | head -n1 || true)"
if [ -n "$ULTIMO_OMNI" ]; then
    echo "${ULTIMO_OMNI#/}" >>"$LIST"
fi

# crontab não é arquivo legível diretamente — vai como texto no pacote.
mkdir -p "$STAGE/extra"
crontab -l >"$STAGE/extra/crontab.txt" 2>/dev/null || echo "# sem crontab" >"$STAGE/extra/crontab.txt"

if [ ! -s "$LIST" ]; then
    log "ERRO: nenhum caminho de configuração legível — nada a empacotar."
    exit 1
fi

umask 077
set +e
tar czf - --ignore-failed-read -C / -T "$LIST" -C "$STAGE" extra 2>"$STAGE/tar.err" \
    | gpg --batch --yes --quiet --pinentry-mode loopback --symmetric --cipher-algo AES256 \
        --passphrase-file "$BACKUP_PASSPHRASE_FILE" -o "$OUT"
status=("${PIPESTATUS[@]}")
set -e
# tar sai com 1 quando um arquivo muda durante a leitura (logs, sqlite do
# OmniRoute): o pacote é válido. 2+ é erro de verdade.
if [ "${status[0]}" -gt 1 ] || [ "${status[1]}" -ne 0 ]; then
    rm -f "$OUT"
    log "ERRO: falha ao gerar o pacote (tar=${status[0]}, gpg=${status[1]}): $(tr '\n' ' ' <"$STAGE/tar.err")"
    exit 1
fi
if [ -s "$STAGE/tar.err" ]; then
    log "Config: avisos do tar: $(tr '\n' ' ' <"$STAGE/tar.err")"
fi
log "Config: $(wc -l <"$LIST") caminhos empacotados e criptografados em $OUT ($(du -h "$OUT" | cut -f1))"
