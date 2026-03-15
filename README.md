# RegMind-LK

A Model Context Protocol (MCP) server that transforms the Sri Lanka Tourism Development Authority's (SLTDA) 50+ static regulatory PDFs into 14 AI-callable tools — covering business registration, financial concessions, tourist statistics, strategic plans, niche tourism toolkits, and investor guidance. Designed for use with Claude Desktop or any MCP-compatible client.

---

## Architecture

```
Claude Desktop / MCP Client
         │  stdio / SSE
         ▼
   ┌─────────────┐       ┌──────────────┐
   │  FastMCP    │──────►│  PostgreSQL  │  (structured data — steps, categories,
   │  (14 tools) │       │  16 (asyncpg)│   concessions, niche toolkits)
   │  port 8001  │       └──────────────┘
   │             │       ┌──────────────┐
   │             │──────►│    Qdrant    │  (768-dim vectors, Gemini embeddings)
   └─────────────┘       │    v1.9.2    │
                         └──────────────┘
                                │
                         ┌──────────────┐
                         │ Gemini Flash │  (synthesis / query expansion)
                         │ embed-004    │  (embeddings)
                         └──────────────┘

   ┌───────────────────────────────────────┐
   │         Ingestion Pipeline            │
   │  Scrape → Download → Extract → Chunk  │
   │  → Embed → PG Sync → Cutover         │
   └───────────────────────────────────────┘
```

### Tool Clusters

| Cluster | Tools |
|---------|-------|
| 1 — Registration | `registration_requirements`, `accommodation_standards`, `registration_checklist` |
| 2 — Financial | `financial_concessions`, `tdl_information`, `tax_rate` |
| 3 — Statistics | `latest_arrivals_report`, `annual_report` |
| 4 — Strategy | `strategic_plan`, `tourism_act_provisions` |
| 5 — Niche Tourism | `niche_categories`, `niche_toolkit` |
| 6 — Investor | `investment_process`, `search_resources` |

---

## Prerequisites

- Docker Desktop 4.x
- A [Google Gemini API key](https://aistudio.google.com/app/apikey)
- Python 3.11+ (for running tests locally)

---

## Quick Start

### 1. Clone and configure

```bash
git clone https://github.com/your-org/RegMind-LK.git
cd RegMind-LK
cp .env.example .env
# Edit .env — set GEMINI_API_KEY, and optionally change POSTGRES_PASSWORD
```

### 2. Start the stack

```bash
docker compose up -d
```

Services started: `postgres:16`, `qdrant:1.9.2`, `sltda-mcp` (port 8001).

### 3. Apply the database schema

```bash
psql $POSTGRES_URL -f migrations/001_initial_schema.sql
# or via Docker:
docker compose exec postgres psql -U sltda -d sltda_mcp -f /migrations/001_initial_schema.sql
```

### 4. Run the ingestion pipeline

```bash
docker compose run ingestion python -m sltda_mcp.ingestion.pipeline
```

Pipeline steps: scrape SLTDA pages → download PDFs → extract → chunk → embed → sync to PG staging → cutover.
Expected runtime: ~30–60 minutes on first run.

### 5. Verify health

```bash
curl http://localhost:8001/health | jq .status
# → "healthy"
```

---

## Claude Desktop Integration

1. Copy `claude_desktop_config.json.example` to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows).
2. Replace `<your-key>` with your Gemini API key.
3. Restart Claude Desktop.
4. The `sltda` server should appear in Claude Desktop's tool list.

---

## Monthly Refresh Procedure

Run once per month to pick up new SLTDA documents:

```bash
# Pre-flight
curl http://localhost:8001/health       # must be healthy
docker compose run ingestion python -m sltda_mcp.ingestion.pipeline

# Post-cutover verification (after pipeline completes)
curl http://localhost:8001/health | jq '{docs: .total_documents, vectors: .total_vectors}'
```

**Rollback** (within 48 hours of cutover):
```bash
docker compose run ingestion python -m sltda_mcp.ingestion.cutover --rollback
```

---

## Running Tests

```bash
# Unit tests (no Docker required)
pytest tests/unit/ -v

# Smoke tests (14 tools, mocked)
pytest tests/smoke/smoke_tests.py -v

# Load tests (20 concurrent, mocked)
pytest tests/smoke/load_test.py -v -s

# Integration tests (requires live Docker stack)
pytest tests/integration/ -v

# RAG evaluation (requires live stack + real documents)
python tests/rag_eval/run_eval.py
```

---

## Known Limitations

- **English only** — Sinhala and Tamil documents are excluded from ingestion scope.
- **Monthly refresh cycle** — Data is updated once per month; real-time SLTDA changes are not reflected immediately.
- **Not legal advice** — All responses include a disclaimer. Verify regulatory requirements directly with SLTDA before making business decisions.
- **Gemini dependency** — RAG tools (Clusters 4–6) require a valid `GEMINI_API_KEY`. Structured tools (Clusters 1–3) work without it.
