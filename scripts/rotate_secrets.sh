#!/usr/bin/env bash
# rotate_secrets.sh - Rotação interativa dos secrets do Grupo 2 (dependem de
# portal externo para gerar o novo valor). Nunca ecoa o valor digitado.
#
# Uso: scripts/rotate_secrets.sh [--only NOME_VAR]
#
# Para cada secret, o script mostra onde gerar o novo valor, pede a entrada
# via `read -rs` (sem eco no terminal), faz backup do .env e substitui a
# linha correspondente. Ao final, confirma com grep redigido (nome + tamanho
# do valor, nunca o conteúdo).

set -euo pipefail

ENV_FILE="${ENV_FILE:-.env}"
ONLY_VAR="${2:-}"

if [[ "${1:-}" == "--only" ]]; then
  ONLY_VAR="${2:-}"
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERRO: $ENV_FILE não encontrado. Rode a partir da raiz do projeto." >&2
  exit 1
fi

# --- Catálogo do Grupo 2: nome da variável + onde gerar o novo valor ---
# Formato: "VAR_NAME|Descrição do portal"
SECRETS=(
  "MSAL_CLIENT_SECRET|Entra ID (portal.azure.com > App registrations > Certificates & secrets)"
  "AZURE_CLIENT_SECRET|Entra ID — mesmo App Registration do MSAL_CLIENT_SECRET acima"
  "INFLUX_TOKEN|InfluxDB UI (http://localhost:18086 > API Tokens)"
  "INFLUX_ADMIN_PASSWORD|InfluxDB UI (http://localhost:18086 > usuário admin)"
  "GITHUB_TOKEN|github.com/settings/tokens"
  "GRAFANA_ADMIN_PASSWORD|Grafana (http://localhost:3001 > Administration > Users)"
  "ZABBIX_PASSWORD|Zabbix UI (usuário Admin > Profile)"
  "LDAP_BIND_PASSWORD|Active Directory (conta GRUPOGADENS\\suporte)"
  "LDAP_PASSWORD|Active Directory — mesma conta do LDAP_BIND_PASSWORD acima"
  "WINRM_PASSWORD|Active Directory — mesma conta (usada via WinRM)"
  "CLICKUP_TOKEN|ClickUp (Perfil > Apps > API Token)"
  "ZENDESK_API_TOKEN|Zendesk (Admin Center > Apps and integrations > APIs)"
)

backup_env() {
  local ts
  ts="$(date +%Y%m%d%H%M%S)"
  cp "$ENV_FILE" "${ENV_FILE}.bak.pre-rotate-${ts}"
  echo "  Backup: ${ENV_FILE}.bak.pre-rotate-${ts}"
}

rotate_one() {
  local var_name="$1"
  local hint="$2"

  if ! grep -q "^${var_name}=" "$ENV_FILE"; then
    echo "[SKIP] ${var_name} não existe em ${ENV_FILE}"
    return
  fi

  echo
  echo "=== ${var_name} ==="
  echo "Portal: ${hint}"
  read -rp "Rotacionar agora? [y/N/skip]: " confirm
  if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
    echo "[SKIP] ${var_name}"
    return
  fi

  local new_value=""
  while [[ -z "$new_value" ]]; do
    read -rsp "Novo valor para ${var_name} (não será exibido): " new_value
    echo
    if [[ -z "$new_value" ]]; then
      echo "Valor vazio, tente novamente."
    fi
  done

  backup_env

  local tmp
  tmp="$(mktemp)"
  chmod 600 "$tmp"
  awk -v var="$var_name" -v val="$new_value" '
    BEGIN { key = "^" var "=" }
    $0 ~ key { print var "=" val; next }
    { print }
  ' "$ENV_FILE" > "$tmp"
  mv "$tmp" "$ENV_FILE"
  chmod 600 "$ENV_FILE"

  local len
  len=$(grep "^${var_name}=" "$ENV_FILE" | cut -d= -f2- | wc -c)
  echo "[OK] ${var_name} atualizado (${len} chars). Valor não exibido."
}

echo "Rotação interativa de secrets — Grupo 2 (IT Governance Dashboard)"
echo "Arquivo alvo: ${ENV_FILE}"
echo "Nenhum valor será exibido no terminal."

for entry in "${SECRETS[@]}"; do
  var_name="${entry%%|*}"
  hint="${entry#*|}"

  if [[ -n "$ONLY_VAR" && "$ONLY_VAR" != "$var_name" ]]; then
    continue
  fi

  rotate_one "$var_name" "$hint"
done

echo
echo "Rotação concluída. Lembre-se de:"
echo "  1. Recriar os containers que consomem essas variáveis (docker compose up -d --force-recreate)"
echo "  2. Revogar/desativar os valores antigos nos respectivos portais"
echo "  3. Conferir 'chmod 600 ${ENV_FILE}' e que '.env' está no .gitignore"
