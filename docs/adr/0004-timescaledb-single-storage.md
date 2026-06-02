> **STATUS: SUPERSEDED** (em 02/06/2026, durante planejamento V1.1)
>
> Esta ADR foi substituida pela ADR-0001 (dual storage SQLite + InfluxDB).
>
> Motivo: Estrategia hibrida (PostgreSQL CRUD + InfluxDB time-series) provou-se mais
> eficiente que TimescaleDB unificado, dado que InfluxDB ja estava em producao para
> metricas Zabbix. A migracao para TimescaleDB nao foi executada e a ADR-0001 permanece
> como decisao vigente de persistencia.
>
> Mantido aqui apenas para registro historico.

---

# ADR-0004: TimescaleDB Single Storage (PostgreSQL + Extension)

**Status:** Accepted  
**Date:** 2026-02-05  
**Author:** Roberto Andrade

---

## Context

ADR-0001 established a dual-storage architecture: SQLite for relational data and InfluxDB v2 for time-series events. After the initial prototype sprint (Sprint 7-8), the team identified significant friction with this approach:

1. **Operational complexity** — running two separate stores (SQLite file + InfluxDB Docker container) with different backup, monitoring, and migration strategies doubled the ops burden.
2. **Cross-store joins** — business logic that correlates contract events with vendor status required merging result sets in Python, adding latency and complexity.
3. **SQLite concurrency** — SQLite's single-writer model caused lock contention under the async Flask workers even in development.
4. **InfluxDB Flux** — the Flux query language has poor Python ORM support; all queries were raw strings, complicating testing and refactoring.
5. **LGPD erasure** (see ADR-0003) — InfluxDB does not support row-level deletion, making right-to-erasure on event payloads architecturally difficult.
6. **TimescaleDB discovery** — the team evaluated TimescaleDB and found that PostgreSQL + `timescaledb` extension satisfies both use cases:
   - Full SQL for relational entities (replacing SQLite).
   - Hypertables with time-based partitioning and native compression for time-series events (replacing InfluxDB).

This decision supersedes ADR-0001.

---

## Decision

**Consolidate all storage into a single PostgreSQL instance with the TimescaleDB extension.**

- Drop SQLite and InfluxDB from the architecture entirely.
- All relational tables (`fornecedores`, `contratos`, RBAC, etc.) live in PostgreSQL.
- Time-series event tables (`contratos_eventos`, future metric snapshots) are created as TimescaleDB hypertables.
- A single `DATABASE_URL` environment variable (PostgreSQL DSN) replaces `SQLITE_PATH` and `INFLUXDB_*` variables.
- SQLAlchemy (async, via `asyncpg`) is used for all database access.
- Alembic manages schema migrations; raw SQL migration files are kept in `db/migrations/` for auditability.

### Deployment topology

| Environment | Database |
|---|---|
| Development (local) | PostgreSQL 16 + TimescaleDB via Docker Compose |
| CI | PostgreSQL 16 + TimescaleDB (GitHub Actions service container) |
| Production | Managed PostgreSQL (Aiven / RDS) + TimescaleDB extension enabled |

### Why not InfluxDB Cloud?

The managed InfluxDB Cloud offering was evaluated. It does not resolve the Flux-ORM gap, the LGPD erasure limitation, or the cross-store join problem. The cost per metric ingestion also exceeds TimescaleDB at projected volume.

### Why not separate PostgreSQL + InfluxDB?

Keeps the dual-store complexity without the SQLite-concurrency fix. The primary motivation for this ADR is simplification, not just upgrading one store.

---

## Consequences

### Positive
- Single database connection string; dramatically simplified configuration and secrets management.
- Full SQL on all data — cross-store joins become standard JOINs.
- TimescaleDB hypertables provide native time-series compression, continuous aggregates, and data retention policies.
- SQLAlchemy ORM covers all entities; no raw Flux strings.
- LGPD-compliant `DELETE`/`UPDATE` on event payloads (see ADR-0003).
- PostgreSQL's MVCC eliminates SQLite write-lock contention.
- Single backup target; point-in-time recovery available via WAL.

### Negative
- PostgreSQL is a server process — local development now requires Docker (minimal: `docker compose up db`).
- TimescaleDB extension must be enabled on managed database providers — not universally supported (e.g., AWS RDS does not support TimescaleDB; Aiven does).
- Existing InfluxDB-based Zabbix metric forwarding is NOT affected — that integration remains on InfluxDB and is outside this dashboard's storage scope.

---

## Alternatives Considered

| Alternative | Reason rejected |
|---|---|
| PostgreSQL only (no TimescaleDB) | Loses native time-series partitioning and compression; would require manual partition management |
| InfluxDB + PostgreSQL (drop SQLite) | Fixes concurrency but keeps dual-store and LGPD problems |
| CockroachDB | Distributed SQL overkill for current scale; TimescaleDB not compatible |
| MongoDB | No relational integrity; poor fit for RBAC and contract foreign-key model |

---

## Supersedes

**ADR-0001** (Dual Storage — SQLite + InfluxDB) is superseded by this decision.
