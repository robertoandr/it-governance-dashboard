-- IT Governance Dashboard — SQLite Schema
-- Idempotent: uses CREATE TABLE IF NOT EXISTS throughout.

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

-- ─── Vendors ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS vendors (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE,
    category    TEXT    NOT NULL DEFAULT 'general',
    contact     TEXT,
    website     TEXT,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ─── Contracts ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS contracts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    vendor_id   INTEGER NOT NULL REFERENCES vendors(id) ON DELETE CASCADE,
    title       TEXT    NOT NULL,
    value       REAL    NOT NULL DEFAULT 0,
    currency    TEXT    NOT NULL DEFAULT 'BRL',
    start_date  TEXT    NOT NULL,
    end_date    TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'active'
                CHECK(status IN ('active','expired','cancelled')),
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ─── Assets ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS assets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    type        TEXT    NOT NULL DEFAULT 'server'
                CHECK(type IN ('server','network','storage','endpoint','cloud','software','other')),
    ip_address  TEXT,
    location    TEXT,
    vendor_id   INTEGER REFERENCES vendors(id) ON DELETE SET NULL,
    status      TEXT    NOT NULL DEFAULT 'active'
                CHECK(status IN ('active','inactive','maintenance','decommissioned')),
    acquired_at TEXT,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ─── M365 Pillars ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS m365_pillars (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE,
    score       REAL    NOT NULL DEFAULT 0 CHECK(score >= 0 AND score <= 100),
    max_score   REAL    NOT NULL DEFAULT 100,
    checked_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ─── Checklist Runs ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS checklist_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    pillar      TEXT    NOT NULL,
    total       INTEGER NOT NULL DEFAULT 0,
    passed      INTEGER NOT NULL DEFAULT 0,
    failed      INTEGER NOT NULL DEFAULT 0,
    run_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS checklist_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER NOT NULL REFERENCES checklist_runs(id) ON DELETE CASCADE,
    title       TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'pass' CHECK(status IN ('pass','fail','warn')),
    detail      TEXT
);

-- ─── LGPD ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS lgpd_processings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL,
    legal_basis     TEXT    NOT NULL,
    data_categories TEXT    NOT NULL,
    retention_days  INTEGER NOT NULL DEFAULT 365,
    controller      TEXT    NOT NULL,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS lgpd_incidents (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    title           TEXT    NOT NULL,
    severity        TEXT    NOT NULL DEFAULT 'medium'
                    CHECK(severity IN ('low','medium','high','critical')),
    reported_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    resolved_at     TEXT,
    description     TEXT
);

-- ─── Governance Scores History ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS governance_scores_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    global_score    REAL    NOT NULL,
    status          TEXT    NOT NULL,
    strategic_alignment   REAL NOT NULL DEFAULT 0,
    value_delivery        REAL NOT NULL DEFAULT 0,
    risk_management       REAL NOT NULL DEFAULT 0,
    resource_management   REAL NOT NULL DEFAULT 0,
    performance_measure   REAL NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_governance_history_date
    ON governance_scores_history(recorded_at DESC);
