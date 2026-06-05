# Infraestrutura — IT Governance Dashboard

> Última atualização: 2026-06-05 (gunicorn + systemd)
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

**Dívida técnica conhecida:** `zabbix` acumula 3 funções. Separar em `itgov` (app) + `zadmin`
(admin) quando o time crescer ou for para produção real. Ver [issue #126](https://github.com/robertoandr/it-governance-dashboard/issues/126).

## Paths Importantes

| Path | Descrição |
|------|-----------|
| `/opt/it-gov-dashboard/` | **Produção** — versão sem SSO (atrás de Cloudflare Access) |
| `~/projects/it-governance-dashboard/` | **Dev** — versão com SSO Entra ID (Sprint 11) |
| `/etc/nginx/conf.d/` | Configs nginx ativas |
| `deploy/` | Gunicorn config + systemd unit |

## Stack de Portas

```
Externo (público)
  └─ :443 HTTPS  →  Cloudflare Access (401 sem auth)  →  nginx  →  Gunicorn :8091

Interno / SSH tunnel
  ├─ :8080 HTTP  →  nginx  →  Gunicorn :8091   [200 OK]
  ├─ :8443 SSL   →  nginx  →  Gunicorn :8091   [200 OK]
  └─ :5001       →  nginx  →  Gunicorn :8091   [catch-all]

Direto (não exposto externamente)
  └─ :8091  →  Gunicorn (systemd: it-gov-dashboard.service)
               workers=9 (gthread), bind=127.0.0.1
```

## Configs nginx Ativas

```
/etc/nginx/conf.d/
├── noc.grupogadens.com.br.conf   ← Principal (vhosts HTTP/HTTPS/SSL)
├── dashboard-api.conf            ← Proxy :5001 → :8091
└── acme-challenge.conf           ← Let's Encrypt renewal
```

> Config versionada em `infra/nginx/noc.grupogadens.com.br.conf` (PR #125).

## Camadas de Autenticação

| Camada | Onde | Status |
|--------|------|--------|
| Cloudflare Access | Antes do nginx (público :443) | ✅ Ativo |
| nginx auth_basic | Não usado em prod (legado em `sites-available/`) | ❌ Inativo |
| Flask SSO (Entra ID) | Dentro do app | ⏳ Apenas em dev (PR #124) |

> **Risco:** produção não tem SSO. O único gate é o Cloudflare Access.
> Roadmap: deployar versão com SSO — ver backlog P1.

## Deploy & Operations

### Serviço systemd

```bash
# Status
sudo systemctl status it-gov-dashboard

# Start / Stop / Restart
sudo systemctl start it-gov-dashboard
sudo systemctl stop it-gov-dashboard
sudo systemctl restart it-gov-dashboard   # workers recriados — downtime ~1s

# Reload zero-downtime (SIGHUP — workers drenam requests antes de reiniciar)
sudo systemctl reload it-gov-dashboard

# Habilitar no boot
sudo systemctl enable it-gov-dashboard
```

### Logs

```bash
# Tempo real
sudo journalctl -u it-gov-dashboard -f

# Últimas 100 linhas
sudo journalctl -u it-gov-dashboard -n 100 --no-pager

# Com timestamp e sem paginação
sudo journalctl -u it-gov-dashboard --since "1 hour ago" --no-pager
```

### Ajustar Workers

Editar `deploy/gunicorn.conf.py`:

```python
workers = min(_cpu * 2 + 1, 9)  # padrão: 9 em host de 4 CPUs
```

Depois: `sudo systemctl restart it-gov-dashboard`

### Smoke Test (3 camadas)

```bash
for port in 8080 8443; do
  proto=$([ "$port" = "8080" ] && echo http || echo https)
  for path in / /static/style.css /api/health; do
    code=$(curl -sk -o /dev/null -w "%{http_code}" $proto://127.0.0.1:$port$path)
    echo ":$port $path -> $code"
  done
done
```

## Rollback Procedure

```bash
# 1. Parar Gunicorn
sudo systemctl stop it-gov-dashboard
sudo systemctl disable it-gov-dashboard

# 2. Restaurar python3 app.py
cd /opt/it-gov-dashboard
nohup .venv/bin/python app.py >> logs/healthcheck.log 2>&1 &
echo "PID=$!"

# 3. Verificar
curl -s -o /dev/null -w ":8091 -> %{http_code}\n" http://127.0.0.1:8091/
curl -s -o /dev/null -w ":8080 -> %{http_code}\n" http://127.0.0.1:8080/
```

Ver procedimento completo em `deploy/README.md`.

## Troubleshooting

### 401 no domínio público

Não é nginx — é **Cloudflare Access**. Verificar policies no Zero Trust dashboard.

### 502 Bad Gateway

Gunicorn caiu ou não subiu:

```bash
sudo systemctl status it-gov-dashboard
sudo journalctl -u it-gov-dashboard -n 50 --no-pager
sudo systemctl restart it-gov-dashboard
```

### Static files 404

Verificar `location /static/` em `noc.grupogadens.com.br.conf`.
Ver post-mortem `docs/postmortems/2026-06-05-static-assets.md`.

## Backlog de Infra

| Prio | Item | Issue/PR |
|------|------|----------|
| **P1** | Deploy versão com SSO em `/opt/` (sync dev → prod) | PR #124 |
| **P2** | Habilitar GitHub Actions self-hosted runner para CI/CD | — |
| **P3** | Separar user `zabbix` em `itgov` + `zadmin` | #126 |
| **P3** | Mover SQLite local para PostgreSQL (postgres já presente no host) | — |
| **P4** | Migrar Docker Compose para Kubernetes (`k8s/` já preparado) | — |
