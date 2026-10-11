# Water Source Research Platform

Initial scaffold for an evidence-grounded, human-reviewed research workflow for the global water-source project.

## Included

- LangGraph plan/execute workflow
- OpenAI as the main reasoning/extraction model
- VT LLM adapter for low-effort filtering/classification
- SearXNG + OpenAlex + Crossref + Semantic Scholar discovery adapters
- Direct HTTP/HTML parsing first
- Docling adapter for PDFs/documents
- Firecrawl and Apify optional fallback adapters
- Computer Use integration boundary
- PostgreSQL + pgvector-oriented data model
- Evidence, claims, field-resolution, human-review, and procedure-memory tables
- Context manager scaffold
- OpenTelemetry bootstrap
- 32-field standardized export schema
- Basic unit tests

## Quick start

```bash
cp .env.example .env
docker compose up -d postgres minio
uv sync
uv run pytest
uv run uvicorn water_source_research.api.main:app --reload
```

Run a sample city workflow:

```bash
uv run python scripts/run_city.py Toronto
```

## Architecture principle

The AI is not the source. Every accepted value should point back to supporting evidence from a utility, government, technical, regulatory, academic, or other approved source.

The scaffold is intentionally incomplete at the provider-integration level so you can wire in the exact VT LLM endpoint, Firecrawl plan, Apify actors, and Computer Use workflow after access is approved.
