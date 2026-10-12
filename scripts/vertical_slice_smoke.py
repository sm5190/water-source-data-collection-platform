"""Live vertical-slice smoke test: one REAL, persisted city run (not run by pytest).

Usage:
  uv run python scripts/vertical_slice_smoke.py Toronto --country Canada
  uv run python scripts/vertical_slice_smoke.py Toronto --country Canada --fields utility_name

Needs: VT VPN + VT_LLM_* in .env (LLM_PROVIDER=vt; OpenAI is refused), migrated Postgres
(`uv run alembic upgrade head`), MinIO and SearXNG (`docker compose up -d postgres minio`,
`docker compose --profile discovery up -d searxng`).

The city is submitted to the real research queue and executed by the real ResearchWorker
(Postgres checkpointer, discovery, safe HTTP/PDF acquisition, raw files in MinIO, context
bundles, VT extraction, evidence/claims tables, run events). Results are read back from the
database. Secrets are never printed.

Exit codes: 0 grounded claim(s) produced | 1 NO_GROUNDED_EVIDENCE (pipeline worked, evidence
does not support a value: not a technical failure) | 2 configuration/service preflight |
3 discovery failed | 4 no official document acquired | 5 live LLM failure | 6 other run failure.
"""

import argparse
import sys
import time
from urllib.parse import urlsplit
import httpx
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from water_source_research.acquisition.artifacts import build_artifact_store
from water_source_research.agents.runtime import build_production_graph
from water_source_research.core.config import Settings, get_settings
from water_source_research.core.paths import REPO_ROOT
from water_source_research.db.session import get_engine
from water_source_research.llm.base import LLMError
from water_source_research.llm.router import build_structured_llm
from water_source_research.procedures.services import build_procedure_services
from water_source_research.research_queue.checkpointing import open_checkpointer, run_async
from water_source_research.research_queue.queue import JobStatus, ResearchQueue
from water_source_research.research_queue.worker import ResearchWorker
from water_source_research.services.evidence_store import EvidenceRepository
from water_source_research.services.field_state import select_fields

DEFAULT_FIELDS = ["utility_name", "water_source", "total_city_water_withdrawals_m3_day"]
OFFICIAL = {"UTILITY", "GOVERNMENT", "REGULATORY"}
OK, NO_EVIDENCE, PREFLIGHT, DISCOVERY, ACQUISITION, LLM, RUN = 0, 1, 2, 3, 4, 5, 6

