#!/usr/bin/env bash
# set_token_safe.sh - Grava/atualiza variavel sensivel no .env de forma segura
# Uso: set_token_safe.sh <NOME_VAR> <ARQUIVO_COM_VALOR>

set -euo pipefail

ENV_FILE="${ENV_FILE:-/opt/it-gov-dashboard/.env}"
VAR_NAME="${1:-}"
VALUE_FILE="${2:-}"

# --- Validacoes ---
if [[ -z "$VAR_NAME" || -z "$VALUE_FILE" ]]; then
  echo "Uso: $0 <NOME_VAR> <ARQUIVO_COM_VALOR>" >&2
  exit 1
fi

if [[ ! "$VAR_NAME" =~ ^[A-Z_][A-Z0-9_]*$ ]]; then
  echo "ERRO: nome de variavel invalido: $VAR_NAME" >&2
  exit 1
fi

if [[ ! -r "$VALUE_FILE" ]]; then
  echo "ERRO: arquivo nao legivel: $VALUE_FILE" >&2
  exit 1
fi

# --- Le valor removendo \r \n finais ---
VALUE="$(tr -d '\r\n' < "$VALUE_FILE")"

if [[ -z "$VALUE" ]]; then
  echo "ERRO: valor vazio em $VALUE_FILE" >&2
  exit 1
fi

# --- Garante que .env existe ---
if [[ ! -f "$ENV_FILE" ]]; then
  sudo touch "$ENV_FILE"
  sudo chmod 600 "$ENV_FILE"
fi

# --- Backup com timestamp ---
TS="$(date +%Y%m%d_%H%M%S)"
BACKUP="${ENV_FILE}.bak.${TS}"
sudo cp -a "$ENV_FILE" "$BACKUP"
sudo chmod 600 "$BACKUP"

# --- Atualiza ou insere a linha (sem expor valor em ps/comando) ---
TMP="$(mktemp)"
chmod 600 "$TMP"

if sudo grep -qE "^${VAR_NAME}=" "$ENV_FILE"; then
  # Substitui linha existente
  sudo grep -vE "^${VAR_NAME}=" "$ENV_FILE" > "$TMP"
  printf '%s=%s\n' "$VAR_NAME" "$VALUE" >> "$TMP"
  ACTION="atualizado"
else
  # Adiciona nova linha
  sudo cat "$ENV_FILE" > "$TMP"
  printf '%s=%s\n' "$VAR_NAME" "$VALUE" >> "$TMP"
  ACTION="adicionado"
fi

sudo cp "$TMP" "$ENV_FILE"
sudo chmod 600 "$ENV_FILE"
shred -u "$TMP" 2>/dev/null || rm -f "$TMP"

# --- Relatorio (sem expor valor) ---
LEN=${#VALUE}
HEAD6="${VALUE:0:6}"
TAIL6="${VALUE: -6}"

echo "[OK] ${VAR_NAME} ${ACTION} em ${ENV_FILE}"
echo "     Tamanho: ${LEN} chars"
echo "     Inicio:  ${HEAD6}"
echo "     Fim:     ${TAIL6}"
echo "     Backup:  ${BACKUP}"
