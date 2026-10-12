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
docker compose --profile discovery up -d searxng   # optional: web discovery (see below)
uv sync
uv run pytest
uv run uvicorn water_source_research.api.main:app --reload
```

Run a sample city workflow:

```bash
uv run python scripts/run_city.py Toronto
```

## Discovery providers

The `discover` step finds candidate sources with four providers, run concurrently:

| Provider | Used for | Credential (`.env`) | Required? |
|---|---|---|---|
| SearXNG (self-hosted) | Web: utility sites, government documents, technical reports | `SEARXNG_URL` | Needs the Docker container below |
| OpenAlex | Academic papers | `OPENALEX_API_KEY` | Recommended (works keyless with lower limits) |
| Semantic Scholar | Academic papers, related papers | `SEMANTIC_SCHOLAR_API_KEY` | Optional; keyless requests share a public pool and are often rate-limited |
| Crossref | Verifying DOIs and citation metadata of the top candidates | `CROSSREF_MAILTO` | Optional contact email (not a key) |

Candidates are deduplicated (DOI, canonical URL, normalized title), ranked with official
utility/government sources ahead of academic and secondary sources, and stored in the run
result (`discovery_candidates`, `candidate_urls`) and in the dashboard's *discover* step
(`top_candidates`, `provider_status`). A provider that times out, is rate-limited (429) or
returns a bad payload is retried with exponential backoff and then skipped for that round;
the run continues with the others. Every call is recorded as a trusted `TOOL_CALL` event.

### 1. Start SearXNG (Docker)

```bash
# in .env: SEARXNG_SECRET=<any long random string>, SEARXNG_URL=http://localhost:8080
docker compose --profile discovery up -d searxng
curl "http://localhost:8080/search?q=toronto+water&format=json"   # should return JSON
```

`config/searxng/settings.yml` (mounted into the container) enables the JSON API, which
SearXNG disables by default (the adapter reports a 403 with a hint if it's off), and turns off
the bot limiter, which would block API clients. Keep this instance local: don't expose port
8080 publicly with the limiter off. To use an existing SearXNG instead, point `SEARXNG_URL`
at it and make sure `json` is in its `search.formats`.

### 2. Get credentials

- **OpenAlex**: create a free account at <https://openalex.org>, then copy the key from
  <https://openalex.org/settings/api> into `OPENALEX_API_KEY`.
- **Semantic Scholar**: request a free key with the form at
  <https://www.semanticscholar.org/product/api#api-key-form> and set
  `SEMANTIC_SCHOLAR_API_KEY`. A keyed account allows about 1 request/second, which the
  default throttle respects.
- **Crossref**: no key. Set `CROSSREF_MAILTO` to a contact email: it's sent with each request
  so Crossref routes you to its faster "polite" pool and can contact you if something
  misbehaves. Crossref doesn't email you anything. `doi_verified=false` means "not registered
  with Crossref" (e.g. DataCite DOIs for theses or datasets), not "invalid".

Keys live only in `.env` (git-ignored). They're never logged, cached or written to events.

### 3. Check it works

```bash
uv run python scripts/discovery_smoke.py Toronto --country Canada
uv run python scripts/discovery_smoke.py Toronto --providers openalex,crossref --json
```

The smoke script calls the real APIs and prints per-provider status, latency and the ranked
candidates. It exits non-zero only if every provider failed.

### Settings

All `DiscoverySettings` fields can be set as `DISCOVERY__<FIELD>` (see `.env.example`):

| Variable | Default | Meaning |
|---|---|---|
| `DISCOVERY__ENABLED` | `true` | `false` turns discovery off (discover finds nothing) |
| `DISCOVERY__PROVIDERS` | all four | JSON list, e.g. `["searxng","openalex"]` |
| `DISCOVERY__TIMEOUT_SECONDS` | `20` | per request |
| `DISCOVERY__MAX_RETRIES` | `3` | retries on timeout / network error / 429 / 5xx |
| `DISCOVERY__BACKOFF_BASE_SECONDS` / `_MAX_SECONDS` | `1` / `30` | exponential backoff with jitter; `Retry-After` honoured up to the max |
| `DISCOVERY__CACHE_TTL_SECONDS` | `86400` | in-process response cache (`0` disables) |
| `DISCOVERY__RESULTS_PER_QUERY` | `10` | results requested per provider per query |
| `DISCOVERY__MAX_QUERIES_PER_ROUND` | `6` | per kind (web / academic) |
| `DISCOVERY__MAX_CANDIDATES_PER_ROUND` / `_TOTAL` | `40` / `150` | caps after ranking |
| `DISCOVERY__CROSSREF_VERIFY_TOP_N` | `15` | top DOI candidates checked with Crossref per round |
| `DISCOVERY__SEMANTIC_SCHOLAR_RELATED_PER_ROUND` | `0` | `>0` adds related-paper expansion |
| `DISCOVERY__<PROVIDER>_LIMITS__MAX_CONCURRENCY` / `__MIN_INTERVAL_SECONDS` | per provider | client-side rate limits |

## Architecture principle

The AI is not the source. Every accepted value should point back to supporting evidence from a utility, government, technical, regulatory, academic, or other approved source.

The scaffold is intentionally incomplete at the provider-integration level so you can wire in the exact VT LLM endpoint, Firecrawl plan, Apify actors, and Computer Use workflow after access is approved.
