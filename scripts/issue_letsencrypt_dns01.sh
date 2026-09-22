#!/usr/bin/env bash
# issue_letsencrypt_dns01.sh - Emite/renova o certificado Let's Encrypt de
# noc.grupogadens.com.br via desafio DNS-01, usando o plugin dns_pdns do
# acme.sh contra a API do PowerDNS (dns1/dns2.grupogadens.com.br).
#
# Não depende de porta 80/443 inbound — usa só a API do PowerDNS (outbound)
# e a validação pública de DNS feita pelos servidores do Let's Encrypt.
#
# Pré-requisitos:
#   - acme.sh instalado em ~/.acme.sh (ver: curl https://get.acme.sh | sh)
#   - PDNS_Url, PDNS_ServerId, PDNS_Token, PDNS_Ttl definidos em .env
#     (ver .env.example para onde obter cada valor com o time de DNS)
#
# Uso:
#   scripts/issue_letsencrypt_dns01.sh          # emite/renova em produção
#   scripts/issue_letsencrypt_dns01.sh --staging # valida sem gastar rate limit

set -euo pipefail

DOMAIN="noc.grupogadens.com.br"
ACME="$HOME/.acme.sh/acme.sh"
ENV_FILE="${ENV_FILE:-.env}"
CERT_DIR="docker/nginx/certs"
NGINX_CONTAINER="itgov-nginx"

if [[ ! -x "$ACME" ]]; then
  echo "ERRO: acme.sh não encontrado em $ACME. Instale com: curl https://get.acme.sh | sh" >&2
  exit 1
fi

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

for var in PDNS_Url PDNS_ServerId PDNS_Token PDNS_Ttl; do
  if [[ -z "${!var:-}" ]]; then
    echo "ERRO: $var não está definido. Preencha em $ENV_FILE (ver .env.example)." >&2
    exit 1
  fi
done
export PDNS_Url PDNS_ServerId PDNS_Token PDNS_Ttl

STAGING_FLAG=()
if [[ "${1:-}" == "--staging" ]]; then
  STAGING_FLAG=(--staging)
  echo "Modo staging: certificado NÃO será confiável, mas não conta para o rate limit de produção."
fi

"$ACME" --issue --dns dns_pdns -d "$DOMAIN" "${STAGING_FLAG[@]}"

"$ACME" --install-cert -d "$DOMAIN" \
  --fullchain-file "$CERT_DIR/itgov.crt" \
  --key-file "$CERT_DIR/itgov.key" \
  --reloadcmd "docker exec $NGINX_CONTAINER nginx -s reload"

echo "Certificado instalado em $CERT_DIR/itgov.crt / itgov.key e Nginx recarregado."
