#!/usr/bin/env bash
# Renovação do certificado TLS de noc.grupogadens.com.br (ZeroSSL via acme.sh,
# DNS-01 em modo MANUAL — o DNS da Central Server não tem API e o FortiGate
# não encaminha a porta 80, então HTTP-01 não funciona).
#
# Disparado pela trigger Zabbix "Renovar certificado SSL noc.grupogadens.com.br"
# (host "noc.grupogadens.com.br", grupo "Certificados SSL").
#
# Uso:
#   scripts/renovar-cert-noc.sh --inicio     # gera o desafio e mostra o TXT
#   (criar o TXT no painel da Central Server)
#   scripts/renovar-cert-noc.sh --concluir   # espera o TXT propagar e renova
#
# O acme.sh já tem install-cert configurado: copia para docker/nginx/certs/
# noc.grupogadens.com.br.{crt,key} e recarrega o itgov-nginx.
set -euo pipefail

DOMINIO="noc.grupogadens.com.br"
REGISTRO="_acme-challenge.${DOMINIO}"
ACME="${HOME}/.acme.sh/acme.sh"
PENDENTE="${HOME}/.acme.sh/${DOMINIO}.txt-pendente"
MANUAL="--yes-I-know-dns-manual-mode-enough-go-ahead-please"
ESPERA_MAX=900   # segundos aguardando o TXT propagar

inicio() {
    local saida valor
    saida=$("$ACME" --issue --dns -d "$DOMINIO" --force "$MANUAL" 2>&1 || true)
    valor=$(grep -oP "TXT value: '\K[^']+" <<<"$saida" | head -1)
    if [[ -z "$valor" ]]; then
        echo "ERRO: acme.sh não gerou o desafio. Saída:" >&2
        echo "$saida" | tail -20 >&2
        exit 1
    fi
    echo "$valor" > "$PENDENTE"
    cat <<EOF

Crie (ou edite) o registro no painel DNS da Central Server:

  Tipo : TXT
  Nome : _acme-challenge.noc        <- SÓ isso; o painel completa o domínio
  Valor: ${valor}
  TTL  : o menor disponível

Apague qualquer TXT antigo com o mesmo nome e não deixe espaços no valor.
Depois rode:  $0 --concluir
EOF
}

concluir() {
    [[ -f "$PENDENTE" ]] || { echo "ERRO: rode '$0 --inicio' antes." >&2; exit 1; }
    local esperado t=0
    esperado=$(<"$PENDENTE")
    echo "Aguardando o TXT em dns1.grupogadens.com.br e 8.8.8.8 (até $((ESPERA_MAX / 60)) min)..."
    until dig +short TXT "$REGISTRO" @dns1.grupogadens.com.br | grep -qx "\"${esperado}\"" &&
          dig +short TXT "$REGISTRO" @8.8.8.8 | grep -qx "\"${esperado}\""; do
        if (( t >= ESPERA_MAX )); then
            echo "ERRO: TXT não apareceu. Publicado hoje:" >&2
            dig +short TXT "$REGISTRO" @dns1.grupogadens.com.br >&2
            echo "Confira nome (_acme-challenge.noc) e valor (${esperado}) no painel." >&2
            exit 1
        fi
        sleep 20; t=$((t + 20))
    done
    echo "TXT publicado. Validando..."
    "$ACME" --renew -d "$DOMINIO" --ecc "$MANUAL"
    rm -f "$PENDENTE"
    echo
    echo "Certificado servido agora:"
    echo | openssl s_client -connect "${DOMINIO}:443" -servername "$DOMINIO" 2>/dev/null |
        openssl x509 -noout -issuer -enddate
    echo "O registro TXT pode ser apagado do painel."
}

case "${1:-}" in
    --inicio)   inicio ;;
    --concluir) concluir ;;
    *) echo "Uso: $0 --inicio | --concluir" >&2; exit 2 ;;
esac
