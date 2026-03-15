-- 003_scaling.sql — Security & Scaling schema additions

-- ── API Keys ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS api_keys (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                  TEXT NOT NULL,
    key_hash              TEXT NOT NULL UNIQUE,   -- SHA-256(raw_key), hex string
    is_active             BOOLEAN NOT NULL DEFAULT TRUE,
    rate_limit_per_minute INT NOT NULL DEFAULT 60,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at          TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_api_keys_hash
    ON api_keys(key_hash) WHERE is_active = TRUE;

-- ── Health alert state (replaces in-process global variable) ─────────────────
ALTER TABLE system_metadata
    ADD COLUMN IF NOT EXISTS last_slack_alert_status TEXT NOT NULL DEFAULT 'healthy';

-- ── Partitioned tool_invocation_log ──────────────────────────────────────────
-- Step 1: Rename legacy (unpartitioned) table if it exists and is not yet partitioned
DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_name = 'tool_invocation_log'
          AND table_schema = 'public'
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_partitioned_table pt
        JOIN pg_class c ON pt.partrelid = c.oid
        WHERE c.relname = 'tool_invocation_log'
    ) THEN
        ALTER TABLE tool_invocation_log RENAME TO tool_invocation_log_legacy;
        RAISE NOTICE 'Renamed existing tool_invocation_log to tool_invocation_log_legacy';
    END IF;
END $$;

-- Step 2: Create the partitioned table (idempotent)
CREATE TABLE IF NOT EXISTS tool_invocation_log (
    id               BIGSERIAL,
    tool_name        TEXT NOT NULL,
    input_params     JSONB,
    result_status    TEXT,
    response_time_ms INT,
    called_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
) PARTITION BY RANGE (called_at);

-- Step 3: Partitions for current year (extend manually or via pg_partman)
CREATE TABLE IF NOT EXISTS tool_invocation_log_2026_01
    PARTITION OF tool_invocation_log FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');
CREATE TABLE IF NOT EXISTS tool_invocation_log_2026_02
    PARTITION OF tool_invocation_log FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');
CREATE TABLE IF NOT EXISTS tool_invocation_log_2026_03
    PARTITION OF tool_invocation_log FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');
CREATE TABLE IF NOT EXISTS tool_invocation_log_2026_04
    PARTITION OF tool_invocation_log FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');
CREATE TABLE IF NOT EXISTS tool_invocation_log_2026_05
    PARTITION OF tool_invocation_log FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE IF NOT EXISTS tool_invocation_log_2026_06
    PARTITION OF tool_invocation_log FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');
CREATE TABLE IF NOT EXISTS tool_invocation_log_2026_07
    PARTITION OF tool_invocation_log FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
CREATE TABLE IF NOT EXISTS tool_invocation_log_2026_08
    PARTITION OF tool_invocation_log FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');
CREATE TABLE IF NOT EXISTS tool_invocation_log_2026_09
    PARTITION OF tool_invocation_log FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');
CREATE TABLE IF NOT EXISTS tool_invocation_log_2026_10
    PARTITION OF tool_invocation_log FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');
CREATE TABLE IF NOT EXISTS tool_invocation_log_2026_11
    PARTITION OF tool_invocation_log FOR VALUES FROM ('2026-11-01') TO ('2026-12-01');
CREATE TABLE IF NOT EXISTS tool_invocation_log_2026_12
    PARTITION OF tool_invocation_log FOR VALUES FROM ('2026-12-01') TO ('2027-01-01');

-- Step 4: Inherited index on the parent table
CREATE INDEX IF NOT EXISTS idx_invlog_tool_called
    ON tool_invocation_log(tool_name, called_at DESC);

-- Step 5: Migrate recent legacy data
INSERT INTO tool_invocation_log
    (id, tool_name, input_params, result_status, response_time_ms, called_at)
SELECT id, tool_name, input_params, result_status, response_time_ms, called_at
FROM tool_invocation_log_legacy
WHERE called_at >= '2026-01-01'
ON CONFLICT DO NOTHING;

-- Step 6: Function to auto-create next month's partition
-- Call this monthly via pg_cron: SELECT cron.schedule('0 0 25 * *', 'SELECT create_next_invlog_partition()');
CREATE OR REPLACE FUNCTION create_next_invlog_partition() RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    next_month      DATE := date_trunc('month', NOW() + INTERVAL '1 month');
    partition_name  TEXT := 'tool_invocation_log_' || to_char(next_month, 'YYYY_MM');
    start_date      TEXT := to_char(next_month, 'YYYY-MM-DD');
    end_date        TEXT := to_char(next_month + INTERVAL '1 month', 'YYYY-MM-DD');
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_class WHERE relname = partition_name) THEN
        EXECUTE format(
            'CREATE TABLE %I PARTITION OF tool_invocation_log FOR VALUES FROM (%L) TO (%L)',
            partition_name, start_date, end_date
        );
        RAISE NOTICE 'Created partition %', partition_name;
    END IF;
END;
$$;
