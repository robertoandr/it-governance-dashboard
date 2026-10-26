#!/usr/bin/env bash
#
# Sobe o GoPhish num container à parte do dashboard (não entra no docker-compose).
#
# - Painel admin (3333) preso no loopback do host: acesso só por túnel SSH
#   (ssh -L 3333:127.0.0.1:3333 zabbix@itgov-dev), como o InfluxDB.
# - Container ligado à rede do dashboard, para o app ler a API como
#   https://gophish:3333 (certificado autoassinado → GOPHISH_VERIFY_TLS=0).
# - Dados num volume próprio (gophish_data): sobrevivem a recriação.
# - Landing page (o link do e-mail) publicada na rede interna em
#   ${LAN_IP}:8080 — é a URL que vai na campanha do piloto. Em produção
#   geral, trocar por um host com TLS no nginx.
#
# Rodar numa janela de manutenção. Idempotente: recriar não perde dados.
set -euo pipefail

IMAGEM="gophish/gophish:0.12.1"
NOME="gophish"
REDE="it-governance-dashboard_itgov-net"
VOLUME="gophish_data"
LAN_IP="${LAN_IP:-172.29.2.11}"   # IP interno onde a landing fica acessível

if ! docker network inspect "$REDE" >/dev/null 2>&1; then
  echo "Rede $REDE não existe. Confira 'docker network ls' e ajuste REDE no script." >&2
  exit 1
fi

docker volume create "$VOLUME" >/dev/null
docker rm -f "$NOME" >/dev/null 2>&1 || true

docker run -d \
  --name "$NOME" \
  --restart unless-stopped \
  --network "$REDE" \
  -p 127.0.0.1:3333:3333 \
  -p "${LAN_IP}:8080:80" \
  -v "$VOLUME":/opt/gophish \
  "$IMAGEM"

echo "GoPhish no ar. Senha inicial do admin (login 'admin'):"
sleep 3
docker logs "$NOME" 2>&1 | grep -i "please login with the username admin and the password" || \
  echo "  (procure 'password' em: docker logs $NOME)"

cat <<'FIM'

Próximos passos (manuais, no console do GoPhish por túnel SSH):
  1. ssh -L 3333:127.0.0.1:3333 zabbix@itgov-dev  e abrir https://127.0.0.1:3333
  2. Trocar a senha do admin.
  3. Em Account Settings, copiar a API Key.
  4. No .env do dashboard:
       GOPHISH_API_URL=https://gophish:3333
       GOPHISH_API_KEY=<a chave copiada>
       GOPHISH_VERIFY_TLS=0
  5. Recriar o app:  docker compose up -d app

Daí em diante, tudo pelo dashboard (aba Cyber > Phishing): criar o perfil de
envio, montar e disparar a campanha. No piloto, a URL da landing é
http://LAN_IP:8080 (o link que vai no e-mail). Não é preciso abrir o console.
FIM
