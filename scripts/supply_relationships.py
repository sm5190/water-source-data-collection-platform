"""Build water-supply relationship graphs offline from stored runs (read only by default).

    uv run python scripts/supply_relationships.py <run_id|prefix> [...] [--out graphs.json]
        [--persist]

Per run: the stored chunks, documents and claims (as extracted: reviewer corrections left
out) -> services/supply_relationships.py with the current code (the utility registry rebuilt
from the same evidence). Prints counts by kind and status, the build time and the size of the
stored rows. Geography uses only APPROVED identities unless CITY_IDENTITY_ALLOW_DRAFT=1
(evaluation only; the graph records its geo_basis). --persist stores the graph under the run
(run_supply_relationships; replaces that policy version's rows); nothing else is written.
"""

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.orm import Session
from token_audit import resolve_ids
from water_source_research.db.evidence_models import EvidenceChunkRecord, SourceDocumentRecord
from water_source_research.db.research_models import ResearchJobRecord
from water_source_research.db.session import get_engine
from water_source_research.schemas.research import EvidenceChunk
from water_source_research.services.evidence_store import EvidenceRepository
from water_source_research.services.revalidate import claims_as_extracted
from water_source_research.services.supply_relationships import build_relationships

def stored_inputs(engine, run_id: str):
    """(research, chunks, documents by url, document ids by url) of a stored run."""
    evidence = EvidenceRepository(engine)
    with Session(engine) as s:
        job = s.get(ResearchJobRecord, run_id)
        docs = s.scalars(select(SourceDocumentRecord).where(
            SourceDocumentRecord.run_id == run_id)).all()
        by_id = {d.id: d for d in docs}
        rows = s.scalars(select(EvidenceChunkRecord).where(
            EvidenceChunkRecord.run_id == run_id).order_by(
            EvidenceChunkRecord.document_id, EvidenceChunkRecord.ordinal)).all()
        chunks = [EvidenceChunk(chunk_id=r.chunk_id, url=r.url, text=r.text,
                                page_number=r.page_number,
                                title=(by_id[r.document_id].title or "") if r.document_id
                                in by_id else "",
                                source_type=(by_id[r.document_id].source_type or "OTHER")
                                if r.document_id in by_id else "OTHER") for r in rows]
        documents, ids = {}, {}
        for d in docs:
            for u in (d.final_url, d.requested_url, d.canonical_url):
                if u:
                    documents.setdefault(u, {"title": d.title, "source_type": d.source_type})
                    ids.setdefault(u, d.id)
        city, country = job.city_name, job.country
    research, _ = claims_as_extracted(evidence.claims(run_id), city, country)
    return research, chunks, documents, ids

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_ids", nargs="+")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--persist", action="store_true")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    engine = get_engine()
    out = {}
    for run_id in resolve_ids(engine, args.run_ids):
        research, chunks, documents, ids = stored_inputs(engine, run_id)
        started = time.perf_counter()
        graph = build_relationships(research, chunks, documents, None, ids)
        seconds = round(time.perf_counter() - started, 2)
        data = graph.model_dump(mode="json")
        size = len(json.dumps(data["relationships"] + data["rejected"]).encode())
        counts = Counter(f"{r['kind']}:{r['status']}" for r in data["relationships"])
        print(f"{run_id[:8]} {research.city_name}: {len(data['relationships'])} relationships, "
              f"{len(data['rejected'])} rejected, {seconds}s, {size} bytes, "
              f"{len(chunks)} chunks, geo {graph.geo_basis}")
        for k, n in sorted(counts.items()):
            print(f"    {k}: {n}")
        if args.persist:
            EvidenceRepository(engine).save_relationships(run_id, data)
        out[run_id] = {"city": research.city_name, "seconds": seconds, "bytes": size,
                       "chunks": len(chunks), "graph": data}
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    return 0

if __name__ == "__main__":
    sys.exit(main())
