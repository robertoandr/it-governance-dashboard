# Infraestrutura — IT Governance Dashboard

> Última atualização: 2026-06-05
> Mantenedor: Roberto Andrade

## Servidor

| Item | Valor |
|------|-------|
| Hostname | `itgov-dev` |
| IP | `172.29.2.11` |
| OS | Ubuntu (Debian-based) |
| Função | Dev + Prod (mesma VM) |
| Acesso SSH | `ssh zabbix@172.29.2.11` |

### Usuários

| User | Papel | Sudo | Notas |
|------|-------|------|-------|
| `zabbix` | Admin + Daemon Zabbix + App owner | ✅ | ⚠️ Acumula papéis — ver issue #126 |
| `github-runner` | CI/CD runner | ❌ | Self-hosted GH Actions |
| `postgres` | Daemon PostgreSQL | ❌ | Sistema |

**Dívida técnica conhecida:** `zabbix` acumula 3 funções. Separar em `itgov` (app) + `zadmin` (admin) quando time crescer ou for para produção real. Ver [issue #126](https://github.com/robertoandr/it-governance-dashboard/issues/126).

## Paths Importantes

| Path | Descrição |
|------|-----------|
| `/opt/it-gov-dashboard/` | **Produção** — versão sem SSO (atrás de Cloudflare Access) |
| `~/projects/it-governance-dashboard/` | **Dev** — versão com SSO Entra ID (Sprint 11) |
| `/etc/nginx/conf.d/` | Configs nginx ativas |
| `/etc/nginx/backup-20260517/` | Backups arquivados (não carregados) |

## Stack de Portas

```
Externo (público)
  └─ :443 HTTPS  →  Cloudflare Access (401 sem auth)  →  nginx  →  Flask :8091

Interno / SSH tunnel
  ├─ :8080 HTTP  →  nginx  →  Flask :8091   [200 OK]
  ├─ :8443 SSL   →  nginx  →  Flask :8091   [200 OK]
  └─ :5001       →  nginx  →  Flask :8091   [catch-all, sem auth extra]

Direto (não exposto externamente)
  └─ :8091  →  Flask (python3 app.py em prod)
             ⚠️  dev server em produção — ver backlog P1
```

## Configs nginx Ativas

```
/etc/nginx/conf.d/
├── noc.grupogadens.com.br.conf   ← Principal (vhosts HTTP/HTTPS/SSL)
├── dashboard-api.conf            ← Proxy :5001 → :8091
└── acme-challenge.conf           ← Let's Encrypt renewal
```

Validar e recarregar:

```bash
sudo nginx -t && sudo systemctl reload nginx
sudo nginx -T > /tmp/nginx-full.conf   # dump completo da config em memória
```

> **Post-mortem:** em 2026-06-05 assets estáticos retornavam 404 via :8080 porque
> `location /static/` estava ausente no bloco HTTP. Corrigido no PR #125.
> Ver `docs/postmortems/2026-06-05-static-assets.md`.

## Camadas de Autenticação

| Camada | Onde | Status |
|--------|------|--------|
| Cloudflare Access | Antes do nginx (público :443) | ✅ Ativo |
| nginx auth_basic | Não usado em prod (legado em `sites-available/`) | ❌ Inativo |
| Flask SSO (Entra ID) | Dentro do app | ⏳ Apenas em dev (PR #124) |

> **Risco:** produção em `/opt/` **não tem SSO**. O único gate é o Cloudflare Access.
> Roadmap: deployar versão com SSO em prod — ver backlog P1.

## Comandos Frequentes

```bash
# Status do app
ps aux | grep -E "gunicorn|python.*app.py" | grep -v grep

# Logs nginx
sudo journalctl -u nginx -f

# Reload nginx
sudo nginx -t && sudo systemctl reload nginx

# Smoke test (3 camadas)
for port in 8080 8443; do
  proto=$([ "$port" = "8080" ] && echo http || echo https)
  for path in / /static/style.css /api/health; do
    code=$(curl -sk -o /dev/null -w "%{http_code}" $proto://127.0.0.1:$port$path)
    echo ":$port $path -> $code"
  done
done
```

## Troubleshooting

### 401 no domínio público (`noc.grupogadens.com.br`)

Não é nginx — `auth_basic` não está ativo em prod. É **Cloudflare Access**. Verificar policies no Zero Trust dashboard.

### 502 Bad Gateway

Flask caiu. Verificar e reiniciar:

```bash
ps aux | grep python
# reiniciar manualmente enquanto não há systemd unit:
cd /opt/it-gov-dashboard && python3 app.py &
```

### Static files 404

Verificar se `location /static/` está presente no server block correto em `noc.grupogadens.com.br.conf`. Ver post-mortem `docs/postmortems/2026-06-05-static-assets.md`.

## Backlog de Infra

| Prio | Item | Issue |
|------|------|-------|
| **P1** | Deploy versão com SSO em `/opt/` (sync dev → prod) | PR #124 |
| **P1** | Substituir `python3 app.py` por gunicorn + systemd unit | — |
| **P2** | Habilitar GitHub Actions self-hosted runner para CI/CD | — |
| **P3** | Separar user `zabbix` em `itgov` + `zadmin` | #126 |
| **P3** | Mover SQLite local para PostgreSQL (postgres já presente no host) | — |
| **P4** | Migrar Docker Compose para Kubernetes (`k8s/` já preparado) | — |
