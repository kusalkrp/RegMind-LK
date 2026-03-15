-- SLTDA MCP — Production Improvements Migration
-- Run: psql $POSTGRES_URL -f migrations/002_improvements.sql
--
-- Safe to re-run: all DDL uses IF NOT EXISTS / DO blocks.

BEGIN;

-- ─── Composite indexes (not present in 001) ──────────────────────────────────

-- Hot path: documents queries filter by section_id + is_active together
CREATE INDEX IF NOT EXISTS idx_documents_section_active
    ON documents(section_id, is_active);

-- Hot path: registration_steps queries filter by category_code + action_type
CREATE INDEX IF NOT EXISTS idx_registration_steps_category_action
    ON registration_steps(category_code, action_type);

-- ─── New single-column indexes (not present in 001) ──────────────────────────

-- business_categories.category_group queried in health / discovery filters
CREATE INDEX IF NOT EXISTS idx_business_categories_group
    ON business_categories(category_group);

-- ─── Unique constraint on documents(source_url, language) ────────────────────
-- Prevents duplicate ingestion of the same document in the same language.
-- NOTE: if existing data already has duplicates on (source_url, language),
-- clean them up first:
--   DELETE FROM documents a USING documents b
--   WHERE a.id > b.id AND a.source_url = b.source_url AND a.language = b.language;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'uq_documents_source_url_language'
          AND conrelid = 'documents'::regclass
    ) THEN
        ALTER TABLE documents
            ADD CONSTRAINT uq_documents_source_url_language
            UNIQUE (source_url, language);
    END IF;
END;
$$;

-- ─── Validation failure queue ─────────────────────────────────────────────────
-- Extractor output that fails schema validation is routed here for review.
-- Valid records proceed to staging tables; invalid records wait for manual fix.

CREATE TABLE IF NOT EXISTS validation_failure_queue (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id     UUID REFERENCES documents(id) ON DELETE SET NULL,
    extractor_name  VARCHAR(100) NOT NULL,
    field_name      VARCHAR(100) NOT NULL,
    error_message   TEXT NOT NULL,
    raw_json        JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_vfq_document_id
    ON validation_failure_queue(document_id);
CREATE INDEX IF NOT EXISTS idx_vfq_created_at
    ON validation_failure_queue(created_at);

-- ─── Log retention function ───────────────────────────────────────────────────
-- Call this via pg_cron or an external scheduler to enforce the 90-day policy.
-- Enable pg_cron: CREATE EXTENSION IF NOT EXISTS pg_cron;
-- Then schedule: SELECT cron.schedule('cleanup-invocation-logs', '0 3 * * *',
--                  'SELECT cleanup_old_invocation_logs()');

CREATE OR REPLACE FUNCTION cleanup_old_invocation_logs() RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
    DELETE FROM tool_invocation_log
    WHERE called_at < NOW() - INTERVAL '90 days';
END;
$$;

-- ─── Partitioning note (deferred to v2) ───────────────────────────────────────
-- tool_invocation_log is a candidate for range partitioning by called_at once
-- row count exceeds ~10M rows.  Steps for v2:
--   1. CREATE TABLE tool_invocation_log_new (LIKE tool_invocation_log)
--        PARTITION BY RANGE (called_at);
--   2. CREATE TABLE tool_invocation_log_YYYY_MM PARTITION OF ...
--        FOR VALUES FROM ('YYYY-MM-01') TO ('YYYY-MM+1-01');
--   3. INSERT INTO tool_invocation_log_new SELECT * FROM tool_invocation_log;
--   4. ALTER TABLE tool_invocation_log RENAME TO tool_invocation_log_old;
--   5. ALTER TABLE tool_invocation_log_new RENAME TO tool_invocation_log;

COMMIT;
