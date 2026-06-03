# ADR-0001: Dual Storage — SQLite + InfluxDB

**Status:** Superseded by ADR-0004
**Date:** 2026-01-10
**Author:** Roberto Andrade

---

## Context

The IT Governance Dashboard needs to persist two fundamentally different kinds of data:

1. **Relational data** — vendors (fornecedores), contracts (contratos), RBAC assignments, and other structured entities with foreign-key relationships.
2. **Time-series data** — contract events, SLA breach records, metric snapshots, and audit trails with timestamps as the primary access dimension.

At project inception, the team evaluated a dual-storage approach: SQLite for relational data (lightweight, zero-ops) and InfluxDB v2 (containerized) for time-series data. This kept the operational surface minimal during early development and allowed each store to be optimized for its access pattern.

InfluxDB v2 was already in use on the infrastructure monitoring stack (Zabbix metrics forwarding), making it a familiar choice. SQLite was chosen over PostgreSQL to avoid running a database server locally and to simplify onboarding.

---

## Decision

Adopt a dual-storage architecture:

- **SQLite** (file-based, via SQLAlchemy) for relational entities.
- **InfluxDB v2** (Docker container, Flux query language) for time-series events and metrics.

Both stores are accessed through service-layer abstractions so that the presentation layer remains storage-agnostic.

---

## Consequences

### Positive
- Zero-dependency local development for relational data (SQLite file).
- InfluxDB native time-series compression and downsampling.
- Team already familiar with InfluxDB from the monitoring stack.

### Negative
- Two data stores to operate, back up, and monitor in production.
- Flux query language has a steep learning curve and limited ORM support.
- SQLite does not support concurrent writes — not suitable for multi-process production deployment.
- Cross-store joins are impossible; business logic must merge results in application code.
- InfluxDB v2 Docker image adds ~200 MB to the Compose stack and requires persistent volume management.
- SQLite → PostgreSQL migration would be required before any serious production load.

---

## Alternatives Considered

| Alternative | Reason rejected |
|---|---|
| PostgreSQL only | Seemed heavyweight for early prototyping |
| PostgreSQL + TimescaleDB | Not evaluated at this stage |
| MongoDB | No prior team experience; document model not ideal for RBAC |

---

## Superseded by

This decision was reversed in **ADR-0004** (TimescaleDB Single Storage), which consolidates both stores into PostgreSQL + TimescaleDB extension, eliminating the dual-store complexity.
