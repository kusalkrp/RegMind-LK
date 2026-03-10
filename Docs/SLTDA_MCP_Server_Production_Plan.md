# SLTDA MCP Server — Production System Design & Implementation Plan

**Project Name:** `sltda-mcp`  
**Subtitle:** Sri Lanka Tourism Regulatory Intelligence as a Model Context Protocol Server  
**Version:** 2.0  
**Date:** March 2026  
**Author:** System Design Document  
**Changelog v2.0:** English-only PDF scope; dynamic PDF format identifier added (Section 5.5); production issue registry added (Section 16)

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Project Scope & Boundaries](#2-project-scope--boundaries)
3. [Architecture Overview](#3-architecture-overview)
4. [Data Layer Design](#4-data-layer-design)
5. [Document Intelligence Pipeline](#5-document-intelligence-pipeline)
   - 5.1 Pipeline Overview
   - 5.2 English-Only PDF Scope
   - 5.3 Scanned PDF Handling
   - 5.4 Structured Data Extraction
   - 5.5 Dynamic PDF Format Identifier
   - 5.6 Zero-Downtime Ingestion Design
6. [MCP Server Design](#6-mcp-server-design)
7. [Tool Catalogue & Contracts](#7-tool-catalogue--contracts)
8. [RAG System Design](#8-rag-system-design)
9. [Infrastructure & Deployment](#9-infrastructure--deployment)
10. [Observability & Reliability](#10-observability--reliability)
11. [Security & Ethics](#11-security--ethics)
12. [Phased Implementation Plan](#12-phased-implementation-plan)
13. [Testing Strategy](#13-testing-strategy)
14. [Maintenance & Operations](#14-maintenance--operations)
15. [Portfolio Positioning](#15-portfolio-positioning)
16. [Production Issue Registry](#16-production-issue-registry)

---

## 1. Executive Summary

### The Problem

Sri Lanka's tourism regulatory landscape — governed by the Sri Lanka Tourism Development Authority (SLTDA) — is documented across 50+ PDFs, gazettes, forms, and guidelines spread across a single downloads page (`sltda.gov.lk/en/download-2`). The content is critical for hotel operators, tour guides, investors, and policy researchers, but it is practically inaccessible: buried in PDFs, inconsistently formatted, and available in three languages with no programmatic interface.

### The Solution

`sltda-mcp` is a **Model Context Protocol (MCP) server** that transforms SLTDA's static document repository into a structured, AI-queryable intelligence layer. It exposes 14 tools covering regulatory lookup, compliance guidance, document retrieval, niche tourism strategy, and statistical data access — enabling any MCP-compatible AI client (Claude Desktop, LangGraph agents, Cursor) to answer tourism regulatory questions without human document hunting.

### What Makes This Production-Grade

- Pre-processed document intelligence layer (not live scraping on each call)
- Vector search (Qdrant) for semantic Q&A over PDFs
- Structured PostgreSQL layer for deterministic lookups
- Full Docker Compose deployment
- **Zero-downtime monthly refresh** — blue/green staging pattern; server never pauses during ingestion
- Atomic cutover with 48-hour rollback window
- Refresh pipeline for monthly content updates
- Multilingual support (English, Sinhala, Tamil)
- Sub-500ms response time target for structured tools

### Core Value Proposition

> *"Ask any question about registering, operating, or investing in Sri Lanka tourism — get a structured, grounded answer in seconds, without opening a single PDF."*

---

## 2. Project Scope & Boundaries

### In Scope

| Category | Included |
|----------|----------|
| Document sources | All 13 sections of `sltda.gov.lk/en/download-2` |
| Languages | **English PDFs only** (Sinhala and Tamil versions excluded from ingestion) |
| Tool types | Structured lookup + RAG-backed Q&A + document retrieval |
| Deployment | Docker Compose, single-server |
| Clients | Claude Desktop, LangGraph agents, any FastMCP-compatible client |
| Refresh cycle | Monthly automated re-ingestion |

### Out of Scope

| Excluded | Reason |
|----------|--------|
| Real-time arrival statistics | Covered by separate TourismPulse LK system |
| SLTDA tender notifications | Operational/transactional; different update cadence |
| Hotel booking or reservation data | Proprietary, not on downloads page |
| Legal advice or compliance certification | AI cannot provide legal assurance |
| Direct form submission to SLTDA | Requires authenticated government portal |
| Content from other SLTDA subpages not linked from downloads | Scope control |
| **Sinhala and Tamil PDF versions** | **Scoped out — complex script parsing unreliable; English versions cover all regulatory content** |

### Non-Goals

- This server does not replace SLTDA's official guidance
- It does not guarantee document currency beyond the last refresh cycle
- It does not answer questions that require real-time data (arrival counts, live tender status)

---

## 3. Architecture Overview

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        SLTDA DOWNLOADS PAGE                             │
│              sltda.gov.lk/en/download-2 (Public, Static)               │
│   PDFs │ Gazettes │ Forms │ Guidelines │ Reports │ Toolkits │ Acts      │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │ Monthly ingestion job
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                     INGESTION & PROCESSING LAYER                        │
│                                                                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────┐  │
│  │ Page Scraper │  │PDF Downloader│  │Format        │  │ Validator  │  │
│  │ (metadata)   │  │(English only)│  │Identifier    │  │ (Pandera)  │  │
│  └──────────────┘  └──────────────┘  │(Dynamic      │  └────────────┘  │
│                                      │ Classifier)  │                  │
│                                      └──────────────┘                  │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │  Routed Extractors (selected by Format Identifier)               │   │
│  │  ChecklistExtractor │ StepsExtractor │ GazetteExtractor │ ...    │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  Output: raw_documents/ + document_metadata table                      │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │
                    ┌────────────┴────────────┐
                    ▼                         ▼
┌───────────────────────────┐   ┌─────────────────────────────────────────┐
│   STRUCTURED DATA LAYER   │   │         VECTOR INTELLIGENCE LAYER       │
│                           │   │                                         │
│  PostgreSQL               │   │  Chunking → Embedding → Qdrant          │
│  ─────────────────────    │   │  ──────────────────────────────────     │
│  documents                │   │  Collection: sltda_chunks               │
│  document_sections        │   │  Model: Gemini text-embedding-004       │
│  tool_metadata            │   │  Chunk size: 600 tokens, 100 overlap    │
│  categories               │   │  Metadata: section, doc_type, language  │
│  niche_toolkits           │   │  Index: HNSW (cosine similarity)        │
│  financial_concessions    │   │                                         │
│  registration_steps       │   │                                         │
└───────────────┬───────────┘   └──────────────────┬──────────────────────┘
                │                                  │
                └──────────────┬───────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                          MCP SERVER LAYER                               │
│                    FastMCP (Python) — sltda-mcp                         │
│                                                                         │
│  Cluster 1: Registration & Compliance (3 tools)                         │
│  Cluster 2: Financial & Tax (3 tools)                                   │
│  Cluster 3: Statistics & Reports (2 tools)                              │
│  Cluster 4: Strategy & Policy (2 tools)                                 │
│  Cluster 5: Niche Tourism (2 tools)                                     │
│  Cluster 6: Investor & Discovery (2 tools)                              │
│                                                                         │
│  Tool routing: Structured query → PostgreSQL                            │
│                Semantic query   → Qdrant RAG + Gemini Flash             │
│                Document fetch   → PostgreSQL metadata + URL             │
└──────────────────────────────┬──────────────────────────────────────────┘
                               │  MCP Protocol (stdio / SSE)
               ┌───────────────┼───────────────┐
               ▼               ▼               ▼
      Claude Desktop    LangGraph Agent    Cursor / Other
      (local use)       (TourismPulse)     MCP Clients
```

### Architecture Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| MCP framework | FastMCP (Python) | Minimal boilerplate; decorator-based; shares stack with existing FastAPI services |
| Vector DB | Qdrant | Docker-native; Python client; no cloud dependency for portfolio |
| Embedding model | Gemini text-embedding-004 | Consistent with broader Gemini stack; multilingual; 768-dim |
| Structured DB | PostgreSQL | Relational lookups for deterministic tools; shared with TourismPulse |
| PDF parser | pdfplumber (primary), tabula (fallback) | pdfplumber handles complex layouts better for regulatory docs |
| LLM for synthesis | Gemini Flash | Fast, cheap; used only for RAG answer synthesis, not tool routing |
| Transport | stdio (local) + SSE (remote) | stdio for Claude Desktop; SSE for LangGraph agents over network |

---

## 4. Data Layer Design

### 4.1 PostgreSQL Schema

#### Table: `documents`

Central registry of every document in the system.

| Column | Type | Description |
|--------|------|-------------|
| id | UUID PK | Unique document identifier |
| section_id | INTEGER | SLTDA downloads page section (1–13) |
| section_name | VARCHAR(100) | Human-readable section name |
| document_name | VARCHAR(255) | Display name of document |
| document_type | ENUM | gazette / form / guideline / report / act / toolkit / circular / checklist / plan |
| language | ENUM | english / sinhala / tamil / multilingual |
| source_url | TEXT | Direct URL on SLTDA server |
| local_path | TEXT | Path in local document store |
| file_size_kb | INTEGER | For staleness monitoring |
| content_hash | VARCHAR(64) | SHA-256 for change detection |
| last_scraped_at | TIMESTAMP | When last downloaded |
| last_parsed_at | TIMESTAMP | When last text-extracted |
| is_indexed | BOOLEAN | Whether embedded in Qdrant |
| is_active | BOOLEAN | Whether currently available on SLTDA |
| parsed_text_path | TEXT | Path to extracted plaintext |
| created_at | TIMESTAMP | |
| updated_at | TIMESTAMP | |

#### Table: `document_sections`

Maps structured content extracted from documents into queryable records.

| Column | Type | Description |
|--------|------|-------------|
| id | UUID PK | |
| document_id | UUID FK → documents | Parent document |
| section_title | VARCHAR(255) | Section heading within document |
| content_text | TEXT | Extracted text of section |
| page_numbers | INTEGER[] | Pages this section spans |
| section_order | INTEGER | Order within document |

#### Table: `business_categories`

Master list of tourism business types from Section 3.

| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL PK | |
| category_code | VARCHAR(50) | e.g., `boutique_hotel`, `eco_lodge` |
| category_name | VARCHAR(150) | Display name |
| category_group | VARCHAR(100) | accommodation / food_beverage / activities / services / retail |
| gazette_document_id | UUID FK | Relevant gazette |
| guidelines_document_id | UUID FK | Registration guidelines |
| checklist_document_id | UUID FK | Inspection checklist |
| registration_document_id | UUID FK | Registration form/process |
| notes | TEXT | Any special conditions |

#### Table: `registration_steps`

Step-by-step registration/renewal processes per business type.

| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL PK | |
| category_code | VARCHAR(50) | FK to business_categories |
| action_type | ENUM | register / renew / inspect |
| step_number | INTEGER | Sequence |
| step_title | VARCHAR(255) | Short label |
| step_description | TEXT | Full instructions |
| required_documents | TEXT[] | List of required docs |
| estimated_duration | VARCHAR(100) | e.g., "5–7 working days" |
| fees | JSONB | Fee structure details |

#### Table: `financial_concessions`

Structured financial relief data from Section 2.

| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL PK | |
| concession_name | VARCHAR(255) | |
| concession_type | ENUM | tax / banking / moratorium / levy |
| applicable_to | TEXT[] | Business types eligible |
| rate_or_terms | TEXT | Key terms (e.g., "7% flat tax rate") |
| conditions | TEXT | Eligibility conditions |
| effective_from | DATE | |
| circular_reference | VARCHAR(100) | e.g., "Banking Circular No. 07 of 2019" |
| document_id | UUID FK | Source document |

#### Table: `niche_toolkits`

Niche tourism categories and toolkit metadata from Section 12.

| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL PK | |
| toolkit_code | VARCHAR(50) | e.g., `digital_nomad`, `wellness` |
| toolkit_name | VARCHAR(150) | Display name |
| target_market | TEXT | Primary visitor segment |
| key_activities | TEXT[] | Core activities in this niche |
| regulatory_notes | TEXT | Any special licensing required |
| document_id | UUID FK | Toolkit PDF |
| summary | TEXT | AI-generated summary (pre-computed) |

#### Table: `tool_invocation_log`

Observability — every MCP tool call logged.

| Column | Type | Description |
|--------|------|-------------|
| id | UUID PK | |
| tool_name | VARCHAR(100) | Which tool was called |
| input_params | JSONB | Parameters passed |
| response_time_ms | INTEGER | Latency |
| result_source | ENUM | postgresql / qdrant / url_only |
| error | TEXT | If failed |
| called_at | TIMESTAMP | |

---

### 4.2 Qdrant Collection Design

**Collection Name:** `sltda_documents`

**Vector Configuration:**

- Dimensions: 768 (Gemini text-embedding-004)
- Distance metric: Cosine
- Index type: HNSW
- On-disk payload: Yes (documents too large for RAM)

**Payload Schema per Point:**

```
{
  "document_id":    UUID,
  "document_name":  string,
  "section_name":   string,       // SLTDA page section
  "document_type":  string,       // gazette / toolkit / act / etc.
  "language":       string,
  "chunk_index":    integer,      // chunk position within document
  "chunk_text":     string,       // the actual text (for display)
  "page_numbers":   [integer],
  "source_url":     string,
  "last_updated":   ISO8601 date
}
```

**Chunking Strategy:**

- Target chunk size: 600 tokens
- Overlap: 100 tokens (to preserve context across chunk boundaries)
- Split on: paragraph boundaries first, then sentence boundaries, never mid-sentence
- Special handling: Tables extracted separately as structured JSON, not plain text chunks
- Metadata-only chunks: Short documents (< 200 tokens) stored as single chunk

---

### 4.3 Document File Store

```
documents/
├── raw/                         # Original downloaded files (immutable)
│   ├── section_01_registration/
│   ├── section_02_financial/
│   ├── section_03_accommodation/
│   ├── section_06_tdl/
│   ├── section_09_statistics/
│   ├── section_10_annual_reports/
│   ├── section_11_strategic_plan/
│   └── section_12_niche_toolkits/
│
├── parsed/                      # Extracted plaintext per document
│   └── {document_id}.txt
│
├── chunks/                      # Pre-chunked text, one JSON per document
│   └── {document_id}_chunks.json
│
└── manifests/                   # Ingestion manifests for diff detection
    └── {YYYY-MM-DD}_manifest.json
```

---

## 5. Document Intelligence Pipeline

### 5.1 Pipeline Overview

```
TRIGGER (monthly cron or manual)
         │
         ▼
┌────────────────────┐
│  1. Page Scraper   │  Crawl sltda.gov.lk/en/download-2
│                    │  Extract: document names, URLs, sections
│                    │  Filter: English PDFs only (skip Sinhala/Tamil)
│                    │  Output: candidate_documents list
└─────────┬──────────┘
          │
          ▼
┌────────────────────┐
│  2. Change Detector│  Compare candidate list vs manifest
│                    │  Identify: new / modified / removed docs
│                    │  Use: content hash (SHA-256) comparison
└─────────┬──────────┘
          │ Only changed/new docs proceed
          ▼
┌────────────────────┐
│  3. PDF Downloader │  Download with polite rate limiting (1 req/sec)
│                    │  Retry: 3x with exponential backoff
│                    │  Validate: file size > 5KB, PDF magic bytes (%PDF)
│                    │  Guard: reject HTML/CAPTCHA masquerading as PDF
│                    │  Store: documents/raw/{section}/
└─────────┬──────────┘
          │
          ▼
┌────────────────────┐
│  4. FORMAT         │  Feature extraction (structural signals)
│     IDENTIFIER     │  Tier 1: Rule-based classification (deterministic)
│                    │  Tier 2: Embedding similarity (ambiguous cases only)
│                    │  Output: format_family + confidence + strategy_config
│                    │  Unknown/low-confidence → format_review_queue
└─────────┬──────────┘
          │ Routes to appropriate extractor
          ▼
┌────────────────────┐
│  5. ROUTED         │  Extractor selected by Format Identifier
│     EXTRACTOR      │  Each extractor purpose-built for its format family
│                    │  Strategies: list_aware / clause_aware /
│                    │             heading_aware / table_per_chunk
│                    │  Output: documents/parsed/{id}.txt + structured JSON
└─────────┬──────────┘
          │
          ▼
┌────────────────────┐
│  6. Validator      │  Pandera schema checks
│                    │  Minimum content length (> 200 chars for English)
│                    │  Format-specific validation rules
│                    │  Flag: text yield below expected for format type
└─────────┬──────────┘
          │
          ▼
┌────────────────────┐
│  7. Chunker        │  Format-specific chunk strategy (from YAML config)
│                    │  Never split mid-list or mid-clause
│                    │  Attach metadata: format_family, doc_type, section
│                    │  Output: documents/chunks/{id}_chunks.json
└─────────┬──────────┘
          │
          ▼
┌────────────────────┐
│  8. Embedder       │  Gemini text-embedding-004
│                    │  Batch: 100 chunks per API call
│                    │  Checkpoint: track last embedded chunk_id
│                    │  Resume-safe: skip already-embedded chunks on retry
└─────────┬──────────┘
          │
          ▼
┌────────────────────┐
│  9. Qdrant Upsert  │  Target: sltda_documents_next (staging collection)
│                    │  Upsert new vectors + payloads
│                    │  Verify: point count post-upsert
└─────────┬──────────┘
          │
          ▼
┌────────────────────┐
│  10. PG Sync       │  Write to staging tables only
│                    │  (documents_staging, registration_steps_staging etc.)
│                    │  Update manifest
└─────────┬──────────┘
          │
          ▼
┌────────────────────┐
│  11. Smoke Tests   │  Run against staging data before any cutover
│                    │  All 14 tools tested with known inputs
│                    │  Abort pipeline if any test fails
└─────────┬──────────┘
          │
          ▼
┌────────────────────┐
│  12. Atomic        │  PG table rename transaction + Qdrant alias swap
│      Cutover       │  < 2 seconds; server uninterrupted throughout
│                    │  Old data retained 48h for rollback
└─────────┬──────────┘
          │
          ▼
┌────────────────────┐
│  13. Post-Cutover  │  Health check on live system
│      Monitor       │  Alert on error spikes in tool_invocation_log
│                    │  Log pipeline summary + format_review_queue items
└────────────────────┘
```

### 5.2 English-Only PDF Scope

**Decision:** Only English-language PDFs are ingested and indexed. Sinhala and Tamil versions are excluded from the pipeline entirely.

**Rationale:**

- All regulatory content published by SLTDA is available in English. Every form, gazette, guideline, checklist, and report has an English version that covers 100% of the substantive content.
- Sinhala script uses complex ligature rendering that most PDF libraries — including pdfplumber — cannot reliably decode. Many Sinhala PDFs embed text as glyph IDs rather than Unicode code points, producing either empty strings or replacement character sequences (□□□□) on extraction.
- Tesseract's Sinhala OCR model achieves approximately 70–80% character accuracy on clean prints — insufficient for regulatory documents where a misread registration fee or wrong deadline has real business consequences.
- Scoping to English eliminates an entire class of silent quality failures (Issue #6 in the production issue registry) without any loss of functional coverage.

**Implementation:**

- The page scraper filters documents at the URL/filename level: any file with `_si`, `_sinhala`, `_sin`, `_tamil`, `_ta` in the name, or linked from Sinhala/Tamil labelled table cells, is excluded from the candidate list.
- The PDF downloader adds a secondary language check: after downloading, if the first 500 characters extracted contain > 30% non-Latin Unicode characters, the file is rejected and logged as `language_rejected`.
- Document metadata records `language = english` for all indexed documents.
- Tool responses carry a note: *"All content sourced from English-language SLTDA documents."*

**Future extension path:** If multilingual support becomes a requirement, the format identifier and extractor framework introduced in Section 5.5 is designed to accommodate language-specific extraction strategies without changes to the pipeline orchestrator — simply add language-aware extractor classes and update `format_strategies.yaml`.

---

### 5.3 Scanned PDF Handling

Some SLTDA English documents — particularly older gazettes — may be scanned images rather than text-layer PDFs. The pipeline handles this with a focused fallback path for English-only content:

- After pdfplumber extraction, if text yield < 200 characters per page, flag as `scan_detected`
- Route to OCR step: Tesseract `eng` language pack only
- OCR output is lower confidence — flag these chunks in Qdrant payload as `ocr_extracted: true`
- RAG responses from OCR-extracted chunks include a disclaimer: *"This content was extracted via OCR from a scanned document and may contain minor inaccuracies."*
- Scanned documents that produce < 100 characters even after OCR are classified as `unextractable`, stored in `format_review_queue`, and excluded from the index

---

### 5.4 Structured Data Extraction

Beyond raw text, specific documents yield structured data that populates PostgreSQL tables directly. These require custom extractors per document type — selected automatically by the Format Identifier (Section 5.5):

| Document | Extractor Class | Output Table |
|----------|----------------|--------------|
| Registration Process/Steps | `StepsExtractor` | `registration_steps` |
| Payment Structure | `StepsExtractor` | `registration_steps.fees` |
| Financial Concessions | `CircularExtractor` | `financial_concessions` |
| Accommodation Services table | `DataTableExtractor` | `business_categories` |
| TDL Checklist | `ChecklistExtractor` | embedded in `financial_concessions` |
| Niche Toolkit summaries | `ToolkitExtractor` | `niche_toolkits.summary` |

Each extractor produces a validated JSON intermediate before PostgreSQL insertion. Validation rules are format-specific — a `StepsExtractor` output must have at least 2 steps with non-empty descriptions; a `DataTableExtractor` output must have at least 5 rows and 3 columns. Validation failure routes to `format_review_queue` rather than silently loading bad data.

---

### 5.5 Dynamic PDF Format Identifier

The Format Identifier is a dedicated pipeline stage that sits between the PDF Downloader and the extraction step. Its role is to **inspect each PDF, classify its structural family, and select the appropriate extraction strategy** — replacing the previous approach of running a fixed extractor and failing silently when the format drifts.

#### Why This Is Necessary

SLTDA publishes 11 structurally distinct document families — a registration checklist looks nothing like an annual report, which looks nothing like a gazette. Previously, extractors were written against assumed formats. When SLTDA reformats a document (which happens when new staff produce reports, when the organization redesigns templates, or when a new category of document is introduced), the extractor silently produces wrong structured data with no error signal. The Format Identifier converts format drift from a silent data corruption event into a detectable, alertable condition.

#### Architecture

```
Downloaded PDF (English only)
         │
         ▼
┌─────────────────────────────────┐
│   STAGE 1: FEATURE EXTRACTION   │
│                                 │
│  Structural signals only        │
│  (no full text parse needed)    │
│  Runtime: < 500ms per document  │
└──────────────┬──────────────────┘
               │ Feature fingerprint
               ▼
┌─────────────────────────────────┐
│  STAGE 2: CLASSIFICATION        │
│                                 │
│  Tier 1: Rule-based (fast)      │
│  → handles ~75% of documents    │
│  → confidence = 1.0             │
│                                 │
│  Tier 2: Embedding similarity   │
│  → ambiguous cases only         │
│  → vs format_exemplars in Qdrant│
│  → confidence = similarity score│
└──────────────┬──────────────────┘
               │ format_family + confidence
               ▼
┌─────────────────────────────────┐
│  STAGE 3: STRATEGY SELECTION    │
│                                 │
│  format_strategies.yaml lookup  │
│  → extractor class              │
│  → chunk_strategy               │
│  → structured_extraction flag   │
│  → output_table                 │
└──────────────┬──────────────────┘
               │
    ┌──────────┴──────────────────────────────┐
    │          │            │                  │
    ▼          ▼            ▼                  ▼
Checklist   Gazette    Legislation         Unknown
Extractor   Extractor  Extractor           → FallbackExtractor
                                           → format_review_queue
                                           → alert
```

#### Stage 1: Feature Extraction

A lightweight structural fingerprint is extracted from each PDF without full text parsing. Extraction takes under 500ms per document:

| Feature | Extraction Method | Classification Signal |
|---------|------------------|----------------------|
| Page count | PDF metadata | Short = form; Long = report/act |
| Table count | pdfplumber (count only) | High = data report/checklist |
| Avg table column count | pdfplumber | 2-col = steps; 5+ col = accommodation table |
| Text density per page | char count / page area | Low = form with whitespace |
| Font size variance | PDF font table analysis | High variance = report; Low = plain doc |
| Heading hierarchy depth | Font size bucketing | Deep = act/report; Shallow = guideline |
| Has numbered lists | Regex on first 2 pages | Registration steps, checklists |
| First page title text | pdfplumber first page | Direct classification signal |
| Distinct font count | PDF font table | Many = designed toolkit; Few = gov form |
| File size bracket | OS stat | < 200KB = form; > 2MB = annual report |
| Filename pattern | Regex on filename | `annual_report_2024` → unambiguous |

#### Stage 2: Classification

**Tier 1 — Rule-Based (Deterministic, ~75% of documents)**

Explicit rules handle the clear cases before any embedding is computed:

```
IF filename matches /annual.?report.?\d{4}/i          → FORMAT: annual_report
IF filename matches /monthly.?arrival/i               → FORMAT: arrivals_report
IF first_page_title contains "Gazette"                → FORMAT: gazette_legal
IF first_page_title contains "Act No"                 → FORMAT: legislation
IF first_page_title contains "Toolkit"                → FORMAT: niche_toolkit
IF first_page_title contains "Circular"               → FORMAT: financial_circular
IF page_count == 1 AND has_numbered_list == true      → FORMAT: checklist_form
IF table_count > 8 AND avg_col_count > 4              → FORMAT: data_table_report
IF page_count <= 3 AND text_density < 0.25            → FORMAT: registration_form
IF filename matches /strategic.?plan/i                → FORMAT: strategic_plan
```

**Tier 2 — Embedding Similarity (Ambiguous cases, ~25% of documents)**

Documents not matched by Tier 1 rules have their first 500 tokens embedded using Gemini text-embedding-004 and compared against the **format exemplar library** — a dedicated Qdrant collection (`format_exemplars`) containing one canonical example per format family:

| Format Family | Exemplar Description |
|--------------|---------------------|
| `checklist_form` | Standard SLTDA registration checklist |
| `registration_steps` | Step-by-step registration process document |
| `gazette_legal` | Official gazette with clause numbering |
| `legislation` | Tourism Act with section/subsection structure |
| `niche_toolkit` | Adventure tourism toolkit |
| `data_table_report` | Monthly arrivals report with country breakdown table |
| `annual_report` | Annual report with narrative sections and financial tables |
| `financial_circular` | Banking circular with rate tables |
| `strategic_plan` | Strategic plan with goals and KPI targets |
| `registration_form_blank` | Blank form with fillable fields |
| `guidelines_narrative` | Narrative operational guidelines document |

Classification result = format family of nearest exemplar + cosine similarity as confidence score.

#### Stage 3: Strategy Selection

The format classification maps to an extraction strategy via `ingestion/config/format_strategies.yaml`. This configuration file — not hardcoded logic — is what makes the system dynamically extensible:

```yaml
format_strategies:

  checklist_form:
    extractor: ChecklistExtractor
    output_table: registration_steps
    chunk_strategy: list_aware        # never split numbered lists
    structured_extraction: true
    table_extraction: false

  registration_steps:
    extractor: StepsExtractor
    output_table: registration_steps
    chunk_strategy: list_aware
    structured_extraction: true
    table_extraction: false

  gazette_legal:
    extractor: GazetteExtractor
    output_table: document_sections
    chunk_strategy: clause_aware      # split on clause/section boundaries
    structured_extraction: false
    table_extraction: true

  legislation:
    extractor: LegislationExtractor
    output_table: document_sections
    chunk_strategy: section_aware     # split on numbered sections
    structured_extraction: false
    table_extraction: false

  niche_toolkit:
    extractor: ToolkitExtractor
    output_table: niche_toolkits
    chunk_strategy: heading_aware     # split on heading boundaries
    structured_extraction: true       # Gemini summary generation
    table_extraction: false

  data_table_report:
    extractor: DataTableExtractor
    output_table: arrivals_data
    chunk_strategy: table_per_chunk   # each table becomes one chunk
    structured_extraction: true
    table_extraction: true

  annual_report:
    extractor: AnnualReportExtractor
    output_table: document_sections
    chunk_strategy: heading_aware
    structured_extraction: false
    table_extraction: true

  financial_circular:
    extractor: CircularExtractor
    output_table: financial_concessions
    chunk_strategy: paragraph_aware
    structured_extraction: true
    table_extraction: true

  strategic_plan:
    extractor: NarrativeExtractor
    output_table: document_sections
    chunk_strategy: heading_aware
    structured_extraction: false
    table_extraction: false

  registration_form_blank:
    extractor: FormExtractor
    output_table: document_sections
    chunk_strategy: paragraph_aware
    structured_extraction: false
    table_extraction: false

  guidelines_narrative:
    extractor: NarrativeExtractor
    output_table: document_sections
    chunk_strategy: paragraph_aware
    structured_extraction: false
    table_extraction: false

  unknown:
    extractor: FallbackExtractor      # generic text extraction, no structured output
    output_table: document_sections
    chunk_strategy: paragraph_aware
    structured_extraction: false
    table_extraction: false
    flag_for_review: true
    alert: true
```

**Adding a new format family** requires only: (1) adding a row to this YAML, (2) writing the corresponding extractor class, (3) adding one exemplar document to the `format_exemplars` Qdrant collection. The pipeline orchestrator requires no changes.

#### Confidence Scoring and Fallback Routing

| Confidence Level | Condition | Action |
|-----------------|-----------|--------|
| `certain` (1.0) | Rule-based match | Use selected extractor, no flag |
| `high` (0.85–1.0) | Embedding similarity > 0.85 | Use selected extractor, no flag |
| `medium` (0.70–0.85) | Similarity 0.70–0.85 | Use FallbackExtractor, add to `format_review_queue` |
| `low` (< 0.70) | Similarity < 0.70 | Use FallbackExtractor, add to `format_review_queue`, alert |

Documents routed to `format_review_queue` are never dropped — they are indexed via FallbackExtractor (plain text chunking, no structured extraction) so they remain searchable, and a weekly summary of unrecognized formats is included in the pipeline report.

#### How This Resolves Production Issues

| Issue | How Format Identifier Addresses It |
|-------|-------------------------------------|
| #3 — PDF format drift breaking extractors | Drift detected as similarity drop → routes to FallbackExtractor + alert instead of silently wrong data |
| #7 — Table row-shifting in structured extraction | Format-specific extractors use purpose-built table parsing strategies; column misalignment bugs are isolated per extractor |
| #8 — Gemini hallucinating toolkit summaries | Gemini summarization only runs when `niche_toolkit` classified with high confidence; low-confidence docs skip summarization |
| #1 — CAPTCHA stored as PDF | Near-zero text + no tables + no headings = no rule match + low embedding similarity → `unknown` + alert |

#### The `format_review_queue` Table

```
format_review_queue:
  id              UUID PK
  document_id     UUID FK → documents
  document_name   VARCHAR
  filename        VARCHAR
  detected_format VARCHAR     -- best guess even if low confidence
  confidence      FLOAT
  feature_summary JSONB       -- the feature fingerprint for debugging
  exemplar_scores JSONB       -- top 3 exemplar similarity scores
  reviewed        BOOLEAN     -- set true after manual review
  resolution      VARCHAR     -- "added_new_format" / "mapped_to_existing" / "excluded"
  created_at      TIMESTAMP
```

This table is reviewed as part of the monthly refresh checklist. Recurring patterns in unrecognized documents drive new extractor development.

---

---

### 5.6 Zero-Downtime Ingestion Design

**The MCP server never goes down during a monthly refresh.** The ingestion pipeline and the MCP server are fully independent processes that share the same databases but never block each other. This is achieved through a blue/green staging pattern with an atomic cutover.

#### Core Principle: The Server is Always Stateless

The MCP server holds no document content in memory. Every tool call queries PostgreSQL or Qdrant at call time — nothing is loaded at startup, nothing is cached between calls. This means swapping the underlying data is completely invisible to the running server process. There is nothing to reload, restart, or signal.

#### The Four Phases of a Monthly Refresh

**Phase 1 — Background Ingestion (1–3 hours)**

The ingestion job starts (triggered by cron or manually). Throughout this entire phase, the MCP server continues serving all 14 tools normally using the previous month's data. The pipeline writes exclusively to shadow staging targets — never to production tables:

- PostgreSQL: writes to `documents_staging`, `registration_steps_staging`, `financial_concessions_staging`, and all other structured staging tables
- Qdrant: builds a new collection named `sltda_documents_next` in parallel with the live `sltda_documents` collection

The MCP server reads only from `documents`, `registration_steps`, `financial_concessions`, and `sltda_documents`. It has no awareness that staging targets exist.

```
During Ingestion:

  MCP Server          Ingestion Pipeline
  ──────────          ──────────────────
  reads: documents    writes: documents_staging
  reads: reg_steps    writes: reg_steps_staging
  reads: qdrant/      writes: qdrant/
         sltda_docs          sltda_docs_NEXT
  
  ↑ Unaffected        ↑ Isolated
```

**Phase 2 — Smoke Tests Against Staging (5–10 minutes)**

Before any production data is touched, the pipeline runs the full 14-tool smoke test suite pointed at the staging tables and the `sltda_documents_next` Qdrant collection. Every tool is called with a known valid input and asserted against expected output structure.

If any smoke test fails, the pipeline **aborts entirely**. Staging tables are preserved for debugging but production is untouched. An alert fires to Slack/email. The server continues serving last month's data indefinitely until the issue is resolved and the pipeline is re-run.

**Phase 3 — Atomic Cutover (< 2 seconds)**

If all smoke tests pass, production tables are swapped inside a single PostgreSQL transaction and Qdrant's collection alias is atomically reassigned:

```
PostgreSQL transaction:
  BEGIN;
    ALTER TABLE documents            RENAME TO documents_old;
    ALTER TABLE documents_staging    RENAME TO documents;
    ALTER TABLE registration_steps   RENAME TO registration_steps_old;
    ALTER TABLE registration_steps_staging RENAME TO registration_steps;
    -- ... same pattern for all structured tables
    UPDATE system_metadata
      SET active_qdrant_collection = 'sltda_documents_next',
          last_refresh_at = NOW();
  COMMIT;

Qdrant:
  Reassign alias 'sltda_documents' → 'sltda_documents_next'
  (atomic — Qdrant supports collection aliases for this pattern)
```

The MCP server's connection pool picks up the renamed tables on its next query. Any tool call that started just before the cutover completes against the old tables (transaction isolation). Any call starting after completes against the new tables. There is no observable interruption.

**Phase 4 — Old Data Retained for 48 Hours**

`documents_old`, `registration_steps_old`, and the previous Qdrant collection are not immediately deleted. They are retained for 48 hours as a rollback window. If a problem is discovered post-cutover — a document was incorrectly parsed, structured data is wrong — rollback is a single reverse rename transaction taking under one second.

After 48 hours with no issues, old tables and the previous Qdrant collection are dropped.

#### Ingestion Failure Modes

| Scenario | Behaviour | Server Impact |
|----------|-----------|---------------|
| SLTDA site unreachable during download | Pipeline aborts; retries next morning; alert fires | None — serves previous month's data |
| < 10% of documents fail parsing | Pipeline continues with successfully parsed subset; failed docs retained from previous cycle | None |
| > 10% of documents fail parsing | Pipeline aborts before cutover; alert fires | None |
| Smoke test fails post-ingestion | Pipeline aborts before cutover; staging preserved for debug | None |
| Gemini embedding API quota exceeded | Embedding step pauses, resumes from last checkpointed chunk_id | None |
| Qdrant write failure mid-upsert | Rolls back to last valid state; staging collection remains incomplete; pipeline aborts | None |
| Cutover transaction fails | PostgreSQL rolls back automatically; production tables unchanged | None |

#### What Clients Observe During a Refresh Window

From any MCP client perspective during the 1–3 hour ingestion window:

- All 14 tools respond normally with no added latency
- All responses are based on the previous month's indexed data
- `GET /health` shows `ingestion_status: running` as an informational field — this does not affect tool availability
- The `last_refresh` field in every tool response continues to show the previous refresh date honestly
- After atomic cutover, `last_refresh` updates to the current date on all subsequent responses

#### Why SLTDA Data Is Well-Suited to This Pattern

SLTDA documents change slowly — the Tourism Act has not changed since 2005, the Strategic Plan covers 2022–2025, and registration guidelines update at most once or twice per year. The only content that changes meaningfully month-to-month is the monthly arrivals report and the annual report when a new year is published. Serving data from the previous month's index during a 2–3 hour ingestion window is accurate for 99% of queries. The freshness disclaimer on every tool response communicates the data currency honestly.

---

## 6. MCP Server Design

### 6.1 Server Configuration

```
Server Name:     sltda-mcp
Version:         1.0.0
Description:     Sri Lanka Tourism Development Authority regulatory
                 and strategic intelligence server
Transport:       stdio (Claude Desktop) + SSE (network agents)
Base URL (SSE):  http://localhost:8001/mcp
Auth:            None (public data; portfolio deployment)
Max concurrency: 20 simultaneous tool calls
Timeout:         30 seconds per tool call
```

### 6.2 Tool Response Envelope

Every tool returns a consistent JSON envelope:

```
{
  "status":       "success" | "partial" | "not_found" | "error",
  "tool":         string (tool name),
  "data":         object | array (the actual result),
  "source":       {
    "type":       "structured" | "rag" | "metadata",
    "documents":  [{ name, url, section, last_updated }],
    "confidence": "high" | "medium" | "low"   // RAG tools only
  },
  "disclaimer":   string | null,   // legal/freshness disclaimer if needed
  "generated_at": ISO8601 timestamp
}
```

**Confidence levels for RAG tools:**

- `high`: Retrieved chunks have similarity score > 0.85
- `medium`: Similarity score 0.70–0.85
- `low`: Best match below 0.70; answer may be incomplete

**Disclaimer rules:**

- All tools: *"This information is based on SLTDA documents as of [last_refresh_date]. Verify with official SLTDA sources before making business decisions."*
- Legal tools (Tourism Act): *"This is not legal advice. Consult a qualified attorney for legal matters."*
- Financial tools: *"Tax and levy information may change. Confirm current rates with SLTDA or a tax professional."*

### 6.3 Tool Routing Logic

```
Incoming Tool Call
       │
       ▼
Is this a structured lookup tool?
(get_registration_requirements, get_accommodation_standards,
 get_registration_checklist, get_financial_concessions,
 get_tdl_information, get_tax_rate, get_annual_report,
 get_niche_categories, get_niche_toolkit, get_investment_process)
       │
   YES │                          NO (RAG tools)
       ▼                           ▼
PostgreSQL query           Qdrant semantic search
(deterministic,            + Gemini Flash synthesis
 sub-100ms)                (300–800ms)
       │                           │
       └──────────┬────────────────┘
                  ▼
         Envelope + Disclaimer
                  │
                  ▼
            Return to Client
```

---

## 7. Tool Catalogue & Contracts

### Cluster 1 — Registration & Compliance

---

#### Tool: `get_registration_requirements`

**Purpose:** Returns complete registration or renewal requirements for any SLTDA-regulated tourism business type.

**When Claude should call this:** User asks about registering a hotel, guest house, tour guide service, restaurant, spa, eco-lodge, or any tourism business with SLTDA. Also called for renewal queries.

**Input Parameters:**

| Parameter | Type | Required | Values | Description |
|-----------|------|----------|--------|-------------|
| business_type | string | Yes | See business_categories table | Type of tourism business |
| action | enum | Yes | `register` / `renew` | First-time or renewal |
| language | enum | No | `english` / `sinhala` / `tamil` | Default: english |

**Output:**

```
{
  "business_type": string,
  "action": string,
  "steps": [
    {
      "step_number": integer,
      "title": string,
      "description": string,
      "required_documents": [string],
      "estimated_duration": string,
      "fees": { "amount": string, "currency": "LKR", "notes": string }
    }
  ],
  "total_estimated_duration": string,
  "applicable_gazette": { "name": string, "url": string },
  "checklist_url": string,
  "contact": string
}
```

**Data source:** PostgreSQL (`registration_steps` + `business_categories`)
**Expected latency:** < 100ms

---

#### Tool: `get_accommodation_standards`

**Purpose:** Returns regulatory standards, gazette references, and operational guidelines for any accommodation category registered under SLTDA.

**When Claude should call this:** User asks about standards, classifications, or legal requirements for a specific accommodation type (boutique hotel, home stay, hostel, heritage bungalow, etc.).

**Input Parameters:**

| Parameter | Type | Required | Values |
|-----------|------|----------|--------|
| category | string | Yes | `classified_hotel` / `boutique_villa` / `guest_house` / `home_stay` / `hostel` / `eco_lodge` / `heritage_bungalow` / `heritage_home` / `rented_apartment` / `rented_home` / `camping_site` / `serviced_apartment` / `ayurvedic_hotel` |
| detail_level | enum | No | `summary` / `full` — default: `summary` |

**Output:**

```
{
  "category": string,
  "category_group": string,
  "gazette": { "name": string, "url": string },
  "standards_summary": string,
  "operational_guidelines_url": string,
  "inspection_checklist_url": string,
  "registration_requirements_url": string,
  "special_conditions": string | null
}
```

**Data source:** PostgreSQL (`business_categories` with document joins)
**Expected latency:** < 100ms

---

#### Tool: `get_registration_checklist`

**Purpose:** Returns an itemized checklist of documents and requirements for a given business type and process stage.

**When Claude should call this:** User explicitly asks for a checklist, or is in a compliance workflow preparing documents for SLTDA submission.

**Input Parameters:**

| Parameter | Type | Required | Values |
|-----------|------|----------|--------|
| business_type | string | Yes | Any value from business_categories |
| checklist_type | enum | Yes | `registration` / `renewal` / `inspection` |

**Output:**

```
{
  "business_type": string,
  "checklist_type": string,
  "items": [
    {
      "item_number": integer,
      "document_name": string,
      "description": string,
      "is_mandatory": boolean,
      "notes": string | null
    }
  ],
  "total_items": integer,
  "mandatory_items": integer,
  "source_document_url": string
}
```

**Data source:** PostgreSQL (structured extraction from checklist PDFs)
**Expected latency:** < 100ms

---

### Cluster 2 — Financial & Tax

---

#### Tool: `get_financial_concessions`

**Purpose:** Returns all available financial concessions, tax relief measures, and banking circulars applicable to tourism businesses.

**When Claude should call this:** User asks about financial relief, post-crisis concessions, tax breaks, banking support, or moratoriums for tourism operators.

**Input Parameters:**

| Parameter | Type | Required | Values |
|-----------|------|----------|--------|
| business_type | string | No | Filter by business type; null = all |
| concession_type | enum | No | `tax` / `banking` / `moratorium` / `levy` / `all` |

**Output:**

```
{
  "concessions": [
    {
      "name": string,
      "type": string,
      "applicable_to": [string],
      "terms": string,
      "conditions": string,
      "effective_from": date,
      "circular_reference": string,
      "source_document": { "name": string, "url": string }
    }
  ],
  "total_count": integer
}
```

**Data source:** PostgreSQL (`financial_concessions`)
**Expected latency:** < 100ms

---

#### Tool: `get_tdl_information`

**Purpose:** Returns Tourism Development Levy details including rates, clearance process, and required documentation.

**When Claude should call this:** User asks about TDL, tourism levy, clearance certificates, or TDL compliance requirements.

**Input Parameters:**

| Parameter | Type | Required | Values |
|-----------|------|----------|--------|
| query_type | enum | Yes | `overview` / `rate` / `clearance_process` / `required_documents` / `form_download` |

**Output:**

```
{
  "query_type": string,
  "content": string,         // narrative explanation
  "structured_data": object, // rate table / step list / document list depending on query_type
  "gazette_url": string,
  "clearance_form_url": string | null,
  "checklist_url": string | null
}
```

**Data source:** PostgreSQL (TDL-specific structured tables) + document URLs
**Expected latency:** < 100ms

---

#### Tool: `get_tax_rate`

**Purpose:** Returns applicable tax rates for tourism businesses under SLTDA's framework.

**When Claude should call this:** User asks about tax rates, VAT, tourism tax, or which tax regime applies to their business type.

**Input Parameters:**

| Parameter | Type | Required | Values |
|-----------|------|----------|--------|
| business_type | string | No | Filter by type; null = all applicable rates |

**Output:**

```
{
  "rates": [
    {
      "tax_type": string,
      "rate_percentage": number,
      "applicable_to": [string],
      "conditions": string,
      "circular_reference": string,
      "effective_from": date
    }
  ],
  "notes": string
}
```

**Data source:** PostgreSQL (`financial_concessions` filtered by type=tax)
**Expected latency:** < 80ms

---

### Cluster 3 — Statistics & Reports

---

#### Tool: `get_latest_arrivals_report`

**Purpose:** Returns metadata and download reference for the latest SLTDA tourist arrivals report.

**When Claude should call this:** User asks for current tourism statistics, latest arrival numbers, or wants to access SLTDA statistical reports.

**Input Parameters:**

| Parameter | Type | Required | Values |
|-----------|------|----------|--------|
| report_type | enum | Yes | `monthly` / `annual` |
| year | integer | No | 2015–2026; null = latest available |

**Output:**

```
{
  "report_type": string,
  "period_covered": string,
  "publication_date": date,
  "document_name": string,
  "download_url": string,
  "file_size_kb": integer,
  "key_figures": {
    "total_arrivals": integer | null,
    "yoy_change_percent": number | null,
    "top_source_market": string | null
  },
  "statistics_page_url": string
}
```

**Data source:** PostgreSQL (documents table, section 9)
**Note:** `key_figures` populated only if structured extraction succeeded on the report PDF; otherwise null with URL provided.
**Expected latency:** < 100ms

---

#### Tool: `get_annual_report`

**Purpose:** Returns metadata and download link for SLTDA Annual Reports (2015–2024) in any available language.

**When Claude should call this:** User asks for SLTDA annual report for a specific year, or wants historical organizational/financial data.

**Input Parameters:**

| Parameter | Type | Required | Values |
|-----------|------|----------|--------|
| year | integer | Yes | 2015–2024 |
| language | enum | No | `english` / `sinhala` / `tamil` — default: english |

**Output:**

```
{
  "year": integer,
  "language": string,
  "document_name": string,
  "download_url": string,
  "available_languages": [string],
  "file_size_kb": integer
}
```

**Data source:** PostgreSQL (documents table, section 10)
**Expected latency:** < 80ms

---

### Cluster 4 — Strategy & Policy

---

#### Tool: `get_strategic_plan`

**Purpose:** Answers questions about Sri Lanka Tourism Strategic Plan 2022–2025 using RAG over the full document.

**When Claude should call this:** User asks about government tourism targets, strategic priorities, recovery plans, sustainability goals, or long-term tourism direction.

**Input Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| query | string | Yes | Natural language question about the strategic plan |
| section_focus | string | No | Optional hint: `recovery` / `sustainability` / `targets` / `investment` / `niche` |

**Output:**

```
{
  "answer": string,          // Gemini Flash synthesized response
  "source_chunks": [
    {
      "chunk_text": string,
      "page_numbers": [integer],
      "relevance_score": number
    }
  ],
  "document": { "name": string, "url": string },
  "confidence": "high" | "medium" | "low"
}
```

**Data source:** Qdrant RAG (strategic plan document only) + Gemini Flash
**Expected latency:** 400–900ms

---

#### Tool: `get_tourism_act_provisions`

**Purpose:** Retrieves relevant provisions from Tourism Act No. 38 of 2005 for a given topic or legal question.

**When Claude should call this:** User asks about legal requirements, penalties, authority powers, licensing obligations, or any statutory basis for SLTDA regulation.

**Input Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| topic | string | Yes | e.g., "registration penalties", "classification authority", "tour guide licensing" |

**Output:**

```
{
  "relevant_provisions": [
    {
      "section_number": string | null,
      "section_title": string | null,
      "provision_text": string,
      "page_number": integer | null
    }
  ],
  "answer_summary": string,
  "document": { "name": string, "url": string },
  "confidence": "high" | "medium" | "low",
  "legal_disclaimer": string
}
```

**Data source:** Qdrant RAG (Tourism Act document only) + Gemini Flash
**Expected latency:** 400–900ms

---

### Cluster 5 — Niche Tourism

---

#### Tool: `get_niche_categories`

**Purpose:** Returns the complete list of SLTDA-recognized niche tourism categories with descriptions and development status.

**When Claude should call this:** User asks what types of specialized tourism Sri Lanka supports, or wants to explore niche opportunities.

**Input Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| filter | string | No | Optional keyword filter (e.g., "wellness", "outdoor") |

**Output:**

```
{
  "categories": [
    {
      "code": string,
      "name": string,
      "target_market": string,
      "key_activities": [string],
      "toolkit_available": boolean,
      "toolkit_url": string | null
    }
  ],
  "total_count": integer
}
```

**Data source:** PostgreSQL (`niche_toolkits`)
**Expected latency:** < 80ms

---

#### Tool: `get_niche_toolkit`

**Purpose:** Returns strategic guidance and key content from a specific niche tourism toolkit.

**When Claude should call this:** User asks about developing a specific type of tourism product, wants guidance on a niche segment, or is planning an investment in a specialized tourism category.

**Input Parameters:**

| Parameter | Type | Required | Values |
|-----------|------|----------|--------|
| category | enum | Yes | `adventure` / `agro` / `wellness` / `digital_nomad` / `mice` / `weddings` / `food_drink` / `heritage` / `nature_wildlife` / `volunteer` / `festivals` / `brand` / `hosted_travel` |
| detail_level | enum | No | `summary` / `full` — default: summary |

**Output:**

```
{
  "toolkit_name": string,
  "target_market": string,
  "key_activities": [string],
  "regulatory_notes": string,
  "summary": string,           // pre-computed Gemini summary
  "full_content": string | null, // only if detail_level = full (RAG)
  "download_url": string,
  "related_categories": [string]
}
```

**Data source:** PostgreSQL (summary) + Qdrant RAG (full detail)
**Expected latency:** 80ms (summary) / 500ms (full)

---

### Cluster 6 — Investor & Discovery

---

#### Tool: `get_investment_process`

**Purpose:** Returns the SLTDA investment application process, required forms, and approval pathway for tourism development projects.

**When Claude should call this:** User asks about investing in Sri Lanka tourism, starting a hotel project, SLTDA approval process for new developments, or foreign investment requirements.

**Input Parameters:**

| Parameter | Type | Required | Values |
|-----------|------|----------|--------|
| project_type | string | No | e.g., `hotel` / `resort` / `eco_lodge` — null = general |

**Output:**

```
{
  "process_steps": [
    {
      "step": integer,
      "title": string,
      "description": string,
      "forms_required": [{ "name": string, "url": string }]
    }
  ],
  "contact": { "unit": string, "email": string | null, "phone": string | null },
  "investor_unit_info_url": string,
  "common_application_url": string
}
```

**Data source:** PostgreSQL + Qdrant RAG (investor unit subpage content)
**Expected latency:** 100–400ms

---

#### Tool: `search_sltda_resources`

**Purpose:** Free-text semantic search across all indexed SLTDA documents. Discovery tool for queries that don't map to a specific tool.

**When Claude should call this:** User's query doesn't clearly match any specific tool, or user wants to explore what resources are available on a topic. Use as fallback when other tools return `not_found`.

**Input Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| query | string | Yes | Free-text search query |
| section_filter | string | No | Limit to specific SLTDA section |
| document_type_filter | string | No | gazette / form / toolkit / report / act / guideline |
| top_k | integer | No | Number of results (default: 5, max: 10) |

**Output:**

```
{
  "results": [
    {
      "rank": integer,
      "document_name": string,
      "section": string,
      "document_type": string,
      "relevance_score": number,
      "excerpt": string,         // most relevant chunk text
      "download_url": string,
      "language": string
    }
  ],
  "total_results": integer,
  "query_interpreted_as": string  // Gemini Flash query understanding
}
```

**Data source:** Qdrant (full collection search) + Gemini Flash (query interpretation)
**Expected latency:** 300–700ms

---

## 8. RAG System Design

### 8.1 RAG Pipeline per Query

```
User Query (natural language)
         │
         ▼
┌────────────────────────┐
│  Query Preprocessing   │
│  - Detect language     │
│  - Expand acronyms     │
│    (TDL, SLTDA, etc.)  │
│  - Section hint inject │
└──────────┬─────────────┘
           │
           ▼
┌────────────────────────┐
│  Embedding             │
│  Gemini text-embed-004 │
│  Output: 768-dim vector│
└──────────┬─────────────┘
           │
           ▼
┌────────────────────────┐
│  Qdrant Search         │
│  Top-k: 6 chunks       │
│  Filter: by section    │
│          by doc_type   │
│          by language   │
│  Score threshold: 0.60 │
└──────────┬─────────────┘
           │
           ▼
┌────────────────────────┐
│  Context Assembly      │
│  Sort by chunk_index   │
│  Deduplicate by doc    │
│  Max context: 4000 tok │
└──────────┬─────────────┘
           │
           ▼
┌────────────────────────┐
│  Gemini Flash Synthesis│
│  System prompt:        │
│  - Grounded answering  │
│  - Cite source docs    │
│  - Note confidence     │
│  - Add disclaimer flag │
└──────────┬─────────────┘
           │
           ▼
     Structured Response
     (answer + sources + confidence)
```

### 8.2 RAG System Prompt Template

The Gemini Flash synthesis prompt follows a strict template:

```
You are an expert on Sri Lanka tourism regulations and SLTDA (Sri Lanka Tourism
Development Authority) policies. Answer the user's question using ONLY the
provided document excerpts. Do not use outside knowledge.

Rules:
- If the answer is in the excerpts, provide it clearly and completely
- If only partial information is available, answer what you can and note the gap
- If the excerpts don't contain relevant information, say "Not found in available
  documents" — do not fabricate
- Always reference which document the information comes from
- Use plain, professional language suitable for tourism business operators
- Do not provide legal or financial advice; note when professional consultation
  is recommended

Document Excerpts:
{context_chunks}

User Question: {query}

Answer:
```

### 8.3 Retrieval Quality Monitoring

Track per tool call:

- Average similarity score of retrieved chunks
- Percentage of queries hitting `not_found` (too many = indexing gap)
- Percentage of `low` confidence responses (too many = chunk quality issue)
- Query topics that consistently underperform → trigger manual review

---

## 9. Infrastructure & Deployment

### 9.1 Service Components

| Service | Technology | Port | Purpose |
|---------|-----------|------|---------|
| `sltda-mcp` | FastMCP (Python 3.11) | 8001 | MCP server (SSE transport) |
| `postgres` | PostgreSQL 16 | 5432 | Structured data |
| `qdrant` | Qdrant latest | 6333 | Vector search |
| `ingestion` | Python (Airflow-lite / cron) | — | Monthly refresh pipeline |
| `mcp-stdio` | FastMCP stdio wrapper | — | Claude Desktop local transport |

### 9.2 Docker Compose Architecture

```
docker-compose.yml
├── service: postgres
│   image: postgres:16-alpine
│   volumes: postgres_data:/var/lib/postgresql/data
│   env: POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD
│
├── service: qdrant
│   image: qdrant/qdrant:latest
│   volumes: qdrant_storage:/qdrant/storage
│   ports: 6333:6333, 6334:6334
│
├── service: sltda-mcp
│   build: ./mcp_server
│   depends_on: [postgres, qdrant]
│   ports: 8001:8001
│   env: POSTGRES_URL, QDRANT_URL, GEMINI_API_KEY
│   volumes: ./documents:/app/documents:ro
│
├── service: ingestion
│   build: ./ingestion
│   depends_on: [postgres, qdrant]
│   volumes: ./documents:/app/documents
│   profiles: [ingestion]   # only runs on demand / schedule
│
└── volumes:
    postgres_data:
    qdrant_storage:
```

### 9.3 Environment Configuration

```
# Required environment variables
POSTGRES_URL=postgresql://user:pass@postgres:5432/sltda_mcp
QDRANT_URL=http://qdrant:6333
GEMINI_API_KEY=...

# Optional tuning
MCP_MAX_CONCURRENCY=20
MCP_TOOL_TIMEOUT_SECONDS=30
RAG_TOP_K_CHUNKS=6
RAG_SIMILARITY_THRESHOLD=0.60
INGESTION_RATE_LIMIT_RPS=1.0
LOG_LEVEL=INFO
REFRESH_NOTIFY_SLACK_WEBHOOK=...   # Optional alerting
```

### 9.4 Claude Desktop Integration

For local use with Claude Desktop, the server runs in stdio mode. The Claude Desktop config entry:

```json
{
  "mcpServers": {
    "sltda": {
      "command": "docker",
      "args": ["run", "--rm", "-i",
               "--network", "sltda-mcp_default",
               "-e", "POSTGRES_URL=...",
               "-e", "QDRANT_URL=...",
               "-e", "GEMINI_API_KEY=...",
               "sltda-mcp:latest",
               "python", "-m", "sltda_mcp.stdio"]
    }
  }
}
```

### 9.5 Resource Requirements

| Resource | Minimum | Recommended |
|----------|---------|-------------|
| CPU | 2 cores | 4 cores |
| RAM | 4 GB | 8 GB |
| Disk | 10 GB | 20 GB |
| Network | Outbound HTTP (SLTDA, Gemini API) | — |

Qdrant with ~50,000 vectors at 768 dimensions requires approximately 300–400 MB RAM for the index. PostgreSQL with this data volume is negligible. The MCP server itself is stateless and lightweight.

---

## 10. Observability & Reliability

### 10.1 Logging Strategy

All components emit structured JSON logs with the following standard fields:

```json
{
  "timestamp": "ISO8601",
  "service": "sltda-mcp | ingestion | postgres",
  "level": "INFO | WARNING | ERROR",
  "event": "tool_call | ingestion_step | qdrant_query | ...",
  "trace_id": "UUID per request",
  "duration_ms": integer,
  "tool_name": string | null,
  "error": string | null
}
```

### 10.2 Health Checks

**MCP Server health endpoint** (`GET /health`):

```json
{
  "status": "healthy | degraded | unhealthy",
  "components": {
    "postgres": "connected | error",
    "qdrant": "connected | error",
    "gemini_api": "reachable | error",
    "document_store": "ok | missing_files"
  },
  "last_refresh": "ISO8601 date",
  "total_documents": integer,
  "total_vectors": integer,
  "uptime_seconds": integer
}
```

**Smoke test suite** — runs after every ingestion pipeline completion:

- Call each of the 14 tools with a known valid input
- Assert response status = `success`
- Assert response time within threshold
- Log pass/fail per tool

### 10.3 Alerting Conditions

| Condition | Alert Level | Action |
|-----------|-------------|--------|
| Ingestion pipeline fails | ERROR | Slack + email |
| Any tool smoke test fails post-refresh | WARNING | Slack |
| Qdrant vector count drops > 10% | ERROR | Slack + pause serving |
| PostgreSQL connection lost | CRITICAL | Immediate |
| Gemini API error rate > 5% in 5 min | WARNING | Slack |
| Document freshness > 45 days | INFO | Slack reminder |
| SLTDA page structure change detected | WARNING | Slack (manual review needed) |

### 10.4 Ingestion Reliability

| Risk | Mitigation |
|------|-----------|
| SLTDA site down during scheduled refresh | Retry next day; serve stale data with freshness note |
| PDF download timeout | 3 retries with exponential backoff; skip and alert |
| PDF format changes year-over-year | Content hash check; route to manual review if parse yield drops |
| Gemini API quota exceeded during embedding | Batch with delay; resume from checkpoint (track last embedded chunk_id) |
| Qdrant write failure mid-upsert | Transactional upsert with rollback to last known good state |

---

## 11. Security & Ethics

### 11.1 Data Use Policy

- All content served originates from `sltda.gov.lk` — a public Sri Lankan government website
- No scraping of authenticated or paywalled content
- `User-Agent` header identifies the project: `sltda-mcp-research/1.0`
- Rate limiting: maximum 1 request/second to SLTDA servers during ingestion
- No redistribution of raw PDFs — only metadata and URLs are stored; users are directed to SLTDA for downloads
- Document texts are stored locally for indexing purposes only (transformative use for research/portfolio)

### 11.2 Disclaimer Strategy

Every tool response includes a `disclaimer` field. The system never presents itself as an authoritative legal or regulatory source. Specific disclaimers:

- **Freshness disclaimer** on every response: last refresh date prominently noted
- **Legal disclaimer** on Tourism Act tool: recommend qualified legal counsel
- **Financial disclaimer** on tax/TDL tools: recommend professional tax advice
- **OCR disclaimer** on scanned document responses: accuracy may be reduced

### 11.3 No PII

The system collects no user data beyond tool invocation logs (which contain only tool names, parameters, and performance metrics — no user identifiers). The `tool_invocation_log` table has a 90-day retention policy.

---

## 12. Phased Implementation Plan

### Phase 1 — Foundation (Days 1–7)

**Goal:** Data layer complete; all documents downloaded and indexed.

| Task | Effort | Output |
|------|--------|--------|
| Audit SLTDA downloads page; catalog all documents | 1 day | `document_catalog.csv` |
| Set up Docker Compose (Postgres + Qdrant) | 0.5 day | `docker-compose.yml` |
| Build page scraper (metadata extraction) | 1 day | `ingestion/scraper.py` |
| Build PDF downloader with rate limiting | 0.5 day | `ingestion/downloader.py` |
| Build PDF parser (pdfplumber + OCR fallback) | 1.5 days | `ingestion/parser.py` |
| Run full download + parse of all 50+ documents | 0.5 day | `documents/` populated |
| Build chunker + Gemini embedder | 1 day | `ingestion/embedder.py` |
| Upsert all chunks to Qdrant | 0.5 day | `sltda_documents` collection |

**Milestone:** All documents indexed in Qdrant; Postgres schema migrated.

---

### Phase 2 — Structured Tools (Days 8–14)

**Goal:** Cluster 1, 2, 3 tools working (9 structured tools).

| Task | Effort | Output |
|------|--------|--------|
| Build structured data extractors (registration steps, financial concessions, business categories, TDL) | 2 days | PostgreSQL tables populated |
| Scaffold FastMCP server | 0.5 day | `mcp_server/main.py` |
| Implement Cluster 1 tools (3 tools) | 1.5 days | Registration tools passing tests |
| Implement Cluster 2 tools (3 tools) | 1 day | Financial tools passing tests |
| Implement Cluster 3 tools (2 tools) | 0.5 day | Statistics tools passing tests |
| Write tool descriptions (critical for Claude routing) | 1 day | All 9 tools documented |
| Unit tests for all structured tools | 0.5 day | `tests/test_structured_tools.py` |

**Milestone:** 9 structured tools working; tested with Claude Desktop.

---

### Phase 3 — RAG Tools (Days 15–21)

**Goal:** Cluster 4, 5, 6 tools working (5 RAG-backed tools).

| Task | Effort | Output |
|------|--------|--------|
| Build RAG retrieval module (Qdrant query + Gemini synthesis) | 1.5 days | `mcp_server/rag.py` |
| Implement `get_strategic_plan` tool | 0.5 day | |
| Implement `get_tourism_act_provisions` tool | 0.5 day | |
| Implement `get_niche_toolkit` (full detail mode) | 0.5 day | |
| Implement `get_investment_process` tool | 0.5 day | |
| Implement `search_sltda_resources` tool | 1 day | |
| RAG quality evaluation (20 test queries) | 1 day | `tests/rag_eval_results.json` |
| Tune chunking / retrieval params based on eval | 0.5 day | |

**Milestone:** All 14 tools operational; RAG quality > 80% on test set.

---

### Phase 4 — Reliability & Polish (Days 22–28)

**Goal:** Production-ready: ingestion pipeline, monitoring, Docker, documentation.

| Task | Effort | Output |
|------|--------|--------|
| Build full ingestion pipeline with change detection | 1.5 days | `ingestion/pipeline.py` |
| Add structured logging throughout | 0.5 day | JSON logs |
| Build smoke test suite | 1 day | `tests/smoke_tests.py` |
| Add health endpoint | 0.5 day | `GET /health` |
| Configure monthly cron / Airflow DAG | 0.5 day | Scheduled refresh |
| Write `claude_desktop_config.json` example | 0.5 day | Integration guide |
| Write `README.md` with architecture diagram | 1 day | Public documentation |
| Full end-to-end demo (10 representative queries) | 0.5 day | Demo recording |

**Milestone:** One-command `docker compose up` starts full system. Demo-ready.

---

## 13. Testing Strategy

### Unit Tests — Per Tool

Each tool has a unit test covering:

- Happy path with valid input → expected output structure
- Edge case: unknown business_type → graceful `not_found` response
- Edge case: missing optional params → defaults applied correctly
- Output envelope validation (all required fields present)

### Integration Tests — Tool Chain

Test multi-tool scenarios as an agent would use them:

1. `get_registration_requirements(boutique_hotel, register)` → follow up with `get_accommodation_standards(boutique_villa)`
2. `get_financial_concessions(all)` → follow up with `get_tdl_information(clearance_process)`
3. `search_sltda_resources("eco lodge certification")` → follow up with specific tool

### RAG Evaluation — 20 Ground Truth Queries

A manually curated set of 20 question-answer pairs derived from the actual documents. Run after every ingestion pipeline. Evaluate:

- Correctness: Does the answer match the ground truth?
- Grounding: Is the answer supported by the retrieved chunks?
- No hallucination: Does the answer contain claims not in the documents?

Target: 80%+ correct on ground truth set.

### Smoke Tests — Post-Ingestion

After every ingestion run, call all 14 tools with a fixed valid input and assert:

- Status = `success`
- Response time within threshold
- Required fields present
- No empty `data` objects

### Load Tests — Basic

Simulate 20 concurrent tool calls using a test harness. Assert:

- No tool call fails under concurrent load
- P95 latency within thresholds (structured < 200ms, RAG < 1500ms)
- No connection pool exhaustion

---

## 14. Maintenance & Operations

### Monthly Refresh Checklist

The refresh follows the zero-downtime blue/green pattern described in Section 5.4. The server remains fully operational throughout.

**Pre-Ingestion**

1. Verify PostgreSQL and Qdrant are healthy (`GET /health`)
2. Confirm staging tables from any previous failed run are cleaned up
3. Confirm `sltda_documents_next` collection does not already exist in Qdrant

**Ingestion Run**
4. Trigger ingestion pipeline (`docker compose run ingestion python -m ingestion.pipeline`)
5. Monitor pipeline log — watch for download failures, parse errors, embedding quota warnings
6. Pipeline writes only to staging targets — production serving is unaffected throughout

**Pre-Cutover Validation**
7. Smoke test suite runs automatically against staging — review results
8. If any smoke test fails: investigate staging data, fix extractor, re-run pipeline — do NOT force cutover
9. Check RAG eval score on staging vs previous month's benchmark (target: no regression > 5%)
10. Review `tool_invocation_log` for high-frequency `not_found` patterns — ensure new index addresses known gaps

**Atomic Cutover**
11. Pipeline executes cutover transaction automatically if all tests pass
12. Verify `last_refresh_at` updated in `system_metadata`
13. Verify `GET /health` shows new document count and updated `last_refresh` date
14. Run one manual spot-check tool call per cluster (6 calls total) to confirm live serving

**Post-Cutover (48-hour window)**
15. Monitor `tool_invocation_log` for error spikes in first 2 hours post-cutover
16. Monitor for any anomalous `not_found` rates that didn't exist before cutover
17. If issues found: execute rollback (single reverse rename transaction, < 1 second)
18. After 48 hours with no issues: drop `documents_old`, `registration_steps_old`, and previous Qdrant collection

### SLTDA Site Change Handling

SLTDA periodically restructures their downloads page. The ingestion pipeline detects:

- New sections or documents → auto-ingest
- Removed documents → flag as `is_active=false` in PostgreSQL; retain in Qdrant with staleness flag
- URL changes → update metadata; re-download if hash differs
- Page structure changes breaking scraper → alert triggered; manual investigation required

When a significant page restructure occurs, the scraper's section-mapping configuration (`ingestion/config/section_map.yaml`) requires manual update before the next pipeline run.

### Tool Description Updates

Tool descriptions (what Claude reads to decide which tool to call) should be reviewed every 3 months or when new SLTDA content is added. Poor routing is almost always caused by mismatched tool descriptions, not code bugs.

### Dependency Versioning

| Dependency | Pin Strategy |
|-----------|-------------|
| FastMCP | Pin to minor version (breaking changes possible) |
| Qdrant client | Pin to minor version |
| pdfplumber | Pin to patch version |
| Gemini SDK | Pin to minor version |
| PostgreSQL | Pin Docker image to `16-alpine` |

---

## 15. Portfolio Positioning

### Skills Demonstrated

| Skill | Evidence |
|-------|---------|
| MCP Protocol | Production FastMCP server with 14 tools, proper tool contracts, stdio + SSE transport |
| RAG Architecture | Qdrant + Gemini embeddings + synthesis; chunking strategy; quality evaluation; query expansion |
| Data Engineering | Web scraping, PDF parsing, dynamic format classification, change detection, validation pipeline |
| System Design | Full layered architecture: ingestion → format identifier → structured DB → vector DB → MCP → clients |
| API Design | Consistent response envelopes, error handling, confidence scoring, staleness flags, disclaimers |
| MLOps Thinking | Smoke tests, RAG eval, structured logging, alerting, zero-downtime blue/green refresh, 48-hour rollback window |
| Production Awareness | 26-issue risk registry with severity classification, detection strategies, and phased mitigations |
| Domain Expertise | Sri Lanka tourism regulation; English-only scope decision; format family classification |
| DevOps | Docker Compose; environment config; one-command startup; backup strategy |

### Differentiation

- **Novel:** No public MCP server exists for Sri Lanka tourism regulation
- **Practical:** Solves a real problem — regulatory PDFs are genuinely hard to navigate
- **Production patterns:** Not a demo/toy — change detection, RAG eval, structured logging, health checks
- **AI-native:** Designed for agent consumption from day one, not retrofitted
- **Composable:** Integrates cleanly with TourismPulse LK's LangGraph agents as a tool cluster

### Suggested Tagline

> *"SLTDA's 50+ regulatory PDFs, distilled into 14 AI-callable tools."*

---

---

## 16. Production Issue Registry

This registry documents all known production-scale risks identified during system design. Each issue includes a severity classification, detection difficulty, the phase during which it manifests, a description of the failure mode, and the recommended mitigation to implement during development. Issues are ordered by severity within each category.

**Severity Definitions:**

- `Critical` — Silent data corruption or wrong answers with no error signal; user impact immediate
- `High` — Significant wrong behaviour; may not be immediately obvious; business impact potential
- `Medium` — Degraded quality or availability; detectable with monitoring; recoverable
- `Low` — Minor degradation; user-visible but not business-critical; easily diagnosed

**Detection Difficulty:**

- `Hard` — No error thrown; system appears healthy; wrong output only discovered by content audit
- `Medium` — Detectable with proper monitoring/alerting but not immediately obvious
- `Easy` — Throws errors or produces obviously wrong output; caught quickly

---

### Category 1: Data Ingestion & Source Reliability

---

#### Issue #1 — CAPTCHA Stored as PDF, Garbage Indexed

**Severity:** Critical | **Detection:** Hard | **Phase:** Ingestion → Embedding

**Failure Mode:** SLTDA's server rate-limits or blocks the pipeline's IP, returning a 403 redirect to a CAPTCHA or error HTML page with HTTP 200. The downloader sees a valid response, stores the HTML as a `.pdf` file. pdfplumber extracts near-zero text, triggers the `scan_detected` path, Tesseract OCR runs on a CAPTCHA image, and garbage text gets embedded into Qdrant. Tools subsequently return confidently grounded answers based on nonsense chunks — no pipeline error, no alert.

**Mitigation to implement:**

- After download, validate PDF magic bytes (`%PDF` at byte offset 0) before accepting the file
- Enforce minimum file size threshold: reject any downloaded file < 5KB
- After pdfplumber extraction, if text yield < 50 chars AND file > 10KB, classify as `suspicious_content` and flag for manual review — do not route to OCR
- Log HTTP response headers (Content-Type, X-Cache, CF-RAY) to detect CDN/WAF interception
- Add exponential backoff with jitter between downloads; cap at 1 req/2sec during ingestion

---

#### Issue #2 — SLTDA PDF URLs Becoming Stale Between Refresh Cycles

**Severity:** Low | **Detection:** Easy | **Phase:** Serving

**Failure Mode:** Tool responses include `download_url` fields pointing to SLTDA's server. Between monthly ingestion runs, SLTDA may move or rename files. Users clicking tool-returned URLs get 404 errors. If the document content didn't change (only the URL), the hash-based change detector won't catch it — stale URLs persist indefinitely.

**Mitigation to implement:**

- Store `last_url_verified_at` in the `documents` table
- Run a lightweight URL HEAD-check job weekly (separate from full ingestion) — check HTTP status only, no download
- Flag documents where HEAD returns non-200 as `url_suspect`; include warning in tool response `disclaimer` field
- On next full ingestion, re-scrape page to find new URL for flagged documents

---

#### Issue #3 — PDF Format Drift Breaking Structured Extractors

**Severity:** High | **Detection:** Hard | **Phase:** Structured Extraction → PostgreSQL

**Failure Mode:** SLTDA reformats a document (new staff, redesigned template). The extractor written against the old format runs without error but produces empty columns, misaligned rows, or incorrect structured data in `registration_steps` or `financial_concessions`. Smoke tests pass (they check structure, not content accuracy). Wrong regulatory information enters production.

**Mitigation to implement:**

- **Resolved by Section 5.5 Dynamic Format Identifier** — format drift is detected as a similarity score drop, routing the document to `FallbackExtractor` + `format_review_queue` + alert rather than running the wrong extractor silently
- Additionally: post-extraction content validation rules per extractor (e.g., `StepsExtractor` must produce ≥ 2 steps; `CircularExtractor` must produce at least one fee amount > 0)
- Monthly: spot-check 3 random structured records against source PDFs

---

#### Issue #4 — Gemini Embedding API Quota Exhaustion Mid-Pipeline

**Severity:** Medium | **Detection:** Medium | **Phase:** Embedding

**Failure Mode:** Gemini's per-minute or per-day quota is hit midway through embedding. Without checkpointing, pipeline restart re-embeds from the beginning — wasting quota and potentially creating duplicate vectors in `sltda_documents_next`. Duplicate vectors inflate similarity scores and surface redundant chunks in RAG retrieval, degrading answer quality.

**Mitigation to implement:**

- Implement embedding checkpoint: track `last_embedded_chunk_id` per ingestion run in a `pipeline_state` table
- On restart, skip chunks with `chunk_id <= last_embedded_chunk_id`
- Before Qdrant upsert, deduplicate by `document_id + chunk_index` — reject any point whose composite key already exists in the staging collection
- Implement per-minute rate limiter in the embedder (stay at 80% of quota ceiling)

---

#### Issue #5 — Partial Qdrant Collection State After Pipeline Abort

**Severity:** Medium | **Detection:** Medium | **Phase:** Qdrant Upsert → Cutover

**Failure Mode:** Pipeline aborts after partially upserting vectors into `sltda_documents_next` (e.g., 60% of documents indexed). If the next pipeline run doesn't clean up the partial collection first, a re-run upserts into a hybrid collection — some documents from the new run, some from the aborted partial run. Cutover proceeds with the hybrid index; some queries hit stale chunks with no indication of which version they came from.

**Mitigation to implement:**

- At pipeline start, check if `sltda_documents_next` already exists; if so, delete it entirely before starting a fresh upsert
- Log the deletion action explicitly — distinguish between "cleaned up previous failed run" and "fresh start"
- Add point count assertion at end of upsert: expected count = total chunks from this run ± 5%; abort if outside range

---

### Category 2: Data Quality & Content Integrity

---

#### Issue #6 — Scoped Out: Sinhala/Tamil Script Corruption

**Severity:** N/A | **Detection:** N/A | **Phase:** N/A

**Status: Eliminated by English-only scope decision (Section 5.2).** This issue — complex script extraction producing corrupted or empty text for Sinhala/Tamil PDFs — is fully resolved by limiting ingestion to English-language documents only. All SLTDA regulatory content is available in English with complete coverage.

---

#### Issue #7 — Table Row-Shifting in Structured Extraction

**Severity:** High | **Detection:** Hard | **Phase:** Structured Extraction

**Failure Mode:** The accommodation services table in Section 3 has merged cells and inconsistent column widths. pdfplumber's table detection misaligns columns when cell boundaries don't render cleanly — a document name lands in the checklist column, a gazette reference merges with guidelines text. `business_categories.gazette_document_id` points to the wrong document. Users receive incorrect gazette references for their business type.

**Mitigation to implement:**

- **Partially resolved by Format Identifier** — `DataTableExtractor` uses purpose-built column detection with explicit column count assertions
- Post-extraction validation: for `business_categories`, each row must have non-null values in `category_name`, `category_group`, and at least one document FK column
- Cross-reference validation: every `document_id` FK in `business_categories` must resolve to an existing row in `documents` — orphan FKs indicate misalignment
- Monthly: manually verify 5 random `business_categories` rows against source PDF

---

#### Issue #8 — Gemini Hallucinating Niche Toolkit Summaries

**Severity:** High | **Detection:** Hard | **Phase:** Structured Extraction → `niche_toolkits`

**Failure Mode:** Toolkit PDFs use decorative fonts and text-as-images in headers and call-out boxes. pdfplumber extracts only partial text. Gemini Flash receives poor, incomplete input but still produces a confident-sounding summary. The fabricated summary is stored in `niche_toolkits.summary` and served verbatim by `get_niche_toolkit` with no indication of partial source quality.

**Mitigation to implement:**

- **Partially resolved by Format Identifier** — Gemini summarization only runs when `niche_toolkit` classification confidence ≥ 0.85
- Before Gemini summarization, check extracted text length: if < 800 tokens for a document > 5 pages, flag as `low_extraction_yield` and skip summarization; serve `full_text_url` instead
- Append extraction yield metadata to the summary record: `source_text_tokens`, `source_pages`, `extraction_confidence`
- Tool response includes extraction confidence when `detail_level = summary`

---

#### Issue #9 — Document Deduplication Failure Creating Redundant Vectors

**Severity:** Low | **Detection:** Medium | **Phase:** Embedding → Qdrant

**Failure Mode:** SLTDA hosts some documents under multiple URLs (linked from both the main page and a subsection page). Without hash-based deduplication before embedding, multiple copies of the same document's chunks enter Qdrant. RAG retrieval surfaces the same text multiple times in the context window, consuming token budget and producing repetitive answers.

**Mitigation to implement:**

- Content hash (`SHA-256` of extracted text) deduplication before embedding: if hash already exists in `documents` table for a different `document_id`, skip embedding and log as `duplicate_detected`
- Qdrant upsert uses `document_id + chunk_index` as the deterministic point ID — re-upserting the same document naturally overwrites rather than duplicates

---

### Category 3: MCP Server Runtime

---

#### Issue #10 — PostgreSQL Connection Pool Exhaustion Under Concurrent Load

**Severity:** Medium | **Detection:** Easy | **Phase:** Runtime — serving

**Failure Mode:** 20 concurrent tool calls, each requiring a PostgreSQL connection. RAG tools additionally open a Qdrant connection. Under burst load, the connection pool is exhausted, new calls queue, and if the queue fills, tool calls fail with connection timeout errors surfaced to the MCP client as opaque failures.

**Mitigation to implement:**

- Configure `asyncpg` connection pool with: min_size=5, max_size=15, max_inactive_connection_lifetime=300s
- Set MCP server `max_concurrency=15` (lower than connection pool ceiling to leave headroom)
- PostgreSQL `max_connections=50` in Docker Compose (sufficient for this scale)
- Add connection pool utilization metric to health endpoint: `pool_available / pool_total`
- Alert when pool utilization > 80% for > 60 seconds

---

#### Issue #11 — Qdrant Cold Start Timeout on First Query After Restart

**Severity:** Low | **Detection:** Easy | **Phase:** Runtime — post-restart

**Failure Mode:** Qdrant loads its HNSW index into memory on first query after startup. For a 50,000-vector collection at 768 dimensions, this takes several seconds. Concurrent RAG tool calls immediately post-restart all hit Qdrant cold simultaneously, causing cascading timeouts that appear as tool failures to clients.

**Mitigation to implement:**

- Add a warm-up step to the MCP server startup sequence: issue one dummy Qdrant search query after container start before accepting traffic
- Docker Compose `healthcheck` for the MCP service should verify Qdrant connectivity, not just HTTP port availability
- Qdrant `on_disk_payload: true` in collection config — keeps vectors on disk, reducing cold-start RAM spike

---

#### Issue #12 — Tool Description Similarity Causing Claude Routing Failures

**Severity:** Low | **Detection:** Hard | **Phase:** Runtime — agent routing

**Failure Mode:** `get_registration_requirements` and `get_accommodation_standards` serve related but distinct purposes. If their descriptions are too similar, Claude routes to the wrong tool for certain query types — producing `not_found` or off-topic answers. No infrastructure error; no alert. Systematic wrong routing for a subset of queries, invisible at the infrastructure level.

**Mitigation to implement:**

- Tool descriptions must include explicit negative examples: "Call this for X. Do NOT call this for Y — use `get_accommodation_standards` instead."
- Maintain a routing evaluation set: 30 test queries each mapped to the expected tool; run as part of smoke tests after any tool description change
- Review `tool_invocation_log` monthly: flag tool calls that return `not_found` and correlate with query text to identify routing gaps
- Tool descriptions reviewed every 3 months as part of maintenance schedule

---

#### Issue #13 — Gemini Flash Rate Limiting During Burst RAG Usage

**Severity:** Medium | **Detection:** Easy | **Phase:** Runtime — RAG synthesis

**Failure Mode:** At 20 concurrent tool calls with half being RAG tools, 10 simultaneous Gemini API calls are made. During a demo, an agent loop, or coordinated usage, per-minute rate limits are hit. Current design has no request queue or backpressure — rate-limited calls fail entirely rather than returning retrieved chunks without synthesis.

**Mitigation to implement:**

- Implement a Gemini API request queue with configurable concurrency cap (default: 5 simultaneous synthesis calls)
- On rate limit response (429), retry with exponential backoff up to 3 times before failing
- Graceful degradation fallback: if Gemini synthesis fails after retries, return retrieved chunks directly with a note: *"Synthesis unavailable; raw excerpts returned"* — partial answer is better than error
- Cache RAG synthesis responses for identical queries within a 24-hour window (Gemini responses for the same retrieved chunks + query are deterministic)

---

#### Issue #14 — stdio Transport Hanging on Large Tool Responses

**Severity:** Low | **Detection:** Medium | **Phase:** Runtime — Claude Desktop only

**Failure Mode:** Claude Desktop uses stdio transport. Large tool responses (e.g., `search_sltda_resources` with 10 results + full chunk texts, `get_tourism_act_provisions` with long provisions) produce 50–100KB JSON payloads. stdio buffering issues in some MCP client implementations cause the server to block on write, eventually timing out. Manifests as intermittent hangs on queries returning large responses, inconsistently reproducible.

**Mitigation to implement:**

- Cap `chunk_text` in tool responses at 500 characters (truncated with `…`); full text accessible via `download_url`
- Cap `search_sltda_resources` default results at 5 (not 10); hard cap at 7
- Set explicit `max_tokens` on Gemini synthesis output: 600 tokens maximum per RAG response
- Test with large responses during development on Claude Desktop specifically — not just SSE transport

---

### Category 4: Data Staleness & Consistency

---

#### Issue #15 — Misleading `last_refresh` Timestamp

**Severity:** Medium | **Detection:** Hard | **Phase:** Serving

**Failure Mode:** Every tool response shows `last_refresh_at` from `system_metadata`. But individual documents within that refresh may have failed to update — a PDF that returned a 403 was skipped and retained from the previous cycle. The timestamp makes all content appear current as of that date, when some documents may be one, two, or three cycles stale. A user gets an answer from last month's version of a recently updated circular with a timestamp that appears current.

**Mitigation to implement:**

- Add `content_as_of` field per document in the `documents` table: the date this specific document's content was last successfully parsed (not the pipeline run date)
- Tool responses for structured tools include `content_as_of` for each source document, not just the global `last_refresh_at`
- Documents not successfully updated in the last 2 ingestion cycles are flagged `stale` in `documents.status`; tool responses for stale-sourced content include an explicit staleness warning

---

#### Issue #16 — Split-Brain After Partial Cutover Failure

**Severity:** High | **Detection:** Hard | **Phase:** Cutover

**Failure Mode:** The cutover involves two separate atomic operations: a PostgreSQL transaction and a Qdrant alias reassignment. These are not part of a single distributed transaction. If PostgreSQL rename succeeds but Qdrant alias reassignment fails, the system enters split-brain: PostgreSQL serves new structured data, Qdrant serves the old collection. Structured tools return new data; RAG tools return old data. `GET /health` shows `healthy` — both services are individually reachable. Inconsistency persists until the next ingestion run.

**Mitigation to implement:**

- Perform Qdrant alias reassignment **before** PostgreSQL cutover transaction — if Qdrant fails, nothing has changed; if PostgreSQL fails after Qdrant, implement a compensating Qdrant alias rollback step
- Record cutover state in `system_metadata`: `cutover_status` = `pending` → `qdrant_done` → `postgres_done` → `complete`
- Health endpoint checks `cutover_status`: any value other than `complete` or `none` triggers a `degraded` health status with a descriptive message
- Post-cutover smoke test includes one structured tool + one RAG tool and asserts both return content from the same data generation (compare `last_refresh_at` in both responses)

---

#### Issue #17 — Rollback Window Data Integrity Risk

**Severity:** Low | **Detection:** Medium | **Phase:** Post-cutover rollback

**Failure Mode:** If any write occurs to the `documents` table between cutover and a rollback attempt (e.g., URL verification job updating `last_url_verified_at`), the rollback rename will fail due to schema conflict or will succeed but lose those writes. Manual intervention required.

**Mitigation to implement:**

- During the 48-hour rollback window, all jobs that write to `documents` are suspended (URL checker, on-demand refresh triggers)
- `system_metadata.rollback_available` flag is set `true` post-cutover and `false` after 48h cleanup — any write job checks this flag before executing
- Document the rollback procedure explicitly in the operations runbook with the caveat about write suspension

---

### Category 5: Operational & Infrastructure

---

#### Issue #18 — Docker Volume Data Loss on Host Restart

**Severity:** Medium | **Detection:** Easy (after the fact)

**Failure Mode:** All Qdrant and PostgreSQL data stored in Docker named volumes. `docker compose down -v` (which a developer may run accidentally during debugging) permanently deletes all indexed data. Re-running full ingestion from scratch takes 2–4 hours and consumes Gemini API quota. No backup mechanism in current design.

**Mitigation to implement:**

- Daily backup job: `pg_dump` of PostgreSQL → compressed file in `backups/postgres/` with 7-day retention
- Qdrant snapshot: use Qdrant's built-in snapshot API weekly → store in `backups/qdrant/`
- Mount backup directory as a Docker volume separate from data volumes (survives `down -v`)
- Add to onboarding README: explicit warning against `docker compose down -v`; document safe shutdown as `docker compose stop`

---

#### Issue #19 — Disk Full Causing PostgreSQL WAL Corruption

**Severity:** Medium | **Detection:** Medium

**Failure Mode:** Single Docker Compose host with no disk monitoring. When disk fills (documents/, backups/, PostgreSQL WAL, Docker layers all competing), PostgreSQL refuses new writes and may corrupt the WAL if it runs out of disk space mid-transaction. Recovery requires WAL replay or restore from backup.

**Mitigation to implement:**

- Add disk usage check to health endpoint: alert if disk > 80% full
- Configure Docker log rotation: `max-size: 50m, max-file: 3` in Docker Compose logging config
- Set `documents/raw/` retention policy: keep only the 3 most recent ingestion runs of raw PDFs (parsed and chunks are smaller; keep all)
- Qdrant and PostgreSQL data volumes: monitor size growth monthly; document expected growth rate (~500MB/year at this document scale)

---

#### Issue #20 — Gemini API Key Exposure

**Severity:** Low | **Detection:** N/A (preventable)

**Failure Mode:** `GEMINI_API_KEY` passed as Docker environment variable. Visible in `docker inspect`, process listings, and any component that accidentally logs its configuration. If `.env` is committed to a public repository, the key is exposed.

**Mitigation to implement:**

- `.env` in `.gitignore` from day one; `.env.example` with placeholder values committed instead
- Use Docker secrets for the key in any environment beyond local development
- Rotate key immediately if repository is ever made public without verifying `.env` exclusion
- Code review checklist item: confirm no environment variable logging in any pipeline component

---

#### Issue #21 — MCP Server Memory Leak Under Long-Running Operation

**Severity:** Medium | **Detection:** Medium

**Failure Mode:** FastMCP is a relatively new framework. Long-running processes handling thousands of tool calls may accumulate memory from connection objects not properly garbage collected or growing Qdrant client connection pools. Without memory monitoring, the MCP process is eventually OOM-killed by Docker daemon — unexpected downtime with no pipeline-related alert.

**Mitigation to implement:**

- Configure Docker Compose memory limit for `sltda-mcp` service: `mem_limit: 1g, memswap_limit: 1g`
- Add memory usage to health endpoint: current RSS and % of container limit
- Alert at 80% memory utilization
- Configure Docker `restart: unless-stopped` — automatic restart on OOM kill with logging
- Weekly: review memory growth trend in tool invocation logs; schedule preemptive restart if trending up

---

### Category 6: RAG Quality Degradation

---

#### Issue #22 — Chunk Boundary Splitting Regulatory Lists

**Severity:** High | **Detection:** Medium | **Phase:** Chunking → RAG retrieval

**Failure Mode:** Registration step lists and compliance checklists are numbered sequences. When chunked at token boundaries mid-list, a chunk may contain steps 1–4 and the next chunk contains steps 5–8. A RAG query for "all registration steps" retrieves the highest-similarity chunk — steps 1–4 — and the synthesized answer presents as complete while omitting half the steps. Partial compliance guidance presented as complete is the most dangerous failure mode in regulatory Q&A.

**Mitigation to implement:**

- **Resolved by Format Identifier chunk strategies** — `list_aware` strategy for `checklist_form` and `registration_steps` format families: never split within a numbered list; if a list exceeds the chunk size limit, keep the entire list as one oversized chunk rather than splitting
- Post-chunking validation: for `list_aware` chunks, assert that no chunk ends with a list item that has a continuation in the next chunk (check for trailing incomplete sentences after numbered items)
- RAG retrieval for registration-related queries: retrieve top-6 chunks and check if chunk indices are sequential from the same document — if so, merge into single context before synthesis

---

#### Issue #23 — Semantic Search Missing Sri Lankan Regulatory Terminology

**Severity:** Medium | **Detection:** Hard | **Phase:** RAG retrieval

**Failure Mode:** Tourism regulatory documents use precise legal terminology — "Tourist Establishment", "Classification Certificate", "Tourism Development Levy" — different from everyday language. A user asking "do I need a permit for my Airbnb" uses none of these terms. General-purpose embedding models may not bridge "Airbnb" → "Rented Home" or "short stay" → "Rented Apartment" well, particularly for Sri Lanka-specific regulatory categories underrepresented in training data. Queries using colloquial or non-Sri Lankan terminology systematically underperform.

**Mitigation to implement:**

- Build a **query expansion dictionary** for the RAG pipeline: a YAML file mapping colloquial terms to regulatory equivalents (`airbnb → rented home OR rented apartment`, `guesthouse → guest house`, `tour permit → registration certificate`)
- Before embedding a user query, expand it with regulatory synonyms: retrieve against both original and expanded query; take union of top results
- Include common colloquial terms in tool descriptions so Claude can pre-translate before calling the tool
- RAG evaluation set (Section 13) must include colloquial-language queries — not just formal regulatory language

---

#### Issue #24 — Context Window Stuffing Degrading Synthesis Quality

**Severity:** Medium | **Detection:** Medium | **Phase:** RAG synthesis

**Failure Mode:** Broad queries like "tell me everything about registering a hotel" retrieve 6 chunks from 6 different documents simultaneously. Gemini Flash receives 4,000 tokens of competing context and produces a generic, surface-level answer that doesn't do justice to any single source — worse than a focused query would produce.

**Mitigation to implement:**

- Query intent classifier (lightweight, rule-based) before RAG: detect broad "overview" queries vs specific queries
- For broad queries: route to structured tools first (`get_registration_requirements` + `get_accommodation_standards`); use RAG only for follow-up specifics
- For RAG retrieval: if top-6 chunks come from > 3 distinct documents, re-rank to prefer document coherence — take top-3 from the single most relevant document rather than 1 from each of 6
- Cap RAG context at 2,500 tokens (not 4,000) — forces tighter, more focused synthesis

---

### Category 7: Legal & Compliance

---

#### Issue #25 — Serving Superseded Regulatory Content

**Severity:** High | **Detection:** Hard | **Phase:** Serving

**Failure Mode:** SLTDA occasionally updates gazettes and circulars without removing old versions. Both old and new versions may be accessible under different URLs simultaneously. The pipeline ingests both, indexes both, and RAG retrieval may surface the superseded version — particularly if the old version has more text and scores higher on similarity. A hotel operator receives the wrong registration fee or an outdated compliance requirement and submits a non-compliant application.

**Mitigation to implement:**

- Document versioning: when two documents in the same section share > 80% text similarity but different filenames/dates, flag as potential supersession pair — add to `format_review_queue` for manual review
- `documents` table `superseded_by` FK column: when a newer version is confirmed, mark old version `is_superseded = true`; Qdrant points for superseded documents tagged with `superseded: true` in payload
- RAG retrieval filter: exclude `superseded: true` points from search results by default
- Tool response includes version note when source document has a `superseded_by` chain: *"Based on the most current version dated [date]."*

---

#### Issue #26 — No Audit Trail for Compliance-Critical Answers

**Severity:** Medium | **Detection:** N/A (architectural gap)

**Failure Mode:** Tourism operators use this system to make real business decisions — submitting registration applications, calculating levy payments. `tool_invocation_log` records what tool was called but not the full response content. If a user disputes that the system gave incorrect guidance, there is no way to reconstruct what answer was served at a specific point in time.

**Mitigation to implement:**

- Extend `tool_invocation_log` to include a `response_hash` field: SHA-256 of the full response JSON
- Store full response content for compliance-critical tools (`get_registration_requirements`, `get_tdl_information`, `get_financial_concessions`, `get_tourism_act_provisions`) in a separate `compliance_response_log` table with 90-day retention
- `compliance_response_log` schema: `id`, `tool_name`, `input_params`, `full_response_json`, `source_document_ids[]`, `called_at`
- Disclaimer in all compliance-critical tool responses: *"For record-keeping, note the `generated_at` timestamp. SLTDA content version: [last_refresh_at]."*

---

### Issue Severity Summary & Implementation Priority

Use this table during sprint planning to prioritise which mitigations to implement first.

| # | Issue | Category | Severity | Detection | Implement In |
|---|-------|----------|----------|-----------|--------------|
| 1 | CAPTCHA stored as PDF, garbage indexed | Ingestion | **Critical** | Hard | Phase 1 — Foundation |
| 16 | Split-brain after partial cutover failure | Consistency | **High** | Hard | Phase 4 — Reliability |
| 25 | Serving superseded regulatory content | Legal | **High** | Hard | Phase 4 — Reliability |
| 3 | PDF format drift breaking extractors | Ingestion | **High** | Hard | Phase 2 — resolved by §5.5 |
| 7 | Table row-shifting in structured extraction | Quality | **High** | Hard | Phase 2 — resolved by §5.5 |
| 8 | Gemini hallucinating toolkit summaries | Quality | **High** | Hard | Phase 2 — resolved by §5.5 |
| 22 | Chunk boundary splitting regulatory lists | RAG | **High** | Medium | Phase 2 — resolved by §5.5 |
| 4 | Gemini embedding quota exhaustion mid-pipeline | Ingestion | **Medium** | Medium | Phase 1 — Foundation |
| 5 | Partial Qdrant collection after abort | Ingestion | **Medium** | Medium | Phase 1 — Foundation |
| 10 | PostgreSQL connection pool exhaustion | Runtime | **Medium** | Easy | Phase 4 — Reliability |
| 13 | Gemini rate limiting during burst RAG | Runtime | **Medium** | Easy | Phase 4 — Reliability |
| 15 | Misleading `last_refresh` timestamp | Staleness | **Medium** | Hard | Phase 3 — RAG tools |
| 19 | Disk full causing PostgreSQL WAL corruption | Infrastructure | **Medium** | Medium | Phase 4 — Reliability |
| 21 | MCP server memory leak | Infrastructure | **Medium** | Medium | Phase 4 — Reliability |
| 23 | Semantic search missing Sri Lankan terms | RAG | **Medium** | Hard | Phase 3 — RAG tools |
| 24 | Context window stuffing degrading synthesis | RAG | **Medium** | Medium | Phase 3 — RAG tools |
| 26 | No audit trail for compliance answers | Legal | **Medium** | N/A | Phase 4 — Reliability |
| 6 | Sinhala/Tamil script corruption | Quality | N/A | N/A | **Eliminated — §5.2** |
| 2 | Stale download URLs in responses | Ingestion | **Low** | Easy | Phase 4 — Reliability |
| 9 | Document deduplication failure | Quality | **Low** | Medium | Phase 1 — Foundation |
| 11 | Qdrant cold start timeout | Runtime | **Low** | Easy | Phase 4 — Reliability |
| 12 | Tool description routing failures | Runtime | **Low** | Hard | Ongoing — maintenance |
| 14 | stdio hanging on large responses | Runtime | **Low** | Medium | Phase 3 — RAG tools |
| 17 | Rollback window data integrity risk | Consistency | **Low** | Medium | Phase 4 — Reliability |
| 18 | Docker volume data loss | Infrastructure | **Low** | Easy | Phase 4 — Reliability |
| 20 | Gemini API key exposure | Security | **Low** | N/A | Phase 1 — Day 1 |

**Three issues to address before any other development work begins:**

1. **#1** (CAPTCHA corruption) — silent data quality failure with no error signal
2. **#20** (API key exposure) — set up `.gitignore` and `.env.example` on day one
3. **#4** (embedding checkpoint) — enables safe pipeline reruns from the start

---

*Document version 2.0. All issues identified through architectural review during system design phase. Mitigations are design-level specifications; implementation details determined during development.*
