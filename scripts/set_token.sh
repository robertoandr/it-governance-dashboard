#!/usr/bin/env bash
# set_token.sh — Insere/atualiza tokens no .env de forma segura
set -euo pipefail

ENV_FILE="/opt/it-gov-dashboard/.env"
KEY="${1:-}"

if [[ -z "${KEY}" ]]; then
  echo "Uso: $0 <NOME_DA_VARIAVEL>"
  echo "Exemplos: $0 GRAFANA_TOKEN | $0 INFLUX_TOKEN | $0 ZABBIX_TOKEN"
  exit 1
fi

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Arquivo nao encontrado: ${ENV_FILE}"
  exit 1
fi

if ! [[ "${KEY}" =~ ^[A-Z_][A-Z0-9_]*$ ]]; then
  echo "Nome de variavel invalido: ${KEY}"
  exit 1
fi

# Captura dono/grupo originais ANTES de qualquer modificacao
ORIG_OWNER="$(stat -c '%U:%G' "${ENV_FILE}")"
ORIG_MODE="$(stat -c '%a' "${ENV_FILE}")"

echo "Inserindo valor para: ${KEY}"
echo "(o que voce colar NAO sera exibido)"
echo -n "Cole o token e pressione ENTER: "
read -rs TOKEN
echo ""
echo -n "Confirme colando novamente:     "
read -rs TOKEN2
echo ""

if [[ -z "${TOKEN}" ]]; then
  echo "Token vazio. Abortado."
  exit 1
fi

if [[ "${TOKEN}" != "${TOKEN2}" ]]; then
  echo "Os valores nao conferem. Abortado."
  exit 1
fi

BACKUP="${ENV_FILE}.bak.$(date +%Y%m%d-%H%M%S)"
sudo cp -p "${ENV_FILE}" "${BACKUP}"
echo "Backup criado: ${BACKUP}"

export KEY TOKEN
if sudo grep -q "^${KEY}=" "${ENV_FILE}"; then
  sudo awk -v k="${KEY}" -v v="${TOKEN}" '
    BEGIN{FS=OFS="="}
    $1==k { print k"="v; next }
    { print }
  ' "${ENV_FILE}" | sudo tee "${ENV_FILE}.tmp" > /dev/null
  sudo mv "${ENV_FILE}.tmp" "${ENV_FILE}"
  echo "Variavel ${KEY} atualizada."
else
  echo "${KEY}=${TOKEN}" | sudo tee -a "${ENV_FILE}" > /dev/null
  echo "Variavel ${KEY} adicionada."
fi
unset TOKEN TOKEN2

# RESTAURA dono/permissoes originais
sudo chown "${ORIG_OWNER}" "${ENV_FILE}"
sudo chmod "${ORIG_MODE}" "${ENV_FILE}"

# Garante minimo de 600
CURRENT_MODE="$(stat -c '%a' "${ENV_FILE}")"
if [[ "${CURRENT_MODE}" != "600" ]]; then
  sudo chmod 600 "${ENV_FILE}"
fi

LEN=$(sudo grep "^${KEY}=" "${ENV_FILE}" | cut -d= -f2- | tr -d '\n' | wc -c)
PREFIX=$(sudo grep "^${KEY}=" "${ENV_FILE}" | cut -d= -f2- | cut -c1-6)
SUFFIX=$(sudo grep "^${KEY}=" "${ENV_FILE}" | cut -d= -f2- | tr -d '\n' | rev | cut -c1-4 | rev)

echo ""
echo "Concluido!"
echo "${KEY} = ${PREFIX}...${SUFFIX}  (${LEN} chars)"
echo "Dono/Permissoes: $(stat -c '%U:%G %a' "${ENV_FILE}")"
