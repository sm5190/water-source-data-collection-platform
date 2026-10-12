# Water Source Research Platform

Initial scaffold for an evidence-grounded, human-reviewed research workflow for the global water-source project.

## Included

- LangGraph plan/execute workflow
- LLM router with Virginia Tech's ARC LLM API as the default provider and OpenAI selectable by
  configuration (see [LLM provider](#llm-provider))
- Evidence-grounded structured extraction gateway (every claim cites a verified quote)
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

## LLM provider

Every LLM call goes through `llm/router.py`, which uses exactly one provider at a time. There
is no automatic fallback: if VT fails, the call fails (and the research queue retries the run);
it never silently switches to paid OpenAI.

| Operation | Where | Uses the router |
|---|---|---|
| Claim extraction (structured, cited) | `extract` node → `llm/extraction.py` | yes |
| Procedure distillation | `procedures/llm_distiller.py` | yes |
| Procedure embeddings | `procedures/` retrieval + merge | yes |
| Query generation, ranking, source classification, validation | `discovery/`, `agents/` | no LLM (deterministic) |

### VT ARC LLM API (default)

1. Get a personal key at <https://llm.arc.vt.edu> → Settings → Account → API keys.
2. In `.env`:
   ```env
   LLM_PROVIDER=vt
   VT_LLM_BASE_URL=https://llm-api.arc.vt.edu/api/v1
   VT_LLM_API_KEY=sk-...
   VT_LLM_MODEL=gpt-oss-120b
   ```
3. **Connect to the VT Campus VPN** — the API answers `403 API access is restricted to the VT
   Campus VPN` from anywhere else.
4. Check it (free; about six small requests):
   ```bash
   uv run python scripts/llm_smoke.py
   ```
   It lists models, reports which structured-output mode VT accepts (set
   `LLM__VT_STRUCTURED_MODE` accordingly), runs one grounded extraction and one embedding.

### Switching to OpenAI later

Set `LLM_PROVIDER=openai` and `OPENAI_API_KEY` (and optionally `OPENAI_MODEL`). No code
changes. `scripts/llm_smoke.py` refuses to make paid calls unless run with `--allow-openai`.
Procedure embeddings change model on the switch; stored vectors are re-embedded by
`scripts/run_procedure_worker.py` and never compared across models meanwhile.

### Rate limits, retries and caching

| Setting | Default | Meaning |
|---|---|---|
| `LLM__REQUESTS_PER_MINUTE` | `27` | sliding-window budget shared by all LLM calls in a process |
| `LLM__MAX_CONCURRENCY` | `8` | requests in flight per process (VT allows 20 per user) |
| `LLM__MAX_RETRIES` | `3` | retries for 429 / timeout / network / 5xx, with jittered exponential backoff honouring the server's wait hint; 401/403/400/404/413 fail at once |
| `LLM__STRUCTURED_MAX_ATTEMPTS` | `2` | repair turns when output fails the response schema; then the call fails (nothing unvalidated is accepted) |
| `LLM__CACHE_TTL_SECONDS` | `86400` | in-process response cache; key = provider, endpoint, model, task, prompt version, schema hash and prompts |
| `LLM__MAX_OUTPUT_TOKENS` | `4000` | must stay ≤ 8000 on VT |

Limits are per worker **process**: with several worker processes on one VT key, divide
`REQUESTS_PER_MINUTE` and `MAX_CONCURRENCY` by the number of processes.

`uv run pytest` never touches a real API (LLM and discovery tests use mocked HTTP).

### Live tests (real APIs)

`tests/live/` runs source discovery against the real OpenAlex, Crossref, Semantic Scholar and
SearXNG APIs, and the router, extraction gateway and embeddings against the real VT LLM API
(including discovered sources fed into VT extraction). Opt in explicitly:

```bash
RUN_LIVE_TESTS=1 uv run pytest tests/live -v -rs          # bash
$env:RUN_LIVE_TESTS=1; uv run pytest tests/live -v -rs    # PowerShell
```

They always use VT for LLM calls (never paid OpenAI, whatever `LLM_PROVIDER` says) and skip,
with the reason, any provider that is unreachable: VT off the VT Campus VPN, SearXNG when its
container isn't running, Semantic Scholar when it rate-limits keyless clients. A full run
takes about 2–3 minutes and a dozen VT requests.

## Architecture principle

The AI is not the source. Every accepted value should point back to supporting evidence from a utility, government, technical, regulatory, academic, or other approved source.

The scaffold is intentionally incomplete at the provider-integration level so you can wire in the exact Firecrawl plan, Apify actors, and Computer Use workflow after access is approved.
