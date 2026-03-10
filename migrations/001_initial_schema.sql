-- SLTDA MCP — Initial Schema
-- Run: psql $POSTGRES_URL -f migrations/001_initial_schema.sql

BEGIN;

-- ─── Enums ────────────────────────────────────────────────────────────────────

CREATE TYPE document_type_enum AS ENUM (
    'gazette', 'form', 'guideline', 'report', 'act',
    'toolkit', 'circular', 'checklist', 'plan', 'other'
);

CREATE TYPE language_enum AS ENUM ('english', 'sinhala', 'tamil', 'multilingual');

CREATE TYPE action_type_enum AS ENUM ('register', 'renew', 'inspect');

CREATE TYPE concession_type_enum AS ENUM ('tax', 'banking', 'moratorium', 'levy');

CREATE TYPE result_source_enum AS ENUM ('postgresql', 'qdrant', 'url_only');

CREATE TYPE doc_status_enum AS ENUM ('active', 'stale', 'superseded', 'inactive', 'url_suspect');

CREATE TYPE cutover_status_enum AS ENUM ('none', 'pending', 'qdrant_done', 'postgres_done', 'complete');

-- ─── Core document registry ───────────────────────────────────────────────────

CREATE TABLE documents (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    section_id          INTEGER NOT NULL CHECK (section_id BETWEEN 1 AND 13),
    section_name        VARCHAR(100) NOT NULL,
    document_name       VARCHAR(255) NOT NULL,
    document_type       document_type_enum NOT NULL DEFAULT 'other',
    language            language_enum NOT NULL DEFAULT 'english',
    source_url          TEXT NOT NULL,
    local_path          TEXT,
    file_size_kb        INTEGER,
    content_hash        VARCHAR(64),
    content_as_of       DATE,           -- date this doc's content was last successfully parsed
    last_scraped_at     TIMESTAMPTZ,
    last_parsed_at      TIMESTAMPTZ,
    last_url_verified_at TIMESTAMPTZ,
    is_indexed          BOOLEAN NOT NULL DEFAULT FALSE,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    status              doc_status_enum NOT NULL DEFAULT 'active',
    superseded_by       UUID REFERENCES documents(id),
    parsed_text_path    TEXT,
    format_family       VARCHAR(50),
    format_confidence   FLOAT,
    ocr_extracted       BOOLEAN NOT NULL DEFAULT FALSE,
    extraction_yield_tokens INTEGER,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_documents_section_id ON documents(section_id);
CREATE INDEX idx_documents_document_type ON documents(document_type);
CREATE INDEX idx_documents_language ON documents(language);
CREATE INDEX idx_documents_status ON documents(status);
CREATE INDEX idx_documents_is_active ON documents(is_active);

-- Staging mirror
CREATE TABLE documents_staging (LIKE documents INCLUDING ALL);

-- ─── Document sections ────────────────────────────────────────────────────────

CREATE TABLE document_sections (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id     UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    section_title   VARCHAR(255),
    content_text    TEXT,
    page_numbers    INTEGER[],
    section_order   INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_document_sections_document_id ON document_sections(document_id);

CREATE TABLE document_sections_staging (LIKE document_sections INCLUDING ALL);

-- ─── Business categories ──────────────────────────────────────────────────────

CREATE TABLE business_categories (
    id                      SERIAL PRIMARY KEY,
    category_code           VARCHAR(50) UNIQUE NOT NULL,
    category_name           VARCHAR(150) NOT NULL,
    category_group          VARCHAR(100) NOT NULL,
    gazette_document_id     UUID REFERENCES documents(id),
    guidelines_document_id  UUID REFERENCES documents(id),
    checklist_document_id   UUID REFERENCES documents(id),
    registration_document_id UUID REFERENCES documents(id),
    notes                   TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE business_categories_staging (LIKE business_categories INCLUDING ALL);

-- ─── Registration steps ───────────────────────────────────────────────────────

CREATE TABLE registration_steps (
    id                  SERIAL PRIMARY KEY,
    category_code       VARCHAR(50) NOT NULL,
    action_type         action_type_enum NOT NULL,
    step_number         INTEGER NOT NULL,
    step_title          VARCHAR(255) NOT NULL,
    step_description    TEXT NOT NULL,
    required_documents  TEXT[],
    estimated_duration  VARCHAR(100),
    fees                JSONB,
    UNIQUE (category_code, action_type, step_number)
);

CREATE INDEX idx_registration_steps_category ON registration_steps(category_code);
CREATE INDEX idx_registration_steps_action ON registration_steps(action_type);

CREATE TABLE registration_steps_staging (LIKE registration_steps INCLUDING ALL);

-- ─── Financial concessions ────────────────────────────────────────────────────

CREATE TABLE financial_concessions (
    id                  SERIAL PRIMARY KEY,
    concession_name     VARCHAR(255) NOT NULL,
    concession_type     concession_type_enum NOT NULL,
    applicable_to       TEXT[],
    rate_or_terms       TEXT,
    conditions          TEXT,
    effective_from      DATE,
    circular_reference  VARCHAR(100),
    document_id         UUID REFERENCES documents(id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_financial_concessions_type ON financial_concessions(concession_type);

CREATE TABLE financial_concessions_staging (LIKE financial_concessions INCLUDING ALL);

-- ─── Niche toolkits ───────────────────────────────────────────────────────────

CREATE TABLE niche_toolkits (
    id                  SERIAL PRIMARY KEY,
    toolkit_code        VARCHAR(50) UNIQUE NOT NULL,
    toolkit_name        VARCHAR(150) NOT NULL,
    target_market       TEXT,
    key_activities      TEXT[],
    regulatory_notes    TEXT,
    document_id         UUID REFERENCES documents(id),
    summary             TEXT,
    source_text_tokens  INTEGER,
    source_pages        INTEGER,
    extraction_confidence VARCHAR(20),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE niche_toolkits_staging (LIKE niche_toolkits INCLUDING ALL);

-- ─── Format review queue ─────────────────────────────────────────────────────

CREATE TABLE format_review_queue (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id     UUID REFERENCES documents(id),
    document_name   VARCHAR(255),
    filename        VARCHAR(255),
    detected_format VARCHAR(50),
    confidence      FLOAT,
    feature_summary JSONB,
    exemplar_scores JSONB,
    reviewed        BOOLEAN NOT NULL DEFAULT FALSE,
    resolution      VARCHAR(50),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ─── Tool invocation log ─────────────────────────────────────────────────────

CREATE TABLE tool_invocation_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tool_name       VARCHAR(100) NOT NULL,
    input_params    JSONB,
    response_time_ms INTEGER,
    result_source   result_source_enum,
    result_status   VARCHAR(20),
    error           TEXT,
    called_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_tool_log_tool_name ON tool_invocation_log(tool_name);
CREATE INDEX idx_tool_log_called_at ON tool_invocation_log(called_at);
-- Retention: auto-drop rows older than 90 days (handled by maintenance job)

-- ─── Arrivals data (structured extraction from monthly/annual reports) ───────

CREATE TABLE arrivals_data (
    id                  SERIAL PRIMARY KEY,
    document_id         UUID REFERENCES documents(id),
    report_period       VARCHAR(20),        -- e.g., '2024-03', '2024'
    report_type         VARCHAR(10) NOT NULL CHECK (report_type IN ('monthly', 'annual')),
    total_arrivals      INTEGER,
    yoy_change_percent  NUMERIC(6, 2),
    top_source_market   VARCHAR(100),
    raw_table_json      JSONB,              -- full extracted table for detailed queries
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_arrivals_data_period ON arrivals_data(report_period);
CREATE INDEX idx_arrivals_data_type ON arrivals_data(report_type);

CREATE TABLE arrivals_data_staging (LIKE arrivals_data INCLUDING ALL);

-- ─── Pipeline state ──────────────────────────────────────────────────────────

CREATE TABLE pipeline_state (
    id                  SERIAL PRIMARY KEY,
    run_id              UUID NOT NULL DEFAULT gen_random_uuid(),
    started_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at        TIMESTAMPTZ,
    last_embedded_chunk_id VARCHAR(255),  -- checkpoint for resume
    status              VARCHAR(30) NOT NULL DEFAULT 'running',
    docs_total          INTEGER,
    docs_downloaded     INTEGER,
    docs_parsed         INTEGER,
    docs_embedded       INTEGER,
    docs_failed         INTEGER,
    error_message       TEXT
);

-- ─── System metadata ─────────────────────────────────────────────────────────

CREATE TABLE system_metadata (
    id                      SERIAL PRIMARY KEY,
    active_qdrant_collection VARCHAR(100) NOT NULL DEFAULT 'sltda_documents',
    last_refresh_at          TIMESTAMPTZ,
    total_documents          INTEGER NOT NULL DEFAULT 0,
    total_vectors            INTEGER NOT NULL DEFAULT 0,
    cutover_status           cutover_status_enum NOT NULL DEFAULT 'none',
    rollback_available       BOOLEAN NOT NULL DEFAULT FALSE,
    rollback_expires_at      TIMESTAMPTZ,
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Seed one row
INSERT INTO system_metadata (active_qdrant_collection) VALUES ('sltda_documents');

COMMIT;
