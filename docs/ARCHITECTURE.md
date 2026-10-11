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
OpenAI:
- planning
- structured extraction
- conflict resolution
- validation

VT LLM:
- relevance filtering
- classification
- query generation
- metadata cleanup

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
