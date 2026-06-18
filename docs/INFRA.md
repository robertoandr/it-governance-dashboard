# IT Governance Dashboard - Infraestrutura

## Padrao de acesso (OFICIAL)

- **URL publica:** https://noc.grupogadens.com.br:8443/gov/
- **API (interna ao nginx):** https://noc.grupogadens.com.br:8443/api/overview
- **Acesso direto (interno):** http://127.0.0.1:5000/gov/

## Arquitetura

```
Browser
  |
  v
nginx :8443 (HTTPS, self-signed)
  |
  +-- location /gov/   --> proxy_pass http://127.0.0.1:5000/gov/
  +-- location /api/   --> proxy_pass http://127.0.0.1:5000/api/
  +-- location /static/--> proxy_pass http://127.0.0.1:5000/static/
  |
  v
Docker container itgov-app  (gunicorn, port 5000)
  |
  +-- Blueprint dashboards:  url_prefix="/gov"  (app/__init__.py:142)
  +-- API blueprint:         /api/overview       (app/api/dashboards.py)
  |
  +-- InfluxDB  http://influxdb:8086  (container, porta host 18086)
  +-- Redis     redis:6379            (container)
```

## Arquivo nginx OFICIAL (unico valido)

- `/etc/nginx/conf.d/noc.grupogadens.com.br.conf`
- Backups arquivados em `/etc/nginx/_archive/<data>/` -- nao sao lidos pelo nginx

## Regra de ouro do deploy

O Flask roda **dentro do container**. Editar arquivos no host NAO altera o container.
Sempre rebuildar apos mudancas no codigo:

```bash
cd /home/zabbix/projects/it-governance-dashboard
docker compose up -d --build --force-recreate app
```

## Processo de deploy completo

```bash
# 1. Testes unitarios
python -m pytest tests/ -x -q

# 2. Rebuild da imagem
docker compose up -d --build --force-recreate app

# 3. Aguarda healthcheck e valida rotas
sleep 15
curl -sk https://172.29.2.11:8443/gov/ -o /dev/null -w "%{http_code}\n"     # espera 200
curl -sk https://172.29.2.11:8443/api/overview -o /dev/null -w "%{http_code}\n"  # espera 200

# 4. Valida score no JSON
curl -sk https://172.29.2.11:8443/api/overview | python3 -c "import sys,json; d=json.load(sys.stdin); print('score:', d['global_score'])"

# 5. Validacao do Zendesk trigger (dry-run)
ZENDESK_DRY_RUN=true python3 /opt/zabbix/zendesk_trigger.py
```

## Fonte da verdade

Repositorio: `github.com/robertoandr/it-governance-dashboard`
Diretorio local: `/home/zabbix/projects/it-governance-dashboard/`
Imagem Docker: `ghcr.io/robertoandr/it-governance-dashboard:latest`

> `/opt/it-gov-dashboard/` e uma copia legada -- NAO usar para deploy.
> Fonte canonica e `/home/zabbix/projects/it-governance-dashboard/`.

## Servicos dependentes (portas)

| Porta  | Servico        | Notas                              |
|--------|----------------|------------------------------------|
| 8443   | nginx HTTPS    | self-signed, dashboard principal   |
| 5000   | Flask/gunicorn | container itgov-app                |
| 18086  | InfluxDB v2    | container itgov-influxdb           |
| 6379   | Redis          | container itgov-redis (interno)    |
| 8080   | nginx HTTP     | Zabbix UI + proxies legados        |

## Armadilhas conhecidas

1. **Bind mounts de hot-patch**: Foram necessarios de mai/2026 a jun/2026 para patchear
   o container sem rebuild. Removidos em 2026-06-18 apos rebuild que incorporou o codigo.
   Se reaparecerem em docker-compose.yml, revisar se sao realmente necessarios ou legado.

2. **Dois diretorios com mesmo nome de container**: `/opt/it-gov-dashboard/docker-compose.yml`
   e `/home/zabbix/projects/it-governance-dashboard/docker-compose.yml` declaram ambos
   `container_name: itgov-app`. Nunca rodar `docker compose up` em `/opt/` enquanto o
   container do `/home/zabbix/projects/` estiver rodando -- causaria conflito de nomes.

3. **url_prefix obrigatorio**: O blueprint `dashboards_bp` DEVE ser registrado com
   `url_prefix="/gov"` (app/__init__.py). Sem isso, Flask serve em `/` e o nginx
   (que aponta para `/gov/`) retorna 404 mesmo com nginx correto.

4. **Alpine.js CSP build**: O template usa `@alpinejs/csp` (sem eval). Qualquer
   expressao JavaScript inline em diretivas `x-*` quebrara silenciosamente.
   Toda logica deve ser em `Alpine.data()` com getters computados.
