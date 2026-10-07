# Backup externo e restauração

Como o servidor `itgov-dev` (VM Hyper-V, 172.29.2.11) é copiado para fora, e como trazer tudo de volta
se a VM for perdida.

## O que é copiado, e para onde

O timer `governanca-ti-coleta` roda `scripts/backup_governanca.sh` todo dia (~00:05 UTC). Cada item vai
para `backups/` na instância e, em seguida, `scripts/sync_cloud.sh` sobe tudo para o OneDrive
(`RCLONE_REMOTE`, hoje `gov-onedrive:Backups/172.29.2.11`).

| Arquivo | Conteúdo | Criptografado |
|---|---|---|
| `zabbix_*.sql.gz` | Postgres do Zabbix (`pg_dump`) | não |
| `app_*.db.gz`, `govti_*.db.gz` | SQLite do dashboard (usuários, aprovações, unidades, links…) | não |
| `influxdb_*.tar.gz` | InfluxDB inteiro (`influx backup`), inclui o histórico de métricas | não |
| `mapacameras_*.tar.gz` | `/var/lib/mapa-cameras` (câmeras, fotos, plantas, usuários do mapa) | não |
| `compartilhado_*.tar.gz` | `/srv/compartilhado` (código-fonte do MapaCameras, plantas, instaladores) | não |
| `config_*.tar.gz.gpg` | `.env` do projeto, `docker-compose.override.yml`, certificados (`docker/nginx/certs`, `~/.acme.sh`), `rclone.conf`, Evolution (`.env`, compose), OmniRoute (scripts, `client.key`, proxy e o último backup do banco), units do systemd, `/opt/mapa-cameras` e o crontab | **sim** (gpg AES-256) |

Retenção: **7 dias** em `backups/` e **30 dias** no OneDrive (`REMOTE_RETENTION_DAYS`; a limpeza do remoto
só roda depois de um upload bem-sucedido). Log do sync: `logs/backup_external.log`.

## A senha do pacote de configuração

- Fica em `~/.config/itgov-backup/passphrase` (modo 600, usuário `zabbix`). Sem esse arquivo o
  `backup_config.sh` **não gera** o pacote — nada sobe em claro.
- **Guarde uma cópia fora do servidor** (cofre de senhas da TI). Se a VM for perdida, a cópia local vai
  junto e o pacote não abre sem ela.
- Ver a senha para guardar no cofre: `cat ~/.config/itgov-backup/passphrase`.

## Teste de restauração

```bash
scripts/testar_restauracao.sh           # baixa do OneDrive (prova a cópia externa)
scripts/testar_restauracao.sh --local   # usa backups/ da instância
```

Restaura o arquivo mais recente de cada tipo em containers descartáveis sem rede (Postgres 16 e
InfluxDB 2.7), confere integridade do SQLite, valida o `cameras.json` e decifra o pacote de
configuração. Não toca a produção. Sai com 0 só se tudo passar; resultado em `logs/restore_test.log`.
Leva ~6 minutos (quase tudo é o restore do Zabbix). Rodar **uma vez por mês** e depois de mudanças no
backup.

### Registro

| Data | Origem | Resultado |
|---|---|---|
| 07/10/2026 | OneDrive | **OK, 7 de 7.** Zabbix: 713 hosts, última coleta 07/10 15:44 UTC (restore em 280 s). app.db: integridade ok, 13 tabelas. govti.db: ok, 10 tabelas. InfluxDB: `governance_raw` restaurado, 464.932 pontos nos últimos 7 dias. MapaCameras: `cameras.json` válido. Pasta compartilhada: 257 arquivos. Configuração: decifrada, 335 arquivos, `.env` presente. |

## Recuperação de desastre: a VM foi perdida

O backup acima cobre **dados e configuração**. A recuperação mais rápida continua sendo um backup da VM
inteira no Hyper-V (export/replica ou Acronis), responsabilidade da infra. Sem ele, reconstrua assim:

1. **Servidor novo**: Ubuntu 24.04, usuário `zabbix`, Docker + compose plugin, `rclone`, `gpg`.
2. **Acesso ao OneDrive**: `rclone config` para recriar o remoto `gov-onedrive` (o `rclone.conf` antigo
   está dentro do pacote criptografado, mas é preciso acesso para baixá-lo primeiro).
3. **Baixar o dia mais recente**:
   ```bash
   mkdir -p ~/restore && rclone copy gov-onedrive:Backups/172.29.2.11 ~/restore --max-age 2d
   ```
4. **Abrir o pacote de configuração** (senha do cofre em `~/senha`, modo 600) e devolver os arquivos aos
   caminhos originais:
   ```bash
   gpg --batch --pinentry-mode loopback --passphrase-file ~/senha -d ~/restore/config_*.tar.gz.gpg > ~/restore/config.tar.gz
   sudo tar xzf ~/restore/config.tar.gz -C /     # extra/crontab.txt fica em /extra → crontab /extra/crontab.txt
   ```
5. **Código**: `git clone https://github.com/robertoandr/it-governance-dashboard.git /home/zabbix/projects/it-governance-dashboard`
   (o `.env` e o override já voltaram no passo 4) e `docker compose up -d` para criar volumes e containers.
6. **Zabbix**: `docker compose stop zabbix_server`, depois
   `gunzip -c ~/restore/zabbix_*.sql.gz | docker exec -i zabbix_db psql -U zabbix -d zabbix` num banco vazio,
   e `docker compose start zabbix_server`.
7. **SQLite do app**: `docker compose stop app`, copiar `app_*.db.gz`/`govti_*.db.gz` descompactados para
   `/app/data/app.db` e `/app/data/govti.db` do volume (`docker cp`), `docker compose start app`.
8. **InfluxDB**: extrair `influxdb_*.tar.gz`, `docker cp` a pasta para o container e
   `influx restore /pasta --full -t <token de operador>`.
9. **MapaCameras**: binário e unit já voltaram no passo 4; `sudo tar xzf mapacameras_*.tar.gz -C /var/lib/mapa-cameras`,
   `sudo chown -R mapacameras: /var/lib/mapa-cameras`, `sudo systemctl daemon-reload && sudo systemctl enable --now mapa-cameras`.
10. **Pasta compartilhada**: `sudo tar xzf compartilhado_*.tar.gz -C /srv`.
11. **Evolution e OmniRoute**: subir com os arquivos devolvidos no passo 4 (`~/evolution`, `~/omniroute/run.sh`).
    O WhatsApp pede novo pareamento por QR code (a sessão não é copiada).
12. **Conferir**: `scripts/testar_restauracao.sh --local` e abrir o dashboard.
