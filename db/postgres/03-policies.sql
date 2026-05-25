SET search_path TO governance, public;

-- COMPRESSÃO
ALTER TABLE metrics SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'metric_name,source',
    timescaledb.compress_orderby = 'time DESC'
);
SELECT add_compression_policy('metrics', INTERVAL '7 days', if_not_exists => TRUE);

ALTER TABLE audit_events SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'event_type,actor',
    timescaledb.compress_orderby = 'time DESC'
);
SELECT add_compression_policy('audit_events', INTERVAL '30 days', if_not_exists => TRUE);

-- RETENÇÃO
SELECT add_retention_policy('metrics', INTERVAL '365 days', if_not_exists => TRUE);
SELECT add_retention_policy('audit_events', INTERVAL '730 days', if_not_exists => TRUE);

-- CONTINUOUS AGGREGATES
CREATE MATERIALIZED VIEW metrics_hourly
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', time) AS hour,
    metric_name,
    source,
    AVG(value_num) AS avg_val,
    MAX(value_num) AS max_val,
    MIN(value_num) AS min_val,
    COUNT(*) AS samples
FROM metrics
WHERE value_num IS NOT NULL
GROUP BY hour, metric_name, source
WITH NO DATA;

SELECT add_continuous_aggregate_policy('metrics_hourly',
    start_offset => INTERVAL '3 days',
    end_offset   => INTERVAL '1 hour',
    schedule_interval => INTERVAL '30 minutes',
    if_not_exists => TRUE);

CREATE MATERIALIZED VIEW metrics_daily
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 day', time) AS day,
    metric_name,
    source,
    AVG(value_num) AS avg_val,
    MAX(value_num) AS max_val,
    MIN(value_num) AS min_val,
    COUNT(*) AS samples
FROM metrics
WHERE value_num IS NOT NULL
GROUP BY day, metric_name, source
WITH NO DATA;

SELECT add_continuous_aggregate_policy('metrics_daily',
    start_offset => INTERVAL '30 days',
    end_offset   => INTERVAL '1 day',
    schedule_interval => INTERVAL '1 hour',
    if_not_exists => TRUE);
