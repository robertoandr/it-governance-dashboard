#!/usr/bin/env bash
# Certificado TLS de noc.grupogadens.com.br (ZeroSSL via acme.sh).
#
# Desde 29/09/2026 a renovação é AUTOMÁTICA: o cron do acme.sh (usuário
# zabbix, 4x/dia) renova ~13 dias antes do vencimento com validação HTTP-01.
# O FortiGate publica a porta 80 (VIP "Publish_zabbix_80") no itgov-nginx,
# que serve /.well-known/acme-challenge/ a partir de
# docker/nginx/certbot-webroot. O install-cert do acme.sh copia para
# docker/nginx/certs/noc.grupogadens.com.br.{crt,key} e recarrega o itgov-nginx.
#
# Este script é para quando a trigger Zabbix "Renovação automática do
# certificado SSL noc.grupogadens.com.br falhou" abrir (host
# "noc.grupogadens.com.br", grupo "Certificados SSL"):
#
#   scripts/renovar-cert-noc.sh --diagnostico  # DNS, porta 80 pela internet, nginx, config
#   scripts/renovar-cert-noc.sh --renovar      # força a renovação (HTTP-01) agora
#
# Plano B, se a porta 80 não puder ser liberada (DNS-01 MANUAL — o DNS da
# Central Server não tem API). ATENÇÃO: troca a validação salva no acme.sh
# para DNS manual; depois, voltar ao automático com --renovar.
#
#   scripts/renovar-cert-noc.sh --inicio     # gera o desafio e mostra o TXT
#   (criar o TXT no painel da Central Server)
#   scripts/renovar-cert-noc.sh --concluir   # espera o TXT propagar e renova
set -euo pipefail

DOMINIO="noc.grupogadens.com.br"
IP_PUBLICO="189.112.100.49"
REGISTRO="_acme-challenge.${DOMINIO}"
ACME="${HOME}/.acme.sh/acme.sh"
CONF="${HOME}/.acme.sh/${DOMINIO}_ecc/${DOMINIO}.conf"
PENDENTE="${HOME}/.acme.sh/${DOMINIO}.txt-pendente"
MANUAL="--yes-I-know-dns-manual-mode-enough-go-ahead-please"
ESPERA_MAX=900   # segundos aguardando o TXT propagar

# ITGOV_DIR permite rodar uma cópia do script contra o checkout de produção.
RAIZ="${ITGOV_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
WEBROOT="${RAIZ}/docker/nginx/certbot-webroot"
CERTS="${RAIZ}/docker/nginx/certs"
RELOAD="docker exec itgov-nginx nginx -s reload"

mostrar_certificado() {
    echo "Certificado servido agora:"
    echo | openssl s_client -connect "127.0.0.1:443" -servername "$DOMINIO" 2>/dev/null |
        openssl x509 -noout -issuer -enddate
}

diagnostico() {
    local falhas=0 token ip
    echo "== DNS público"
    ip=$(dig +short "$DOMINIO" @8.8.8.8 | tail -1)
    if [[ "$ip" == "$IP_PUBLICO" ]]; then
        echo "ok: $DOMINIO -> $ip"
    else
        echo "FALHA: $DOMINIO -> '${ip}' (esperado $IP_PUBLICO)"; falhas=$((falhas + 1))
    fi

    echo "== Validação salva no acme.sh"
    if grep -qx "Le_Webroot='${WEBROOT}'" "$CONF"; then
        echo "ok: HTTP-01 com webroot ${WEBROOT}"
    else
        echo "FALHA: $(grep '^Le_Webroot=' "$CONF") — rode --renovar para voltar ao HTTP-01"; falhas=$((falhas + 1))
    fi

    echo "== Porta 80 (arquivo de teste no webroot)"
    token="diagnostico-$(date +%s)"
    mkdir -p "${WEBROOT}/.well-known/acme-challenge"
    echo "$token" > "${WEBROOT}/.well-known/acme-challenge/${token}"
    if curl -fsS -m 10 -H "Host: ${DOMINIO}" "http://127.0.0.1/.well-known/acme-challenge/${token}" | grep -qx "$token"; then
        echo "ok: itgov-nginx serve o desafio localmente"
    else
        echo "FALHA: itgov-nginx não serve o desafio (container parado ou volume do webroot?)"; falhas=$((falhas + 1))
    fi
    if curl -fsS -m 10 --resolve "${DOMINIO}:80:${IP_PUBLICO}" "http://${DOMINIO}/.well-known/acme-challenge/${token}" | grep -qx "$token"; then
        echo "ok: porta 80 do IP público chega no itgov-nginx"
    else
        echo "FALHA: porta 80 de ${IP_PUBLICO} não chega no itgov-nginx. Resposta:"
        curl -sS -m 10 -o /dev/null -D - --resolve "${DOMINIO}:80:${IP_PUBLICO}" \
            "http://${DOMINIO}/.well-known/acme-challenge/${token}" 2>&1 | grep -i '^HTTP\|^Server' || true
        echo "  Conferir no FortiGate o VIP Publish_zabbix_80: porta externa 80 -> 172.29.2.11:80."
        falhas=$((falhas + 1))
    fi
    rm -f "${WEBROOT}/.well-known/acme-challenge/${token}"

    echo "== Certificado"
    mostrar_certificado
    grep '^Le_NextRenewTimeStr=' "$CONF" | sed 's/^/próxima renovação agendada: /'

    if (( falhas )); then
        echo; echo "${falhas} verificação(ões) falharam."; exit 1
    fi
    echo; echo "Tudo certo para a renovação automática."
}

renovar() {
    "$ACME" --issue --force --server zerossl -d "$DOMINIO" -w "$WEBROOT" --keylength ec-256
    "$ACME" --install-cert -d "$DOMINIO" --ecc \
        --key-file "${CERTS}/${DOMINIO}.key" \
        --fullchain-file "${CERTS}/${DOMINIO}.crt" \
        --reloadcmd "$RELOAD"
    echo
    mostrar_certificado
}

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
    mostrar_certificado
    echo "O registro TXT pode ser apagado do painel."
    echo "Quando a porta 80 voltar, rode '$0 --renovar' para retomar a renovação automática."
}

case "${1:-}" in
    --diagnostico) diagnostico ;;
    --renovar)     renovar ;;
    --inicio)      inicio ;;
    --concluir)    concluir ;;
    *) echo "Uso: $0 --diagnostico | --renovar | --inicio | --concluir" >&2; exit 2 ;;
esac