class _Probe(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: str

class Stages:
    def __init__(self) -> None:
        self.started = time.perf_counter()

    def report(self, stage: str, ok: bool | None, detail: str, seconds: float | None = None):
        mark = {True: "OK  ", False: "FAIL", None: "WARN"}[ok]
        took = f"{seconds:7.2f}s" if seconds is not None else "        "
        print(f"[{mark}] {stage:<14} {took}  {detail}")

def host_of(url: str) -> str:
    return urlsplit(url).netloc or "(not set)"

# --- preflight ---------------------------------------------------------------------------

def preflight(settings: Settings, st: Stages) -> int | None:
    t = time.perf_counter()
    if settings.llm_provider != "vt":
        st.report("config", False, f"LLM_PROVIDER={settings.llm_provider}: this smoke test "
                  "only runs on VT (paid OpenAI is never used)")
        return PREFLIGHT
    missing = [n for n, v in (("VT_LLM_BASE_URL", settings.vt_llm_base_url),
                              ("VT_LLM_API_KEY", settings.vt_llm_api_key),
                              ("VT_LLM_MODEL", settings.vt_llm_model)) if not v]
    if missing:
        st.report("config", False, "missing: " + ", ".join(missing))
        return PREFLIGHT
    st.report("config", True, f"provider=vt host={host_of(settings.vt_llm_base_url)} "
              f"model={settings.vt_llm_model} key=set "
              f"structured_mode={settings.llm.vt_structured_mode}", time.perf_counter() - t)

    t = time.perf_counter()
    try:
        from alembic.config import Config
        from alembic.runtime.migration import MigrationContext
        from alembic.script import ScriptDirectory
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            current = MigrationContext.configure(conn).get_current_revision()
        head = ScriptDirectory.from_config(Config(str(REPO_ROOT / "alembic.ini"))).get_current_head()
    except Exception as exc:  # noqa: BLE001 - report any connection problem
        st.report("postgres", False, f"{type(exc).__name__}: {str(exc).splitlines()[0][:200]}")
        return PREFLIGHT
    if current != head:
        st.report("postgres", False, f"schema at {current}, need {head}: run "
                  "`uv run alembic upgrade head`")
        return PREFLIGHT
    st.report("postgres", True, f"{engine.url.render_as_string(hide_password=True)} @ {head}",
              time.perf_counter() - t)

    t = time.perf_counter()
    store = build_artifact_store(settings)
    if store is None:
        st.report("minio", False, "ARTIFACTS__ENABLED=false: raw documents would not be stored")
        return PREFLIGHT
    try:
        store.check()
    except Exception as exc:  # noqa: BLE001
        st.report("minio", False, f"{settings.artifacts.endpoint}: {type(exc).__name__}: "
                  f"{str(exc)[:200]} (docker compose up -d minio)")
        return PREFLIGHT
    st.report("minio", True, f"{settings.artifacts.endpoint} bucket={settings.artifacts.bucket}",
              time.perf_counter() - t)

    if settings.discovery.enabled and "searxng" in settings.discovery.providers:
        t = time.perf_counter()
        try:
            r = httpx.get(settings.searxng_url.rstrip("/") + "/search",
                          params={"q": "Toronto water", "format": "json"}, timeout=15)
            r.raise_for_status()
            n = len(r.json().get("results", []))
        except Exception as exc:  # noqa: BLE001
            st.report("searxng", False, f"{host_of(settings.searxng_url)}: "
                      f"{type(exc).__name__} (docker compose --profile discovery up -d searxng)")
            return PREFLIGHT
        st.report("searxng", True, f"{host_of(settings.searxng_url)} ({n} results for probe)",
                  time.perf_counter() - t)

    t = time.perf_counter()
    probe_settings = settings.model_copy(update={"llm": settings.llm.model_copy(
        update={"cache_ttl_seconds": 0, "structured_max_attempts": 1})})
    try:
        llm = build_structured_llm(probe_settings, task="smoke-preflight",
                                   prompt_version="slice-1")
        answer = llm.structured("Reply with JSON only.", 'Return {"answer": "ok"}.', _Probe)
    except LLMError as exc:
        st.report("vt-llm", False, f"{exc.kind.value}: {str(exc)[:300]} | check the VT VPN; "
                  "`uv run python scripts/llm_smoke.py --skip-embeddings` finds a working "
                  "LLM__VT_STRUCTURED_MODE")
        return LLM
    st.report("vt-llm", True, f"structured output ({settings.llm.vt_structured_mode}) works: "
              f"{answer.answer!r}", time.perf_counter() - t)
    return None

# --- run -----------------------------------------------------------------------------------

async def execute(settings: Settings, queue: ResearchQueue, engine) -> int:
    procedures = (build_procedure_services(settings, engine)
                  if settings.procedure_memory.enabled else None)
    async with open_checkpointer(settings) as checkpointer:
        graph = build_production_graph(settings, engine, checkpointer, procedures)
        worker = ResearchWorker(queue, graph, queue.s)
        return await worker.drain()

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("city", nargs="+")
    parser.add_argument("--country", default=None)
    parser.add_argument("--fields", default=",".join(DEFAULT_FIELDS))
    args = parser.parse_args()
    city = " ".join(args.city)
    fields = select_fields([f.strip() for f in args.fields.split(",") if f.strip()])
    settings = get_settings()
    st = Stages()
    print(f"Vertical slice smoke: {city}" + (f", {args.country}" if args.country else "")
          + f" | fields: {', '.join(fields or [])}")

    code = preflight(settings, st)
    if code is not None:
        return code

    engine = get_engine()
    # One attempt: a failure here is reported, not silently retried later.
    queue = ResearchQueue(engine, settings.research_queue.model_copy(update={"max_attempts": 1}))
    others = queue.stats()["by_status"].get(JobStatus.QUEUED.value, 0)
    submitted = queue.submit(city, args.country, field_keys=fields)
    run_id = submitted.job.run_id
    if not submitted.created:
        st.report("submit", None, f"an active run for {city} already exists ({run_id}); "
                  "processing it instead (its field subset may differ)")
    else:
        st.report("submit", True, f"run_id={run_id}")
    if others:
        st.report("queue", None, f"{others} other queued run(s) will also be processed")

    t = time.perf_counter()
    try:
        processed = run_async(execute(settings, queue, engine))
    except Exception as exc:  # noqa: BLE001
        st.report("worker", False, f"{type(exc).__name__}: {str(exc)[:300]}")
        return RUN
    job = queue.get(run_id)
    st.report("worker", job.status is JobStatus.SUCCEEDED,
              f"{processed} run(s) processed; {run_id} is {job.status.value}",
              time.perf_counter() - t)

    print("\nStages (from research_run_steps):")
    steps = queue.steps(run_id)
    discover_ok = acquired_official = acquired_any = False
    discovery_status: dict = {}
    for s in steps:
        summary = s.get("summary") or {}
        node = s["node"]
        detail = ""
        if node == "discover":
            discovery_status.update(summary.get("provider_status") or {})
            detail = (f"round {summary.get('discovery_round')}: "
                      f"{summary.get('candidate_urls', 0)} candidate URLs, providers "
                      f"{summary.get('provider_status')}")
            discover_ok |= (summary.get("candidate_urls") or 0) > 0
        elif node == "acquire":
            docs = summary.get("documents") or []
            ok_docs = [d for d in docs if d.get("status") == "OK"]
            acquired_any |= bool(ok_docs)
            acquired_official |= any((d.get("source_type") or "") in OFFICIAL for d in ok_docs)
            detail = (f"{summary.get('documents_ok', 0)}/{summary.get('documents_attempted', 0)}"
                      f" documents OK, {summary.get('evidence_chunks', 0)} chunks total, "
                      f"failures {summary.get('failure_kinds') or {}}")
            for d in docs:
                print(f"      - {d.get('status'):<17} {d.get('source_type')!s:<10} "
                      f"{d.get('error_kind') or '':<18} {d.get('requested_url')}")
        elif node == "extract":
            detail = (f"{len(summary.get('new_claims') or [])} new claims; needing evidence: "
                      f"{summary.get('fields_needing_evidence')}")
        elif node == "validate":
            detail = f"needs_human_review={summary.get('needs_human_review')}"
        elif node == "finalize":
            detail = f"post-processing errors: {summary.get('post_processing_errors')}"
        st.report(node, True, detail, (s.get("duration_ms") or 0) / 1000)

    if job.status is not JobStatus.SUCCEEDED:
        error = job.last_error or "unknown"
        print(f"\nRun failed: {error[:500]}")
        return LLM if "LLMError" in error or "llm" in error.lower() else RUN

    repo = EvidenceRepository(engine)
    claims = repo.claims(run_id)
    states = {s["field_key"]: s for s in repo.field_states(run_id)}
    counts = repo.counts(run_id)
    print(f"\nStored for {run_id}: {counts}")
    print("\nFields:")
    for key in fields or []:
        state = states.get(key, {})
        print(f"  {key}: status={state.get('status')} review_required="
              f"{state.get('review_required')} needs_evidence={state.get('needs_evidence')}")
        for c in [c for c in claims if c["field_key"] == key]:
            print(f"    value={c['value']!r} origin={c['value_origin']} "
                  f"confidence={c['confidence']}")
            for cite in c["citations"]:
                page = f" p.{cite['page_number']}" if cite["page_number"] else ""
                print(f"      source: {cite['title'] or ''} <{cite['url']}>{page}")
                print(f"        type={cite['source_type']} method={cite['acquisition_method']} "
                      f"source_date={cite['source_date']} retrieved={cite['retrieved_at']} "
                      f"raw={cite['raw_object_key']}")
                print(f"        quote: \"{cite['quote']}\"")

    print(f"\nAPI: /research/runs/{run_id}/result | /fields | /claims | /evidence | /steps")
    if not discover_ok:
        print(f"\nDISCOVERY FAILED: no candidate URLs (provider status {discovery_status})")
        return DISCOVERY
    if not acquired_any:
        print("\nACQUISITION FAILED: no document could be acquired (see failures above)")
        return ACQUISITION
    if not acquired_official:
        print("\nACQUISITION: documents were acquired but none from an official source "
              "(UTILITY/GOVERNMENT/REGULATORY)")
        return ACQUISITION
    if not claims:
        print("\nNO_GROUNDED_EVIDENCE: discovery, acquisition and the VT model all worked, but "
              "no claim could be grounded in the acquired evidence. Not a technical failure.")
        return NO_EVIDENCE
    print(f"\nSUCCESS: {len(claims)} grounded candidate claim(s); all await human review.")
    return OK

if __name__ == "__main__":
    sys.exit(main())
