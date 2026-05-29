# ADR-0006: Revised Dual Storage — TimescaleDB (primary) + InfluxDB v2 (SLA metrics)

**Status:** Accepted  
**Date:** 2026-05-29  
**Deciders:** Roberto Andrade  
**Supersedes:** Partially — ADR-0004 remains valid as the source of truth for relational
  and contract-events storage; this ADR adds InfluxDB v2 back for one specific use case.

---

## Context

[ADR-0004](0004-timescaledb-single-storage.md) consolidated all storage into a single
TimescaleDB instance, explicitly dropping InfluxDB v2 ("Drop SQLite and InfluxDB from the
architecture entirely"). That decision solved the LGPD erasure problem, removed cross-store
join complexity, and simplified operations.

During Sprint 10/11 (Fornecedores + Contratos + SLA Metrics), a new requirement emerged:

**High-frequency SLA uptime sampling from contracts** — write rate of ≥ 1 point/hour/contract,
with queries that:

1. Compute rolling means over arbitrary windows (`aggregateWindow` in Flux).
2. Sort contracts by worst average uptime across a 30-day range.
3. Join the result with committed-SLA values from PostgreSQL to compute breach deltas.

### Why TimescaleDB alone is insufficient for this use case

| Concern | TimescaleDB | InfluxDB v2 |
|---------|------------|-------------|
| Write throughput for time-series | Good (SQL `INSERT`) | Excellent (line protocol, batch) |
| Window aggregation syntax | Requires `time_bucket` + SQL | Native `aggregateWindow` in Flux |
| Schemaless tags | Not supported (fixed columns) | Native (tag/field model) |
| Retention policy per measurement | Manual partition + drop | First-class `retention` on bucket |
| Existing infrastructure | Already running | Already running on server (v2.7.10) |

TimescaleDB hypertables (`contratos_eventos`) continue to cover contract **lifecycle events**
(alerts, renewals, SLA breach flags). These are low-frequency, require joins with relational
tables, and benefit from full SQL.

InfluxDB v2 covers **operational SLA metrics** (uptime %, incident count, downtime minutes)
that are high-frequency, tag-addressable, and need time-windowed aggregation.

The LGPD concern from ADR-0004 does not apply here: SLA metrics contain no PII.

---

## Decision

**Adopt a scoped dual-store strategy:**

- **TimescaleDB** (PostgreSQL 16 + extension) — canonical store for:
  - All relational entities: `fornecedores`, `contratos`.
  - Low-frequency contract lifecycle events: `contratos_eventos` (hypertable).
  - Source-of-truth for committed SLA (`contratos.sla_uptime_pct`).

- **InfluxDB v2.7** — supplementary store, limited to:
  - `measurement: sla_uptime` — high-frequency uptime telemetry.
  - Tags: `contrato_id`, `fornecedor_id`, `criticidade`.
  - Fields: `uptime_pct`, `incidentes`, `tempo_indisponivel_min`.
  - Retention: 90 days (bucket `metrics`, org `itgov`).

The dashboard endpoint (`GET /api/v1/metricas/dashboard`) joins both stores in Python
via `asyncio.gather`, keeping each store's query in its native language.

---

## Architectural constraints

1. **InfluxDB is append-only for SLA metrics** — no updates, no deletions.
   Corrections are handled by writing a new point with a corrected value.

2. **TimescaleDB remains the system of record** — any InfluxDB data can be
   reconstructed from contract definitions + external telemetry collectors.

3. **No PII in InfluxDB** — tags and fields must not contain vendor names, contact
   data, or any LGPD-regulated information. UUIDs only.

4. **Token scope** — the InfluxDB token used by the application must be scoped to
   `read+write` on the `metrics` bucket only (`itgov-dashboard-metrics-rw`).

5. **Retry + circuit pattern** — `influx_client.py` implements 3-attempt exponential
   back-off (0.5 s → 1 s → 2 s). Permanent HTTP 4xx errors are not retried.
   Transient failures fall back to an empty result — the dashboard degrades gracefully.

---

## Consequences

### Positive
- Dashboard SLA queries are ~10× faster (Flux native window vs. SQL `time_bucket`).
- Ingest path is decoupled: a write failure to InfluxDB does not affect
  the contract CRUD operations on TimescaleDB.
- 90-day retention enforced automatically; no manual partition maintenance.

### Negative
- Two stores to operate, monitor, and back up.
- The `asyncio.gather` join in the dashboard is the only cross-store operation;
  it must be maintained as contracts scale.
- Flux query strings are raw text — tested via mock, not a typed ORM.

### Neutral
- InfluxDB was already running on the server (`v2.7.10`) for a separate application;
  the incremental operational cost is one new bucket + one new scoped token.

---

## Alternatives considered

| Alternative | Rejected because |
|-------------|-----------------|
| TimescaleDB only (hypertable for SLA) | Window aggregation requires verbose SQL; tags model not native to PostgreSQL |
| InfluxDB only (move contratos there too) | No FK constraints, no ACID, schema-on-read incompatible with contract entities |
| External analytics DB (ClickHouse, QuestDB) | Ops overhead unjustified for one measurement; no existing infrastructure |
| In-memory aggregation in Flask | Not persistent; breaks on restart; scaling impossible |

---

## Implementation notes

- `app/services/influx_client.py` — async wrapper (`InfluxService`) with retry.
- `app/api/v1/metricas.py` — three endpoints: ingest, series, dashboard.
- `scripts/seed_sla_metrics.py` — synthetic 30-day seed for development.
- `docker/docker-compose.yml` — InfluxDB 2.7 service with automated init.
- See `TECH_DEBT.md` TD-001 for the planned cursor-based pagination when
  contract count exceeds 10k.

---

## Related ADRs

- [ADR-0001](0001-dual-storage-sqlite-influxdb.md) — original dual-store (superseded by 0004)
- [ADR-0004](0004-timescaledb-single-storage.md) — TimescaleDB consolidation (still valid for relational/events)
- [ADR-0003](0003-lgpd-compliance.md) — LGPD erasure constraints (not applicable to SLA metrics)
