# ADR-008 — Estrategia de Inventario de Ativos de TI

**Status:** Aceita
**Data:** 2026-06-02
**Autor:** Roberto Andrade
**Issue:** #87

---

## Contexto

O IT Governance Dashboard precisa fornecer visibilidade e governanca sobre
ativos de TI (servidores, switches, aplicacoes, licencas, endpoints) gerenciados
pela equipe do Grupo Gadens. Hoje, esse inventario existe disperso em:

- Planilhas Excel descentralizadas (sem controle de versao)
- Zabbix (apenas infra monitorada — subset dos ativos)
- Microsoft Entra ID (apenas identidades e devices enrollados)
- Conhecimento tacito da equipe de TI

**Problemas identificados:**

- Ausencia de fonte unica da verdade para ativos
- Impossivel auditar criticidade e responsavel (owner) de forma sistematica
- Sem historico de mudancas (quem alterou, quando, o que)
- Sem KPIs agregados (ex: % ativos sem owner, distribuicao por criticidade)
- Impossivel vincular ativos a contratos de fornecedores (modulo Hero V1.0)

---

## Decisao

Implementar modulo de **Inventario de Ativos** integrado ao Dashboard com
as seguintes escolhas arquiteturais:

### 1. Modelo de Dados

Pydantic v2 model `Ativo` com campos tipados, validados e versionados.
Enums para `tipo`, `ambiente` e `criticidade` garantem consistencia e
evitam valores livres que dificultam agregacoes.

Campos obrigatorios:
- `id`: UUID4 (auto-gerado, imutavel)
- `tipo`: `Literal["servidor", "switch", "app", "licenca", "endpoint"]`
- `nome`: str (3-100 chars, unico por tipo)
- `ambiente`: `Literal["prod", "hml", "dev"]`
- `criticidade`: `Literal["alta", "media", "baixa"]`
- `owner`: EmailStr (responsavel tecnico)

Campos opcionais:
- `tags`: `list[str]` para categorizacao livre
- `metadata`: `dict[str, Any]` para atributos especificos por tipo
- `contrato_id`: FK opcional para modulo de Contratos (Hero V1.0)
- `created_at`, `updated_at`: timestamps gerenciados pelo service layer

### 2. Persistencia Hibrida

| Storage    | Papel                                      | Retencao   |
|------------|--------------------------------------------|------------|
| SQLite     | Estado atual (CRUD operacional)            | Indefinida |
| InfluxDB   | Snapshots historicos (metricas agregadas)  | 90 dias    |

Esta decisao reutiliza a infraestrutura existente (ADR-0001 — dual storage)
sem adicionar novos servicos. O SQLite armazena o estado vivo dos ativos
(criacao, atualizacao, soft delete via `deleted_at`). O InfluxDB recebe
snapshots diarios com metricas agregadas para analise historica via Grafana.

### 3. Camadas Arquiteturais

| Camada      | Issue | Responsabilidade                              |
|-------------|-------|-----------------------------------------------|
| Model       | #83   | Pydantic schemas + validacao de entrada/saida |
| Persistence | #84   | SQLite schema (Alembic) + InfluxDB bucket     |
| Service     | #85   | Logica de negocio + snapshot diario           |
| API         | #86   | REST endpoints + autorizacao por role         |
| UI          | #88   | Widget KPIs no dashboard principal            |

### 4. Estrategia de Snapshot

- Job agendado (APScheduler, Sprint 13) dispara diariamente as 02:00 BRT
- Metricas gravadas no bucket InfluxDB `inventory`:
  - `ativos.total` por tipo e ambiente
  - `ativos.sem_owner` (contagem de ativos com owner ausente)
  - `ativos.por_criticidade` (distribuicao alta/media/baixa)
- Retencao: 90 dias (configuravel via `INFLUX_INVENTORY_RETENTION_DAYS`)
- Idempotente: snapshots com mesmo timestamp sao sobrescritos (InfluxDB line protocol)

### 5. Autenticacao e Autorizacao

Reutiliza SSO Entra ID ja implementado:

| Operacao          | Role requerida     |
|-------------------|--------------------|
| GET /ativos       | qualquer autenticado |
| GET /ativos/{id}  | qualquer autenticado |
| GET /ativos/stats | qualquer autenticado |
| POST /ativos      | `admin` ou `ops`   |
| PUT /ativos/{id}  | `admin` ou `ops`   |
| DELETE /ativos/{id} | `admin`          |

### 6. Soft Delete

Ativos nao sao removidos fisicamente. O campo `deleted_at: datetime | None`
marca a remocao logica. APIs de listagem filtram `deleted_at IS NULL` por
padrao. Conforme ADR-0003 (LGPD), dados com PII em `metadata` podem ser
anonimizados via endpoint separado.

---

## Diagrama de Fluxo

```mermaid
flowchart LR
    UI[Widget Dashboard #88] -->|GET /stats| API[/api/v1/ativos #86]
    API --> SVC[AtivoService #85]
    SVC -->|CRUD| SQLite[(SQLite ativos)]
    SVC -->|Snapshot diario| Influx[(InfluxDB inventory)]

    Admin[Admin / Ops] -->|POST PUT DELETE| API
    Viewer[Viewer] -->|GET| API

    subgraph Persistence [Persistencia #84]
        SQLite
        Influx
    end
```

---

## Consequencias

### Positivas

- Fonte unica da verdade para ativos de TI, com historico auditavel
- Type safety via Pydantic v2 elimina erros de validacao em runtime
- Reutiliza infraestrutura existente (SQLite + InfluxDB, ADR-0001)
- Soft delete preserva historico e facilita LGPD (ADR-0003)
- Vinculo opcional com contratos fecha o loop do modulo Hero V1.0

### Negativas

- Duplicacao controlada de dados (estado no SQLite + metricas no InfluxDB)
  — mitigada por job idempotente e retencao limitada a 90d no InfluxDB
- Migracao inicial de planilhas existentes e esforco unico estimado em 4h
- Necessidade de governanca de owners (processo humano, nao tecnico)

---

## Alternativas Consideradas

| Alternativa              | Motivo da Rejeicao                                            |
|--------------------------|---------------------------------------------------------------|
| PostgreSQL puro          | Overhead operacional sem beneficio — joins complexos nao necessarios |
| MongoDB                  | Schema flexivel demais, perde validacao forte do Pydantic     |
| CMDB externo (ServiceNow)| Custo de licenca, lock-in, indisponivel no contexto atual     |
| Manter planilhas Excel   | Status quo e o problema que estamos resolvendo                |
| TimescaleDB              | Supersedido por ADR-0004 — hibrido SQLite+InfluxDB preferido  |

---

## Relacao com ADRs Existentes

- **ADR-0001** (Dual Storage): esta ADR aplica o mesmo padrao ao modulo Ativos
- **ADR-0003** (LGPD): soft delete e anonimizacao de PII em `metadata`
- **ADR-001** (Coverage): modulo Ativos deve ter cobertura >= 90%
- **ADR-002** (Verification Rigor): checklist obrigatorio em todos os PRs #83-#88

---

## Historico de Revisoes

| Data       | Autor           | Alteracao              |
|------------|-----------------|------------------------|
| 2026-06-02 | Roberto Andrade | Versao inicial aceita  |
