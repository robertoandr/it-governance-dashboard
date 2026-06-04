# Deployment Guide — IT Governance Dashboard

## Pré-requisitos

| Ferramenta | Versão mínima |
|---|---|
| Docker | 24.0+ |
| Docker Compose | v2.20+ (plugin, não standalone) |
| Make | 4.x |
| Git | 2.x |

```bash
docker --version        # Docker version 24.x.x
docker compose version  # Docker Compose version v2.x.x
```

---

## Setup local (dev)

```bash
# 1. Clonar
git clone git@github.com:robertoandr/it-governance-dashboard.git
cd it-governance-dashboard

# 2. Configurar variáveis
cp .env.example .env
# Editar .env com seus valores reais (ver seção abaixo)

# 3. Subir stack
make up

# 4. Validar
curl http://localhost:5000/health
# → {"status": "healthy", "version": "1.1.0", "environment": "production"}
```

---

## Variáveis de ambiente obrigatórias

| Variável | Descrição | Exemplo |
|---|---|---|
| `APP__SECRET_KEY` | Chave Flask (mínimo 32 chars) | `openssl rand -hex 32` |
| `APP__ENVIRONMENT` | `development` / `production` | `production` |
| `INFLUX_TOKEN` | Token de acesso InfluxDB | `abc123...` |
| `INFLUX_ORG` | Organização InfluxDB | `gadens` |
| `INFLUX_BUCKET` | Bucket de métricas | `govti` |
| `ZABBIX_URL` | URL API Zabbix | `https://zabbix.example.com/api_jsonrpc.php` |
| `ZABBIX_USER` | Usuário API Zabbix | `api_user` |
| `ZABBIX_PASSWORD` | Senha API Zabbix | — |

Variáveis opcionais (integrações desabilitadas se ausentes):

| Variável | Integração |
|---|---|
| `GITHUB__TOKEN` + `GITHUB__ORG` | GitHub |
| `ZENDESK__URL` + `ZENDESK__EMAIL` + `ZENDESK__TOKEN` | Zendesk |
| `GRAPH__TENANT_ID` + `GRAPH__CLIENT_ID` + `GRAPH__CLIENT_SECRET` | M365 |

---

## Comandos Make

```bash
make up          # Sobe stack dev (app + redis + influxdb)
make prod        # Sobe stack completa com nginx (profile prod)
make down        # Para containers (preserva volumes)
make logs        # Tail de logs do app
make shell       # Shell no container app
make build       # Build local da imagem
make image-size  # Exibe tamanho da imagem
```

---

## Stack de serviços

```
┌─────────────────────────────────────────────────────────┐
│                      itgov-net                          │
│                                                         │
│  nginx:80/443 (prod)                                    │
│       │                                                 │
│  app:5000  ──→  redis:6379                              │
│       │                                                 │
│       └──────→  influxdb:8086                           │
│                                                         │
│  grafana:3000 ──→ influxdb:8086                         │
└─────────────────────────────────────────────────────────┘
```

---

## Produção com nginx

```bash
# Adicionar certificados TLS
mkdir -p docker/nginx/certs
cp your.crt docker/nginx/certs/itgov.crt
cp your.key docker/nginx/certs/itgov.key

# Subir com profile prod
make prod
```

A configuração de nginx esperada em `docker/nginx/nginx.conf`:

```nginx
server {
    listen 443 ssl;
    server_name itgov.example.com;
    ssl_certificate     /etc/nginx/certs/itgov.crt;
    ssl_certificate_key /etc/nginx/certs/itgov.key;

    location / {
        proxy_pass http://app:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
server {
    listen 80;
    return 301 https://$host$request_uri;
}
```

---

## Imagem Docker (GHCR)

```bash
# Pull da imagem publicada
docker pull ghcr.io/robertoandr/it-governance-dashboard:latest

# Verificar assinatura (cosign)
cosign verify \
  --certificate-identity-regexp "https://github.com/robertoandr/it-governance-dashboard" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  ghcr.io/robertoandr/it-governance-dashboard:latest
```

Tags disponíveis:

| Tag | Quando |
|---|---|
| `latest` | Push em `main` |
| `sha-<curto>` | Todo push |
| `v1.2.0` | Tag semver |
| `1.2` | Major.Minor |

---

## Troubleshooting

### App não sobe / healthcheck failing
```bash
docker compose logs app     # Ver stack trace
curl -v http://localhost:5000/health
```

Causas comuns:
- `APP__SECRET_KEY` vazia → erro na startup do Flask
- `data/` sem permissão de escrita → schema.sql não aplicado
- Porta 5000 em uso → mudar `APP_PORT=5001` no `.env`

### InfluxDB não aceita conexão
```bash
docker compose logs influxdb
# Se "token inválido": regenerar via UI em http://localhost:8086
```

### Redis ping falha
```bash
docker compose exec redis redis-cli ping
# Deve retornar PONG
```

### Imagem muito grande
```bash
make image-size
# Se >200MB: verificar se .dockerignore exclui venv/, tests/, docs/
docker history itgov-app:local
```

---

## Rollback procedure

```bash
# 1. Identificar imagem anterior
docker images ghcr.io/robertoandr/it-governance-dashboard

# 2. Atualizar GHCR tag no compose
# Editar docker-compose.yml: image: ghcr.io/.../...:sha-<commit-anterior>

# 3. Redeployar
docker compose up -d --no-build

# 4. Verificar
curl http://localhost:5000/health
```

Para rollback de banco (SQLite): o arquivo `data/govti.db` é persistido no volume `app_data`. Para restaurar:

```bash
docker compose down
docker run --rm -v it-governance-dashboard_app_data:/data \
  alpine cp /data/govti.db.bak /data/govti.db
docker compose up -d
```
