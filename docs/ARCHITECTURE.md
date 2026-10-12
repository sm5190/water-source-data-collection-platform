# Architecture

## Control plane
- LangGraph orchestrator
- Context manager
- Field-aware state
- Procedure memory
- Human review

## Discovery
Default:
- SearXNG
- OpenAlex
- Crossref
- Semantic Scholar

Optional:
- Firecrawl
- Apify

## Acquisition
1. Direct HTTP / HTML parsing
2. Docling for PDFs/documents
3. Firecrawl fallback
4. Apify specialized actor
5. Computer Use for interactive portals

## LLM routing
All model calls go through one router (`llm/router.py`); `LLM_PROVIDER` selects exactly one
provider, with no automatic fallback between them:

- `vt` (default): Virginia Tech ARC LLM API — free, OpenAI-compatible Chat Completions,
  `gpt-oss-120b`, embeddings `Qwen3-Embedding-4B`; reachable on the VT Campus VPN only.
- `openai`: paid OpenAI API (Responses API with native schema enforcement); activated by
  configuration only (`LLM_PROVIDER=openai` + `OPENAI_API_KEY`).

Router-backed operations:
- structured claim extraction (extraction gateway, LangGraph `extract` node)
- procedure distillation (LLM distiller)
- procedure embeddings (semantic retrieval / merge guard)

Query generation, source ranking/classification, validation and human review are
deterministic and call no model. The router owns rate limiting (27 requests/min per worker
process, in-flight cap), response caching, retries for transient errors, and strict
client-side schema validation with bounded repair; every accepted claim must cite a grounded
quote from its evidence.

## Evidence model
Source document -> artifact -> evidence span -> field claim -> resolution -> accepted data

## Observability
OpenTelemetry is the canonical audit layer for:
- run/city/field identifiers
- provider/model
- token use
- latency
- tool usage
- estimated cost
- confidence
- HITL events

## Procedural memory
Reusable research strategies learned from validated runs. Advisory planning guidance only:
never evidence, never field state, never an LLM cache.

Read: graph.plan → metadata filter → similarity over filtered candidates → reliability ranking
→ top 1–3 compact recipes → planner adapts to current capability availability (or uses the
default strategy). Failures degrade to the default strategy.

Write (async, after the city result is complete): trusted workflow events → trajectory →
eligibility → deterministic distillation → schema / grounding / safety checks → dedupe/merge
→ CANDIDATE → promotion policy (+ optional human approval) → ACTIVE → DEPRECATED → RETIRED.

Trust boundary: raw web/PDF content never becomes procedure text. Only events describing what
the system did (capabilities, outcomes, validation/HITL decisions, accepted evidence) are
distilled, and recipes may not contain URLs, domains or secrets.
