CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE SCHEMA IF NOT EXISTS governance;

COMMENT ON SCHEMA governance IS 'IT Governance Dashboard - schema v2.0 (Timescale)';
