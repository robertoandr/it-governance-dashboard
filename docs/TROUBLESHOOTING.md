ting — IT Governance Dashboard

## Bug: "Only one datasource per organization can be marked as default"

**Sintoma:** Logs do Grafana mostram erro de múltiplos defaults.

```
logger=provisioning level=error msg="Failed to provision data sources"
error="datasource.yaml config is invalid. Only one datasource per
organization can be marked as default"
```

**Causa:** Arquivos `.bak`, `.old`, `.disabled` na pasta
`grafana/provisioning/datasources/` são **lidos pelo Grafana**
(ele parseia por conteúdo YAML, não por extensão de arquivo).
Se qualquer um desses arquivos contiver `isDefault: true`,
o Grafana soma os defaults e rejeita a configuração.

**Solução:**

```bash
# 1. Identifica os arquivos problemáticos
docker exec itgov-grafana ls -la /etc/grafana/provisioning/datasources/

# 2. Move backups pra FORA do mount (não deleta — segurança)
mv grafana/provisioning/datasources/*.bak /opt/backups/

# 3. Restart Grafana
docker compose restart grafana

# 4. Valida que o erro sumiu
docker compose logs grafana --tail 30 | grep -iE "(error|default)"
```

**Prevenção:** o `.gitignore` já bloqueia commit de `*.bak`, `*.old`,
`*.tmp`, `*.orig`, `*.disabled` dentro de `grafana/provisioning/`.
Nunca salve backups de datasource dentro da pasta de provisioning.

---

## Bug: Dashboards não aparecem após restart do Grafana

**Sintoma:** Grafana sobe mas os dashboards da pasta `IT Governance` somem.

**Causa provável:** O arquivo `grafana/provisioning/dashboards/dashboards.yml`
aponta para o path correto mas o volume não está montado.

**Diagnóstico:**

```bash
# Verifica se os JSONs estão visíveis dentro do container
docker exec itgov-grafana ls /var/lib/grafana/dashboards/

# Verifica o mount do volume
docker inspect itgov-grafana \
  --format '{{range .Mounts}}{{.Source}} → {{.Destination}}{{println}}{{end}}'
```

**Solução:** confirmar que o `docker-compose.yml` tem o bind mount correto:

```yaml
volumes:
  - ./grafana/dashboards:/var/lib/grafana/dashboards:ro
```

---

## Bug: Datasource InfluxDB retorna "no such bucket"

**Sintoma:** Painéis mostram erro `bucket "governance_raw" not found`.

**Causa:** O bucket não foi criado pelo `influxdb-init` ou o token não tem permissão.

**Diagnóstico:**

```bash
# Lista buckets existentes
docker exec itgov-influxdb influx bucket list \
  --host http://localhost:8086 \
  --token "$INFLUX_TOKEN" \
  --org "$INFLUX_ORG"
```

**Solução:** Reexecutar o init manualmente:

```bash
docker compose run --rm influxdb-init

---

## Bug #5: Stashes Perdidos em `git reset --hard`

### Sintoma
Após `git reset --hard`, mudanças em arquivos como app.py,
static/*, templates/* "desaparecem" do working tree.

### Causa Raiz
Sequência destrutiva:
1. `git stash` cria commits órfãos (refs/stash)
2. `git rebase --abort` pode dropar a stash ref
3. `git reset --hard` apaga working tree
4. Commits da stash ficam **dangling** (sem ref apontando)

### Diagnóstico
```bash
# Lista TUDO que está pendurado
git fsck --lost-found

# Pra cada dangling commit:
git show <sha> --stat
```

<<<<<<< HEAD
### Identificação de Stash Órfã
Mensagens com padrão:
- `WIP on <branch>: <sha> <mensagem>`
- `On <branch>: <descrição>`
- `index on <branch>: <sha> <mensagem>`

### Recuperação Completa
```bash
# 1. Inspeciona
git show <sha> --stat

# 2. Cria branch de resgate
git branch rescue/<descrição> <sha>

# 3. Cherry-pick por arquivo
git checkout <sha> -- path/to/file

