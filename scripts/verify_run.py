"""Audit one city run straight from PostgreSQL, MinIO and (optionally) the API.

Usage: uv run python scripts/verify_run.py <run_id> [--api http://localhost:8000]

Read-only. Prints the job, steps per attempt, LangGraph checkpoint rows for the run's thread,
trusted events (provider calls, real VT requests/tokens), acquired documents, evidence
chunks, claims with their quotes (each re-checked against the stored chunk text), field
states, context bundles, raw objects in MinIO, and cross-checks the API. Ends with PASS/FAIL
per success criterion. Exit 1 if any criterion fails.
"""

import argparse
import sys
from collections import Counter
from typing import Any
import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session
from water_source_research.acquisition.artifacts import MinioArtifactStore
from water_source_research.core.config import get_settings
from water_source_research.db.models import ContextBundleRecord
from water_source_research.db.research_models import ResearchJobRecord, ResearchRunRecord
from water_source_research.db.session import get_engine
from water_source_research.llm.extraction import normalize_quote
from water_source_research.research_queue.queue import ResearchQueue
from water_source_research.services.evidence_store import EvidenceRepository

def heading(title: str) -> None:
    print(f"\n== {title} " + "=" * max(0, 70 - len(title)))

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id")
    parser.add_argument("--api", default=None, help="API base URL to cross-check")
    args = parser.parse_args()
    run_id = args.run_id
    settings = get_settings()
    engine = get_engine()
    queue = ResearchQueue(engine, settings.research_queue)
    repo = EvidenceRepository(engine)
    criteria: dict[str, tuple[bool, str]] = {}

    heading("Research job")
    with Session(engine) as s:
        job = s.get(ResearchJobRecord, run_id)
        if job is None:
            print(f"no research_jobs row for {run_id}")
            return 1
        print(f"run_id={job.run_id} city={job.city_name!r} country={job.country!r} "
              f"field_keys={job.field_keys}")
        print(f"status={job.status} attempts={job.attempts}/{job.max_attempts} "
              f"resumed={job.resumed} last_error={job.last_error!r}")
        print(f"created={job.created_at} started={job.started_at} finished={job.finished_at}")
        result: dict[str, Any] = job.result or {}
        run_row = s.get(ResearchRunRecord, run_id)
        bundle_ids = list((result.get("context_bundle_ids") or {}).values())
        bundles = {b.id: b for b in s.query(ContextBundleRecord)
                   .filter(ContextBundleRecord.id.in_(bundle_ids))} if bundle_ids else {}

    heading("Execution steps (research_run_steps)")
    steps = queue.steps(run_id)
    by_attempt: dict[int, list[str]] = {}
    for st in steps:
        by_attempt.setdefault(st["attempt"], []).append(st["node"])
    for attempt, nodes in by_attempt.items():
        print(f"attempt {attempt}: {' -> '.join(nodes)}")

    heading("LangGraph checkpoints (thread_id = run_id)")
    with engine.connect() as conn:
        cp = {t: conn.execute(text(f"SELECT count(*) FROM {t} WHERE thread_id = :r"),
                              {"r": run_id}).scalar()
              for t in ("checkpoints", "checkpoint_writes", "checkpoint_blobs")}
        latest = conn.execute(text(
            "SELECT checkpoint_id, metadata->>'step' AS step, metadata->>'source' AS source "
            "FROM checkpoints WHERE thread_id = :r ORDER BY checkpoint_id DESC LIMIT 1"),
            {"r": run_id}).first()
    print(cp)
    if latest:
        print(f"latest checkpoint: id={latest.checkpoint_id} step={latest.step} "
              f"source={latest.source}")
    elif job.status == "SUCCEEDED" and not settings.research_queue.keep_checkpoints:
        print("(none: checkpoints are deleted after success unless "
              "RESEARCH_QUEUE__KEEP_CHECKPOINTS=true)")

    heading("Trusted events (run_events) and provider calls")
    with engine.connect() as conn:
        events = conn.execute(text(
            "SELECT kind, node, field_key, provider, result, attrs FROM run_events "
            "WHERE run_id = :r ORDER BY seq"), {"r": run_id}).all()
    print("by kind:", dict(Counter(e.kind for e in events)))
    calls = Counter((e.node, e.provider, e.result) for e in events if e.kind == "TOOL_CALL")
    for (node, provider, res), n in sorted(calls.items(), key=str):
        print(f"  TOOL_CALL {node:<9} {provider!s:<17} {res!s:<10} x{n}")
    usage = [e.attrs for e in events if e.kind == "NODE" and e.node == "extract"
             and (e.attrs or {}).get("bundle_status") == "READY"]
    vt_requests = sum(int(u.get("llm_requests") or 0) for u in usage)
    tokens = (sum(int(u.get("prompt_tokens") or 0) for u in usage),
              sum(int(u.get("completion_tokens") or 0) for u in usage))
    models = sorted({u.get("model") for u in usage if u.get("model")})
    print(f"extractions with READY evidence: {len(usage)} | real model requests: {vt_requests}"
          f" | cache hits: {sum(int(u.get('cache_hits') or 0) for u in usage)} | tokens "
          f"in/out: {tokens[0]}/{tokens[1]} | models: {models}")
    hitl = [e for e in events if e.kind == "HITL"]
    print(f"human-review decisions (HITL events): {len(hitl)}")
    print(f"research_runs row: {'yes' if run_row else 'NO'}"
          + (f" outcome={run_row.outcome} review_required={run_row.human_review_required}"
             if run_row else ""))

    heading("Discovery (result JSON; candidates are not a normalized table)")
    candidates = result.get("discovery_candidates") or []
    print(f"rounds={result.get('discovery_rounds')} candidates={len(candidates)} "
          f"provider_status={result.get('discovery_provider_status')}")
    for c in candidates[:8]:
        print(f"  {c.get('source_type')!s:<10} {c.get('providers')} {c.get('url')}")

    heading("Acquisition (source_documents) and evidence (evidence_chunks)")
    docs = repo.documents(run_id)
    counts = repo.counts(run_id)
    print(f"documents by status: {dict(Counter(d['status'] for d in docs))} | {counts}")
    store = MinioArtifactStore(settings.artifacts) if settings.artifacts.enabled else None
    raw_ok = raw_total = 0
    for d in docs:
        mark = ""
        if d["raw_object_key"] and store:
            raw_total += 1
            bucket, _, key = d["raw_object_key"].partition("/")
            try:
                size = store.client.stat_object(bucket, key).size
                raw_ok += 1
                mark = f"raw={size}B"
            except Exception as exc:  # noqa: BLE001
                mark = f"raw MISSING ({type(exc).__name__})"
        print(f"  r{d['round']} {d['status']:<17} {d['source_type']!s:<10} "
              f"{d['acquisition_method']!s:<15} chunks={d['chunks']:<4} {mark:<14} "
              f"{d['error_kind'] or ''} {d['requested_url']}")
    print(f"raw objects present in MinIO: {raw_ok}/{raw_total}")
    canon = Counter(d["canonical_url"] for d in docs)
    dup_docs = [u for u, n in canon.items() if n > 1]

    heading("Claims, citations and field states")
    claims = repo.claims(run_id)
    chunk_text = {c["chunk_id"]: normalize_quote(c["text"])
                  for c in repo.chunks(run_id, limit=100_000)}
    traceable = 0
    for c in claims:
        print(f"  {c['field_key']}: {c['value']!r} ({c['value_status']}, {c['value_origin']}, "
              f"confidence {c['confidence']}, selected={c['is_selected']})")
        ok_claim = bool(c["citations"])
        for cite in c["citations"]:
            in_chunk = bool(cite["chunk_id"]) and normalize_quote(cite["quote"]) in \
                chunk_text.get(cite["chunk_id"], "")
            ok_claim &= bool(cite["url"]) and in_chunk
            print(f"      <{cite['url']}> page={cite['page_number']} type={cite['source_type']}"
                  f" method={cite['acquisition_method']} date={cite['source_date']}")
            print(f"      quote ({'found in stored chunk ' + cite['chunk_id'] if in_chunk else 'NOT FOUND in stored chunks'}):"
                  f" {cite['quote'][:200]!a}")
        traceable += ok_claim
    states = repo.field_states(run_id)
    for s_ in states:
        print(f"  state {s_['field_key']}: {s_['status']} review_required="
              f"{s_['review_required']} needs_evidence={s_['needs_evidence']}")
    state_claims = sum(len(f.get("candidate_claims") or []) for f in
                       (result.get("research_state") or {}).get("fields", {}).values())
    claim_keys = Counter((c["field_key"], repr(c["value"])) for c in claims)
    dup_claims = [k for k, n in claim_keys.items() if n > 1]
    print(f"claims in DB={len(claims)} vs candidate claims in final state={state_claims}; "
          f"duplicate (field, value) claims: {dup_claims or 'none'}; "
          f"duplicate documents: {dup_docs or 'none'}")
    print(f"context bundles referenced: {len(bundle_ids)}, stored: {len(bundles)}")

    api_ok, api_detail = None, "not checked (pass --api)"
    if args.api:
        heading(f"API cross-check ({args.api})")
        try:
            base = f"{args.api.rstrip('/')}/research/runs/{run_id}"
            got = {name: httpx.get(base + suffix, timeout=30)
                   for name, suffix in (("run", ""), ("result", "/result"), ("fields", "/fields"),
                                        ("claims", "/claims"), ("evidence", "/evidence"),
                                        ("discovery", "/discovery"), ("steps", "/steps"))}
            for name, r in got.items():
                print(f"  GET {name:<10} -> {r.status_code}")
            j_claims = got["claims"].json()["claims"]
            j_ev = got["evidence"].json()["counts"]
            api_ok = (all(r.status_code == 200 for r in got.values())
                      and got["run"].json()["status"] == job.status
                      and len(j_claims) == len(claims) and j_ev == counts
                      and len(got["discovery"].json()["candidates"]) == len(candidates))
            api_detail = (f"status={got['run'].json()['status']} claims={len(j_claims)} "
                          f"evidence={j_ev} discovery={len(got['discovery'].json()['candidates'])}")
            print(f"  consistent with database: {api_ok} ({api_detail})")
        except Exception as exc:  # noqa: BLE001
            api_ok, api_detail = False, f"{type(exc).__name__}: {exc}"
            print(f"  FAILED: {api_detail}")

    heading("Success criteria")
    from alembic.config import Config
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory
    from water_source_research.core.paths import REPO_ROOT
    with engine.connect() as conn:
        current = MigrationContext.configure(conn).get_current_revision()
    head = ScriptDirectory.from_config(Config(str(REPO_ROOT / "alembic.ini"))).get_current_head()
    ok_docs = [d for d in docs if d["status"] == "OK" and d["chunks"]]
    criteria["1 schema at head"] = (current == head, f"{current}")
    criteria["2 real discovery candidates"] = (len(candidates) > 0,
                                               f"{len(candidates)} candidates")
    criteria["3 document acquired+parsed"] = (bool(ok_docs), (f"{len(ok_docs)} documents, "
                                                               f"{counts['chunks']} chunks"))
    criteria["4 real VT grounded claim"] = (vt_requests > 0 and traceable > 0,
                                            f"{vt_requests} VT requests, {traceable} claims")
    criteria["5 claim traceable to quote+URL"] = (traceable == len(claims) and traceable > 0,
                                                  f"{traceable}/{len(claims)} claims")
    criteria["6 checkpoints + execution records"] = (
        cp["checkpoints"] > 0 and bool(steps) and run_row is not None and bool(events),
        f"checkpoints={cp['checkpoints']} steps={len(steps)} events={len(events)}")
    criteria["7 retrievable via API"] = (bool(api_ok), api_detail)
    criteria["8 recovered from interruption"] = (
        job.resumed and job.attempts >= 2 and job.status == "SUCCEEDED",
        f"attempts={job.attempts} resumed={job.resumed} (only expected for a drill run)")
    criteria["- job succeeded, no duplicates"] = (
        job.status == "SUCCEEDED" and not dup_claims and not dup_docs
        and len(claims) == state_claims, f"status={job.status}")
    for name, (ok, detail) in criteria.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {name:<36} {detail}")
    required = [k for k in criteria if not k.startswith("8")]
    return 0 if all(criteria[k][0] for k in required) else 1

if __name__ == "__main__":
    sys.exit(main())
