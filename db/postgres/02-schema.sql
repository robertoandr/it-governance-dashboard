SET search_path TO governance, public;

-- Inventário de Ativos
CREATE TABLE assets (
    id              BIGSERIAL PRIMARY KEY,
    hostname        VARCHAR(255) NOT NULL UNIQUE,
    asset_type      VARCHAR(50) NOT NULL,
    environment     VARCHAR(20) NOT NULL,
    owner           VARCHAR(100),
    criticality     VARCHAR(20) DEFAULT 'medium',
    tags            JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_assets_env ON assets(environment);
CREATE INDEX idx_assets_crit ON assets(criticality);
CREATE INDEX idx_assets_tags ON assets USING GIN (tags);

-- Inventário de PATs
CREATE TABLE pats (
    id              BIGSERIAL PRIMARY KEY,
    platform        VARCHAR(50) NOT NULL,
    token_id        VARCHAR(100) NOT NULL,
    token_hint      VARCHAR(10),
    token_type      VARCHAR(20),
    owner_login     VARCHAR(100) NOT NULL,
    owner_email     VARCHAR(255),
    name            VARCHAR(255),
    scopes          JSONB,
    created_at      TIMESTAMPTZ,
    expires_at      TIMESTAMPTZ,
    last_used_at    TIMESTAMPTZ,
    status          VARCHAR(20) DEFAULT 'active',
    is_compliant    BOOLEAN GENERATED ALWAYS AS (
        expires_at IS NOT NULL
        AND expires_at <= created_at + INTERVAL '90 days'
        AND status = 'active'
    ) STORED,
    collected_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(platform, token_id)
);
CREATE INDEX idx_pats_status ON pats(status);
CREATE INDEX idx_pats_expires ON pats(expires_at);
CREATE INDEX idx_pats_compliant ON pats(is_compliant);

-- Secrets Detectados
CREATE TABLE secret_findings (
    id              BIGSERIAL PRIMARY KEY,
    scan_id         UUID NOT NULL,
    asset_id        BIGINT REFERENCES assets(id),
    file_path       TEXT NOT NULL,
    rule_id         VARCHAR(100) NOT NULL,
    severity        VARCHAR(20) NOT NULL,
    secret_hint     VARCHAR(20),
    commit_hash     VARCHAR(40),
    author          VARCHAR(255),
    detected_at     TIMESTAMPTZ DEFAULT NOW(),
    resolved_at     TIMESTAMPTZ,
    resolution      VARCHAR(50),
    mttd_minutes    INTEGER,
    mttr_minutes    INTEGER
);
CREATE INDEX idx_findings_severity ON secret_findings(severity);
CREATE INDEX idx_findings_resolved ON secret_findings(resolved_at);
CREATE INDEX idx_findings_detected ON secret_findings(detected_at DESC);

-- Incidentes
CREATE TABLE security_incidents (
    id              BIGSERIAL PRIMARY KEY,
    title           VARCHAR(255) NOT NULL,
    category        VARCHAR(50) NOT NULL,
    severity        VARCHAR(20) NOT NULL,
    status          VARCHAR(20) DEFAULT 'open',
    description     TEXT,
    detected_at     TIMESTAMPTZ NOT NULL,
    resolved_at     TIMESTAMPTZ,
    mttd_minutes    INTEGER,
    mttr_minutes    INTEGER,
    related_finding BIGINT REFERENCES secret_findings(id),
    assignee        VARCHAR(100),
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_incidents_status ON security_incidents(status);
CREATE INDEX idx_incidents_severity ON security_incidents(severity);

-- Conformidade
CREATE TABLE policy_compliance (
    id              BIGSERIAL PRIMARY KEY,
    policy_id       VARCHAR(50) NOT NULL,
    asset_id        BIGINT REFERENCES assets(id),
    check_name      VARCHAR(100) NOT NULL,
    is_compliant    BOOLEAN NOT NULL,
    details         JSONB,
    checked_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_compliance_policy ON policy_compliance(policy_id);
CREATE INDEX idx_compliance_status ON policy_compliance(is_compliant);

-- HYPERTABLE: métricas
CREATE TABLE metrics (
    time            TIMESTAMPTZ NOT NULL,
    metric_name     VARCHAR(100) NOT NULL,
    source          VARCHAR(50) NOT NULL,
    tags            JSONB DEFAULT '{}',
    value_num       DOUBLE PRECISION,
    value_str       TEXT
);
SELECT create_hypertable('metrics', 'time', chunk_time_interval => INTERVAL '7 days', if_not_exists => TRUE);
CREATE INDEX idx_metrics_name_time ON metrics (metric_name, time DESC);
CREATE INDEX idx_metrics_source_time ON metrics (source, time DESC);
CREATE INDEX idx_metrics_tags ON metrics USING GIN (tags);

-- HYPERTABLE: auditoria
CREATE TABLE audit_events (
    time            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    event_type      VARCHAR(50) NOT NULL,
    actor           VARCHAR(100),
    resource        VARCHAR(255),
    action          VARCHAR(50),
    outcome         VARCHAR(20),
    details         JSONB DEFAULT '{}'
);
SELECT create_hypertable('audit_events', 'time', chunk_time_interval => INTERVAL '30 days', if_not_exists => TRUE);
CREATE INDEX idx_audit_type_time ON audit_events (event_type, time DESC);
CREATE INDEX idx_audit_actor ON audit_events (actor, time DESC);

-- VIEWS
CREATE OR REPLACE VIEW v_security_posture AS
SELECT
    (SELECT COUNT(*) FROM secret_findings WHERE resolved_at IS NULL) AS open_findings,
    (SELECT COUNT(*) FROM secret_findings WHERE severity = 'critical' AND resolved_at IS NULL) AS critical_findings,
    (SELECT COUNT(*) FROM pats WHERE status = 'active') AS active_pats,
    (SELECT COUNT(*) FROM pats WHERE status = 'active' AND NOT is_compliant) AS non_compliant_pats,
    (SELECT COUNT(*) FROM pats WHERE expires_at < NOW() + INTERVAL '7 days' AND status = 'active') AS expiring_soon,
    (SELECT AVG(mttd_minutes) FROM secret_findings WHERE resolved_at IS NOT NULL AND detected_at > NOW() - INTERVAL '30 days') AS avg_mttd_30d,
    (SELECT AVG(mttr_minutes) FROM secret_findings WHERE resolved_at IS NOT NULL AND detected_at > NOW() - INTERVAL '30 days') AS avg_mttr_30d;

CREATE OR REPLACE VIEW v_pat_compliance AS
SELECT
    platform,
    COUNT(*) AS total,
    COUNT(*) FILTER (WHERE is_compliant) AS compliant,
    COUNT(*) FILTER (WHERE NOT is_compliant) AS non_compliant,
    ROUND(100.0 * COUNT(*) FILTER (WHERE is_compliant) / NULLIF(COUNT(*), 0), 2) AS compliance_pct
FROM pats
WHERE status = 'active'
GROUP BY platform;

-- TRIGGERS
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_assets_updated_at
    BEFORE UPDATE ON assets
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