# 4. OU restaura como stash novamente
git stash apply <sha>
```

### Prevenção (REGRA DE OURO)
```bash
# SEMPRE antes de operação destrutiva:

# 1. Branch de backup
git branch backup/$(date +%Y%m%d-%H%M)

# 2. Stash explícito com nome claro
git stash push -m "Backup pré-reset - $(date +%H:%M)"

# 3. Lista stashes pra confirmar
git stash list

# SÓ ENTÃO faz o que precisa

---

## Bug: Dashboards novos não aparecem — Grafana mostra conteúdo antigo

**Sintoma:** Após provisionar dashboards no Docker Grafana, o browser
em `http://<HOST>:3000` continua mostrando dashboards antigos ou vazios.

**Causa raiz:** Há **dois Grafanas rodando** no mesmo servidor:

| Instância | Porta | Gerenciamento | Dashboards |
|-----------|-------|---------------|------------|
| Nativo (systemd) | `3000` | `/etc/grafana/` | Antigos/nenhum |
| Docker (`itgov-grafana`) | `3000/tcp` (interno) | `/opt/it-gov-dashboard/grafana/` | Sprint 10A ✅ |

O Grafana Docker **não expõe a porta 3000 para o host** — é acessado
exclusivamente via Nginx nas portas `8090` (HTTP→HTTPS) e `8453` (HTTPS).

**URL correta do Docker Grafana:**
```
http://172.29.2.11:8090   →  redireciona para HTTPS
https://172.29.2.11:8453  →  Grafana com dashboards do Sprint 10A
```

**Diagnóstico rápido:**
```bash
# Qual processo está na porta 3000?
ss -tlnp | grep 3000
systemctl status grafana-server   # se existir = Grafana nativo no host

# Quais containers expõem porta?
docker ps --format "table {{.Names}}\t{{.Ports}}"

# Dashboards no Docker Grafana (sem precisar de senha):
docker cp itgov-grafana:/var/lib/grafana/grafana.db /tmp/g.db
python3 -c "
import sqlite3
conn = sqlite3.connect('/tmp/g.db')
[print(r) for r in conn.execute('SELECT title, uid FROM dashboard WHERE is_folder=0')]
"
```

**Prevenção:**
- Acessar sempre via Nginx (`8090`/`8453`), nunca via `:3000` direto
- Considerar parar o Grafana nativo se não for mais usado:
  `sudo systemctl stop grafana-server && sudo systemctl disable grafana-server`

---

## Resolução Definitiva: Bug #4 (30/05/2026)

### Ação Executada
Removido completamente o Grafana nativo do servidor:

```bash
sudo systemctl stop grafana-server
sudo systemctl disable grafana-server
sudo apt remove --purge grafana
sudo rm -rf /usr/share/grafana/ /etc/grafana/ /var/lib/grafana/
```

### Achado Bônus: Plugin Zabbix Órfão
Após matar o Grafana nativo, sobrou um processo filho órfão:
```
/var/lib/grafana/plugins/alexanderzobnin-zabbix-app/datasource/gpx_zabbix-datasource_linux_amd64
```
**Solução:** `sudo kill -9 <pid>` (identificado via `ps aux | grep grafana`).

### Resultado
- 1.028 GB de disco liberado
- ~450 MB de RAM liberada
- Processo plugin Zabbix órfão eliminado
- Porta 3000 do host: livre
- Único Grafana ativo: Docker (`itgov-grafana`)
- Acesso oficial: `http://172.29.2.11:8090` (via Nginx, redireciona para HTTPS :8453)

### Lições Aprendidas
1. **Auditar serviços nativos antes de containerizar:**
   ```bash
   systemctl list-units --type=service --state=running | grep -iE "(grafana|influx|prometheus|zabbix)"
   ss -tlnp | grep -E ':(3000|8086|9090)\s'
   ```
2. **Após remover serviço, checar processos filhos órfãos:**
   ```bash
   ps aux | grep -i <servico> | grep -v grep
   ```
3. **Testes HTTP devem seguir redirects (-L) ou ir direto no HTTPS:**
   ```bash
   curl -sL http://host:porta/api/...     # segue redirects
   curl -sk https://host:8453/api/...     # vai direto no HTTPS
   ```
