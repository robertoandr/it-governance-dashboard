-- ═══════════════════════════════════════════════════════════════
-- IT Governance Dashboard - Schema Inicial
-- TimescaleDB 2.27 + PostGIS 3.4 + PostgreSQL 16
-- ═══════════════════════════════════════════════════════════════

SET client_min_messages TO WARNING;

-- ─── Schemas lógicos ──────────────────────────────────────────
CREATE SCHEMA IF NOT EXISTS cmdb;       -- Configuration Management DB
CREATE SCHEMA IF NOT EXISTS metrics;    -- Time-series data
CREATE SCHEMA IF NOT EXISTS audit;      -- Audit logs

COMMENT ON SCHEMA cmdb    IS 'Assets, contratos, usuários (relacional)';
COMMENT ON SCHEMA metrics IS 'Métricas time-series (hypertables)';
COMMENT ON SCHEMA audit   IS 'Logs de auditoria e mudanças';

-- ═══════════════════════════════════════════════════════════════
-- 1) CMDB — Tabelas relacionais
-- ═══════════════════════════════════════════════════════════════

-- Sources/Integrações (GitHub, Zabbix, Zendesk, etc.)
CREATE TABLE IF NOT EXISTS cmdb.sources (
    id           SMALLSERIAL PRIMARY KEY,
    name         TEXT NOT NULL UNIQUE,
    kind         TEXT NOT NULL CHECK (kind IN ('github','zabbix','zendesk','graph','custom')),
    base_url     TEXT,
    enabled      BOOLEAN NOT NULL DEFAULT TRUE,
    config       JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Repositórios GitHub
CREATE TABLE IF NOT EXISTS cmdb.repos (
    id              BIGSERIAL PRIMARY KEY,
    github_id       BIGINT UNIQUE,
    owner           TEXT NOT NULL,
    name            TEXT NOT NULL,
    full_name       TEXT GENERATED ALWAYS AS (owner || '/' || name) STORED,
    private         BOOLEAN NOT NULL DEFAULT FALSE,
    default_branch  TEXT DEFAULT 'main',
    language        TEXT,
    topics          TEXT[],
    archived        BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(owner, name)
);
CREATE INDEX IF NOT EXISTS idx_repos_owner ON cmdb.repos(owner) WHERE archived = FALSE;

-- Hosts/Servidores monitorados (Zabbix)
CREATE TABLE IF NOT EXISTS cmdb.hosts (
    id             BIGSERIAL PRIMARY KEY,
    zabbix_hostid  BIGINT UNIQUE,
    hostname       TEXT NOT NULL,
    visible_name   TEXT,
    ip_address     INET,
    location       GEOGRAPHY(POINT, 4326),  -- PostGIS: lat/lon do datacenter
    environment    TEXT CHECK (environment IN ('prod','staging','dev','dr')),
    os             TEXT,
    tags           JSONB DEFAULT '{}'::jsonb,
    enabled        BOOLEAN NOT NULL DEFAULT TRUE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_hosts_env ON cmdb.hosts(environment) WHERE enabled = TRUE;
CREATE INDEX IF NOT EXISTS idx_hosts_location ON cmdb.hosts USING GIST(location);

-- Tickets de suporte (snapshot mais recente — histórico vai em metrics)
CREATE TABLE IF NOT EXISTS cmdb.tickets (
    id              BIGSERIAL PRIMARY KEY,
    zendesk_id      BIGINT UNIQUE NOT NULL,
    subject         TEXT NOT NULL,
    status          TEXT NOT NULL,
    priority        TEXT,
    requester       TEXT,
    assignee        TEXT,
    tags            TEXT[],
    created_at      TIMESTAMPTZ NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL,
    solved_at       TIMESTAMPTZ,
    first_response_minutes INTEGER,
    resolution_minutes     INTEGER
);
CREATE INDEX IF NOT EXISTS idx_tickets_status ON cmdb.tickets(status);
CREATE INDEX IF NOT EXISTS idx_tickets_updated ON cmdb.tickets(updated_at DESC);

-- ═══════════════════════════════════════════════════════════════
-- 2) METRICS — Hypertables (TimescaleDB)
-- ═══════════════════════════════════════════════════════════════

-- ── GitHub: Pull Requests events ──────────────────────────────
CREATE TABLE IF NOT EXISTS metrics.github_pr_events (
    time          TIMESTAMPTZ NOT NULL,
    repo_id       BIGINT NOT NULL,
    pr_number     INTEGER NOT NULL,
    event         TEXT NOT NULL,  -- opened, closed, merged, review_requested, etc.
    author        TEXT,
    state         TEXT,
    additions     INTEGER,
    deletions     INTEGER,
    files_changed INTEGER,
    labels        TEXT[],
    raw           JSONB
);
SELECT create_hypertable('metrics.github_pr_events', 'time',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS idx_pr_repo_time ON metrics.github_pr_events(repo_id, time DESC);

-- ── GitHub: Workflow runs (Actions) ───────────────────────────
CREATE TABLE IF NOT EXISTS metrics.github_workflow_runs (
    time         TIMESTAMPTZ NOT NULL,
    repo_id      BIGINT NOT NULL,
    workflow     TEXT NOT NULL,
    run_id       BIGINT NOT NULL,
    status       TEXT NOT NULL,   -- queued, in_progress, completed
    conclusion   TEXT,             -- success, failure, cancelled, skipped
    duration_s   INTEGER,
    branch       TEXT,
    actor        TEXT,
    raw          JSONB
);
SELECT create_hypertable('metrics.github_workflow_runs', 'time',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS idx_wf_repo_time ON metrics.github_workflow_runs(repo_id, time DESC);
CREATE INDEX IF NOT EXISTS idx_wf_conclusion ON metrics.github_workflow_runs(conclusion, time DESC);

-- ── GitHub: Security alerts (CodeQL + Dependabot) ─────────────
CREATE TABLE IF NOT EXISTS metrics.github_security_alerts (
    time         TIMESTAMPTZ NOT NULL,
    repo_id      BIGINT NOT NULL,
    source       TEXT NOT NULL CHECK (source IN ('codeql','dependabot','secret_scanning')),
    alert_number INTEGER NOT NULL,
    severity     TEXT NOT NULL CHECK (severity IN ('low','medium','high','critical')),
    state        TEXT NOT NULL,  -- open, fixed, dismissed
    rule         TEXT,
    package      TEXT,
    raw          JSONB
);
SELECT create_hypertable('metrics.github_security_alerts', 'time',
    chunk_time_interval => INTERVAL '30 days',
    if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS idx_sec_severity ON metrics.github_security_alerts(severity, state, time DESC);

-- ── Zabbix: Eventos/incidentes ────────────────────────────────
CREATE TABLE IF NOT EXISTS metrics.zabbix_events (
    time         TIMESTAMPTZ NOT NULL,
    host_id      BIGINT NOT NULL,
    eventid      BIGINT NOT NULL,
    severity     SMALLINT NOT NULL,  -- 0..5 (Zabbix scale)
    trigger_name TEXT,
    value        SMALLINT,           -- 0 ok, 1 problem
    acknowledged BOOLEAN DEFAULT FALSE,
    duration_s   INTEGER,
    raw          JSONB
);
SELECT create_hypertable('metrics.zabbix_events', 'time',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS idx_zbx_host_time ON metrics.zabbix_events(host_id, time DESC);
CREATE INDEX IF NOT EXISTS idx_zbx_severity ON metrics.zabbix_events(severity, time DESC);

-- ── Zabbix: Métricas numéricas (CPU, RAM, disk, etc.) ─────────
CREATE TABLE IF NOT EXISTS metrics.zabbix_metrics (
    time     TIMESTAMPTZ NOT NULL,
    host_id  BIGINT NOT NULL,
    itemid   BIGINT NOT NULL,
    metric   TEXT NOT NULL,    -- cpu.util, mem.used, disk.free, etc.
    value    DOUBLE PRECISION NOT NULL,
    unit     TEXT
);
SELECT create_hypertable('metrics.zabbix_metrics', 'time',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS idx_zbx_metric_host_time ON metrics.zabbix_metrics(metric, host_id, time DESC);

-- ── Zendesk: Eventos de ticket ────────────────────────────────
CREATE TABLE IF NOT EXISTS metrics.zendesk_events (
    time         TIMESTAMPTZ NOT NULL,
    ticket_id    BIGINT NOT NULL,
    event        TEXT NOT NULL,    -- created, updated, solved, reopened, escalated
    status_from  TEXT,
    status_to    TEXT,
    actor        TEXT,
    raw          JSONB
);
SELECT create_hypertable('metrics.zendesk_events', 'time',
    chunk_time_interval => INTERVAL '14 days',
    if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS idx_zd_ticket_time ON metrics.zendesk_events(ticket_id, time DESC);

-- ═══════════════════════════════════════════════════════════════
-- 3) AUDIT — Mudanças e logs operacionais
-- ═══════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS audit.changes (
    time      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actor     TEXT NOT NULL,
    action    TEXT NOT NULL,
    entity    TEXT NOT NULL,
    entity_id TEXT,
    diff      JSONB,
    metadata  JSONB DEFAULT '{}'::jsonb
);
SELECT create_hypertable('audit.changes', 'time',
    chunk_time_interval => INTERVAL '30 days',
    if_not_exists => TRUE);

-- ═══════════════════════════════════════════════════════════════
-- 4) COMPRESSION + RETENTION POLICIES
-- ═══════════════════════════════════════════════════════════════

-- Compression: comprime chunks > 7 dias
ALTER TABLE metrics.github_pr_events       SET (timescaledb.compress, timescaledb.compress_segmentby = 'repo_id');
ALTER TABLE metrics.github_workflow_runs   SET (timescaledb.compress, timescaledb.compress_segmentby = 'repo_id');
ALTER TABLE metrics.github_security_alerts SET (timescaledb.compress, timescaledb.compress_segmentby = 'repo_id');
ALTER TABLE metrics.zabbix_events          SET (timescaledb.compress, timescaledb.compress_segmentby = 'host_id');
ALTER TABLE metrics.zabbix_metrics         SET (timescaledb.compress, timescaledb.compress_segmentby = 'host_id, metric');
ALTER TABLE metrics.zendesk_events         SET (timescaledb.compress, timescaledb.compress_segmentby = 'ticket_id');

SELECT add_compression_policy('metrics.github_pr_events',       INTERVAL '7 days',  if_not_exists => TRUE);
SELECT add_compression_policy('metrics.github_workflow_runs',   INTERVAL '7 days',  if_not_exists => TRUE);
SELECT add_compression_policy('metrics.github_security_alerts', INTERVAL '30 days', if_not_exists => TRUE);
SELECT add_compression_policy('metrics.zabbix_events',          INTERVAL '7 days',  if_not_exists => TRUE);
SELECT add_compression_policy('metrics.zabbix_metrics',         INTERVAL '2 days',  if_not_exists => TRUE);
SELECT add_compression_policy('metrics.zendesk_events',         INTERVAL '14 days', if_not_exists => TRUE);

-- Retention: deleta dados muito antigos
SELECT add_retention_policy('metrics.zabbix_metrics',           INTERVAL '180 days', if_not_exists => TRUE);
SELECT add_retention_policy('metrics.github_workflow_runs',     INTERVAL '365 days', if_not_exists => TRUE);
SELECT add_retention_policy('audit.changes',                    INTERVAL '730 days', if_not_exists => TRUE);

-- ═══════════════════════════════════════════════════════════════
-- 5) Permissões para user `itgov`
-- ═══════════════════════════════════════════════════════════════
GRANT USAGE ON SCHEMA cmdb, metrics, audit TO itgov;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA cmdb, metrics, audit TO itgov;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA cmdb, metrics, audit TO itgov;

ALTER DEFAULT PRIVILEGES IN SCHEMA cmdb    GRANT ALL ON TABLES TO itgov;
ALTER DEFAULT PRIVILEGES IN SCHEMA metrics GRANT ALL ON TABLES TO itgov;
ALTER DEFAULT PRIVILEGES IN SCHEMA audit   GRANT ALL ON TABLES TO itgov;
ALTER DEFAULT PRIVILEGES IN SCHEMA cmdb    GRANT ALL ON SEQUENCES TO itgov;
ALTER DEFAULT PRIVILEGES IN SCHEMA metrics GRANT ALL ON SEQUENCES TO itgov;
ALTER DEFAULT PRIVILEGES IN SCHEMA audit   GRANT ALL ON SEQUENCES TO itgov;

-- ═══════════════════════════════════════════════════════════════
SELECT '✅ Schema criado com sucesso!' AS status;
