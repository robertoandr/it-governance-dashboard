# Emissão/renovação de certificado — Let's Encrypt via DNS-01 (PowerDNS)

## Contexto

`noc.grupogadens.com.br` resolve publicamente (`189.112.203.34`), mas a porta 80
externa não chega em `172.29.2.11` (confirmado pelo próprio validador do Let's
Encrypt em staging — timeout de conexão). A porta 443 está acessível. Por isso
o desafio **HTTP-01** não funciona hoje (ver branch `feat/letsencrypt-http01`),
enquanto **DNS-01** é viável, pois não depende de nenhuma porta inbound — só de
acesso outbound à API do PowerDNS que hospeda a zona `grupogadens.com.br`
(`dns1.grupogadens.com.br`, `dns2.grupogadens.com.br` — PowerDNS Authoritative
Server 4.7/4.8).

## Pré-requisitos (pendentes com o time de DNS/infra)

Antes de emitir de verdade, confirmar com quem administra o PowerDNS:

1. A API HTTP do PowerDNS está habilitada (`api=yes`, `webserver=yes` em
   `pdns.conf`) e acessível a partir do `itgov-dev` (172.29.2.11)? Porta
   padrão `8081` — pode exigir liberação de firewall.
2. Qual o valor de `api-key` (`PDNS_Token`) a ser usado — gerar uma chave
   dedicada para esta automação, não reaproveitar uma chave de outro uso.
3. Confirmar `PDNS_ServerId` (normalmente `localhost`).

Preencher os 4 valores em `.env` (nunca commitar — ver `.env.example` para o
formato e onde cada um é usado).

## Instalação (já feita no itgov-dev em 22/09/2026)

```bash
curl https://get.acme.sh | sh -s email=flavio@grupogadens.com.br
```

Instala em `~/.acme.sh` (sem root) e registra automaticamente um cron job
(`~/.acme.sh/acme.sh --cron`, 4x/dia) que verifica validade dos certificados
emitidos e renova + reaplica o deploy hook quando faltar menos de 30 dias
para expirar. Não faz nada até que um certificado tenha sido emitido.

## Emissão (NÃO EXECUTADO — pendente dos pré-requisitos acima)

```bash
# 1. Validar em staging primeiro (não conta para rate limit de produção)
scripts/issue_letsencrypt_dns01.sh --staging

# 2. Se staging OK, emitir em produção
scripts/issue_letsencrypt_dns01.sh
```

O script (`scripts/issue_letsencrypt_dns01.sh`):

- Carrega `PDNS_Url`, `PDNS_ServerId`, `PDNS_Token`, `PDNS_Ttl` de `.env`.
- Roda `acme.sh --issue --dns dns_pdns -d noc.grupogadens.com.br`.
- Instala o certificado com `acme.sh --install-cert`, copiando
  `fullchain`/`key` para `docker/nginx/certs/itgov.crt` e `itgov.key` — os
  mesmos nomes que `docker/nginx/nginx.conf` já espera, então não é preciso
  mudar a config do Nginx.
- Define `--reloadcmd "docker exec itgov-nginx nginx -s reload"`, que o
  acme.sh executa automaticamente a cada emissão/renovação (manual ou via
  cron), sem precisar recriar o container.

## Renovação automática

Já configurada pelo cron do acme.sh (etapa de instalação). Nenhuma ação
adicional necessária após a primeira emissão bem-sucedida — o deploy hook
(`--reloadcmd`) roda sozinho a cada renovação.

## Rollback

Se o certificado emitido causar algum problema, os arquivos anteriores
(self-signed) não são sobrescritos até `--install-cert` rodar com sucesso.
Para reverter manualmente: restaurar backup de `docker/nginx/certs/itgov.crt`
e `itgov.key`, depois `docker exec itgov-nginx nginx -s reload`.
