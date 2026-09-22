# 🏛️ IT Governance Dashboard

> Dashboard completo para governança de TI com métricas baseadas em **COBIT**, **ITIL v4** e **ISO/IEC 27001**.

![Status](https://img.shields.io/badge/status-active-success)
![License](https://img.shields.io/badge/license-MIT-blue)
[![CI](https://github.com/robertoandr/it-governance-dashboard/actions/workflows/ci.yml/badge.svg)](https://github.com/robertoandr/it-governance-dashboard/actions/workflows/ci.yml)
[![Docker](https://github.com/robertoandr/it-governance-dashboard/actions/workflows/docker.yml/badge.svg)](https://github.com/robertoandr/it-governance-dashboard/actions/workflows/docker.yml)
[![GHCR](https://img.shields.io/badge/ghcr.io-latest-blue?logo=github)](https://github.com/robertoandr/it-governance-dashboard/pkgs/container/it-governance-dashboard)

---

## 📸 Preview

> _Adicione um screenshot aqui após o deploy_

---

## 🎯 Sobre o Projeto

Plataforma de monitoramento e governança de TI que centraliza indicadores estratégicos, táticos e operacionais, permitindo a gestão executiva visualizar em tempo real:

- **Conformidade** com frameworks de governança
- **Performance** de serviços de TI (SLA/OLA)
- **Riscos** e incidentes de segurança
- **Investimentos** e ROI de iniciativas tecnológicas

---

## 🏛️ Frameworks Implementados

| Framework | Domínios Cobertos |
|-----------|-------------------|
| **COBIT 2019** | EDM, APO, BAI, DSS, MEA |
| **ITIL v4** | Service Strategy, Design, Operation |
| **ISO 27001** | Controles A.5 a A.18 |
| **NIST CSF** | Identify, Protect, Detect, Respond, Recover |

---

## 📊 KPIs Disponíveis

- ✅ Disponibilidade de Serviços (Uptime %)
- ✅ MTTR (Mean Time to Repair)
- ✅ MTBF (Mean Time Between Failures)
- ✅ Taxa de Cumprimento de SLA
- ✅ Incidentes por Severidade
- ✅ Vulnerabilidades Críticas Abertas
- ✅ Aderência a Políticas
- ✅ Custos de TI vs. Orçamento

---

## 🛠️ Stack Tecnológica

- **Frontend:** React + TypeScript + Vite
- **UI:** TailwindCSS + Shadcn/UI
- **Gráficos:** Recharts / Chart.js
- **Estado:** Zustand / Redux Toolkit
- **Ícones:** Lucide React

---

## 🚀 Como Executar Localmente

### Pré-requisitos
- Node.js 18+
- npm ou pnpm

### Passos

```bash
# 1. Clone o repositório
git clone https://github.com/robertoandr/it-governance-dashboard.git
cd it-governance-dashboard

# 2. Instale as dependências
npm install

# 3. Configure variáveis de ambiente
cp .env.example .env

# 4. Inicie o servidor de desenvolvimento
npm run dev
```

---

## 🛟 Backup e Recuperação de Desastre

Backup diário automático (systemd timer `governanca-ti-coleta.timer`, ~00:00 UTC ± 10min) dos 3
armazenamentos reais do sistema — nenhum deles é git, todos são perdidos se a VM cair:

- **Postgres do Zabbix** (container `zabbix_db`) — `pg_dump` compactado
- **`app.db`** (SQLite — usuários/autenticação)
- **`govti.db`** (SQLite — vendors/contracts/assets/governança)

Retenção local de 7 dias (`scripts/backup_governanca.sh`); cada dump também sobe para o remote
configurado em `RCLONE_REMOTE` no `.env` (OneDrive) via `scripts/sync_cloud.sh`. Logs em
`logs/backup_external.log` (sync) e `/var/log/governanca-ti/coleta.log` (coleta + backup) ou
`journalctl -u governanca-ti-coleta.service`.

### Recuperação de Desastre

Se a VM for perdida, o estado mais recente de cada base está no remote `RCLONE_REMOTE` (ver `.env`).
Passos para restaurar em um servidor novo:

1. Instale o rclone e reautentique o mesmo remote (`rclone config reconnect gov-onedrive:`, ou
   `rclone config` do zero seguindo o fluxo de autorização) — o `rclone.conf` **não** está no
   repositório (contém token OAuth) e não sobrevive à perda da VM.

2. Baixe os backups mais recentes:
   ```bash
   rclone lsl gov-onedrive:Backups/172.29.2.11 | sort -k2 | tail -n3   # confirma os mais novos
   rclone copy gov-onedrive:Backups/172.29.2.11/zabbix_<timestamp>.sql.gz .
   rclone copy gov-onedrive:Backups/172.29.2.11/app_<timestamp>.db.gz .
   rclone copy gov-onedrive:Backups/172.29.2.11/govti_<timestamp>.db.gz .
   ```

3. Suba a stack vazia (`docker compose up -d zabbix-db app`) e restaure:

   **Postgres (Zabbix):**
   ```bash
   gunzip -c zabbix_<timestamp>.sql.gz | \
     docker exec -i -e PGPASSWORD="$ZABBIX_DB_PASSWORD" zabbix_db psql -U zabbix -d zabbix
   ```

   **SQLite (app.db / govti.db):**
   ```bash
   gunzip -c app_<timestamp>.db.gz > app.db
   gunzip -c govti_<timestamp>.db.gz > govti.db
   docker cp app.db itgov-app:/app/data/app.db
   docker cp govti.db itgov-app:/app/data/govti.db
   docker compose up -d --force-recreate app   # reabre os arquivos copiados
   ```

4. Confirme a integridade (login na aplicação, contagem de linhas em tabelas-chave) antes de
   apontar produção para o servidor novo.

**Ponto de atenção:** a retenção de 7 dias só vale para o disco local — o OneDrive guarda tudo que
já foi enviado; revise o volume do remote periodicamente. O Zabbix (`zabbix_db`/`zabbix_server`/
`zabbix_web`) roda no mesmo `docker-compose.yml` mas hoje está "órfão" das definições atuais do
arquivo (containers sobreviventes de uma versão anterior) — recapturar esses serviços no compose é
um follow-up pendente, sem o qual `docker compose up` num servidor novo não recria o Zabbix sozinho.
