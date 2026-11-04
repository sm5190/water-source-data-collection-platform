"""Read-only report of a Firecrawl-enabled run: eligible attempts, recovered documents, new
evidence passages, claims citing them (validation and automatic decisions), credits
(reserved / estimated / reported / team delta) and water-source candidates supported only by
Firecrawl documents.

    uv run python scripts/firecrawl_pilot_report.py <run_id> [--out report.json]
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.orm import Session
from water_source_research.db.evidence_models import (
    ClaimCitationRecord, EvidenceChunkRecord, FieldClaimRecord, FirecrawlAttemptRecord,
    SourceDocumentRecord,
)
from water_source_research.db.observability_models import ProviderUsageRecord
from water_source_research.db.session import get_engine
from water_source_research.services.evidence_store import EvidenceRepository
from water_source_research.services.revalidate import automatic_decisions

def report(run_id: str) -> dict:
    engine = get_engine()
    with Session(engine) as s:
        docs = s.scalars(select(SourceDocumentRecord).where(
            SourceDocumentRecord.run_id == run_id).order_by(SourceDocumentRecord.id)).all()
        fc_docs = [d for d in docs if d.attempt == "firecrawl"]
        fc_ids = {d.id for d in fc_docs}
        chunks = s.scalars(select(EvidenceChunkRecord).where(
            EvidenceChunkRecord.run_id == run_id)).all()
        claims = s.scalars(select(FieldClaimRecord).where(
            FieldClaimRecord.run_id == run_id)).all()
        cites = s.scalars(select(ClaimCitationRecord).where(
            ClaimCitationRecord.run_id == run_id)).all()
        attempts = s.scalars(select(FirecrawlAttemptRecord).where(
            FirecrawlAttemptRecord.run_id == run_id)).all()
        ledger = s.scalars(select(ProviderUsageRecord).where(
            ProviderUsageRecord.run_id == run_id,
            ProviderUsageRecord.provider == "firecrawl")).all()
    direct = [d for d in docs if d.attempt == "direct"]
    notes = Counter(str((d.escalation or {}).get("firecrawl")).split(":", 1)[0]
                    for d in direct if (d.escalation or {}).get("firecrawl"))
    retry_kinds = Counter((d.escalation or {}).get("retry_reason") for d in fc_docs)
    not_sent_kinds = Counter((d.escalation or {}).get("retry_reason") for d in direct
                             if (d.escalation or {}).get("firecrawl"))
    fc_chunks = [c for c in chunks if c.document_id in fc_ids]
    fc_chunk_ids = {c.chunk_id for c in fc_chunks}
    by_claim: dict[int, list] = {}
    for c in cites:
        by_claim.setdefault(c.claim_id, []).append(c)
    auto = automatic_decisions(EvidenceRepository(engine).resolutions(run_id))
    auto_selected = {u for d in auto.values() if d["status"] == "RESOLVED"
                     for u in d["selected"]}
    citing, only_fc = [], []
    for cl in claims:
        refs = by_claim.get(cl.id, [])
        fc_refs = [r for r in refs if r.document_id in fc_ids or r.chunk_id in fc_chunk_ids]
        if not fc_refs:
            continue
        row = {"field_key": cl.field_key, "value": cl.value, "validation": cl.validation_outcome,
               "auto_accepted": cl.claim_uid in auto_selected,
               "only_firecrawl_evidence": len(fc_refs) == len(refs),
               "quote": fc_refs[0].quote[:240], "url": fc_refs[0].url}
        citing.append(row)
        if row["only_firecrawl_evidence"]:
            only_fc.append(row)
    def credit(d):
        e = d.escalation or {}
        return {"reserved": e.get("credits_reserved"), "estimated": e.get("credits_estimated"),
                "actual": e.get("credits_actual")}
    documents = [{
        "url": d.requested_url, "retry_reason": (d.escalation or {}).get("retry_reason"),
        "recovery_status": (d.escalation or {}).get("recovery_status"),
        "status": d.status, "error_kind": d.error_kind, "chunks": d.chunk_count,
        "representation": (d.escalation or {}).get("representation"),
        "profiles": {k: v for k, v in ((d.escalation or {}).get("representation_reason")
                                       or {}).items() if k != "rule"},
        "coverage": (d.escalation or {}).get("coverage"),
        "proxy_used": (d.escalation or {}).get("proxy_used"), **credit(d)} for d in fc_docs]
    teams = {json.dumps((d.escalation or {}).get("round_team_credits"), sort_keys=True)
             for d in fc_docs if (d.escalation or {}).get("round_team_credits")}
    sources = [r for r in citing if r["field_key"] in ("water_source", "reservoir_names")]
    return {
        "run_id": run_id,
        "direct_documents": len(direct),
        "direct_outcomes": dict(Counter(d.error_kind or d.status for d in direct)),
        "firecrawl_sent": len(fc_docs), "sent_by_retry_reason": dict(retry_kinds),
        "not_sent": dict(notes), "not_sent_by_retry_reason": dict(not_sent_kinds),
        "recovery": dict(Counter((d.escalation or {}).get("recovery_status") for d in fc_docs)),
        "usable_documents": sum(1 for d in fc_docs if d.status == "OK" and d.chunk_count),
        "new_evidence_passages": len(fc_chunks),
        "claims_citing_firecrawl": len(citing),
        "claims_only_firecrawl_evidence": len(only_fc),
        "claims_by_validation": dict(Counter(r["validation"] for r in citing)),
        "claims_auto_accepted": sum(r["auto_accepted"] for r in citing),
        "source_claims_citing_firecrawl": sources,
        "claims": citing,
        "credits": {
            "attempt_rows": len(attempts),
            "states": dict(Counter(a.state for a in attempts)),
            "budget_used_conservative": sum(
                (a.credits_estimated or 0) if a.state == "SETTLED" else a.credits_reserved
                for a in attempts),
            "estimated_sum": sum(d["estimated"] or 0 for d in documents),
            "actual_reported_sum": (sum(d["actual"] for d in documents
                                        if d["actual"] is not None)
                                    if any(d["actual"] is not None for d in documents)
                                    else None),
            "ledger_rows": len(ledger),
            "round_team_credits": [json.loads(t) for t in sorted(teams)]},
        "documents": documents,
    }

def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser()
    p.add_argument("run_id")
    p.add_argument("--out")
    a = p.parse_args()
    r = report(a.run_id)
    text = json.dumps(r, indent=1, default=str, ensure_ascii=False)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    print(text[:20000])
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
