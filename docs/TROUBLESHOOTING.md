# Troubleshooting — IT Governance Dashboard

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
