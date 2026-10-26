"""Saved-document replay: original acquisition rounds introduced in sequence (so later-round
re-extraction is really exercised), complete provenance under a new run, and the parent run's
rows untouched."""

import asyncio
import hashlib
import json
import pytest
from procedure_helpers import make_engine
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from water_source_research.db.evidence_models import (
    ClaimCitationRecord, EvidenceChunkRecord, FieldClaimRecord, SourceDocumentRecord,
)
from water_source_research.db.models import Base
from water_source_research.db.research_models import ResearchJobRecord
from water_source_research.schemas.research import EvidenceChunk, FieldClaim
from water_source_research.services.evidence_store import EvidenceRepository
from water_source_research.services.replay import replay_run

PARENT = "parent-run-0001"
DOCS = [  # (round, url, source type, text)
    (1, "https://nairobiwater.example/dams", "UTILITY",
     "Nairobi Water operates the Ndakaini Dam reservoir for the city."),
    (2, "https://gov.example/kenya-water", "GOVERNMENT",
     "Kenya's national water report covers many counties."),
    (3, "https://ngo.example/upper-tana.pdf", "OTHER",
     ("The Upper Tana basin supplies Nairobi through the Sasumua and Ndakaini dams drawing "
      "water from the Chania and Thika rivers respectively.")),
]

@pytest.fixture
def engine(tmp_path):
    e = make_engine(tmp_path, "replay.db")
    Base.metadata.create_all(e)
    with Session(e) as s, s.begin():
        s.add(ResearchJobRecord(run_id=PARENT, city_name="Nairobi", country="Kenya",
                                city_key="nairobi|kenya", status="AWAITING_REVIEW",
                                max_attempts=3, mode="RESEARCH",
                                field_keys=["water_source", "reservoir_names", "water_type"],
                                result={"discovery_rounds": 3}))
    repo = EvidenceRepository(e)
    for rnd, url, kind, text in DOCS:
        sha = hashlib.sha256(text.encode()).hexdigest()
        record = {"round": rnd, "requested_url": url, "canonical_url": url, "final_url": url,
                  "title": url, "source_type": kind, "status": "OK", "content_sha256": sha,
                  "chunks": 1, "acquisition_method": "DIRECT_HTTP"}
        chunk = EvidenceChunk(chunk_id=f"c{rnd}", url=url, text=text, source_type=kind)
        repo.save_documents(PARENT, [record], [chunk])
    return e

class Gateway:
    """Names every hydronym-looking phrase it is shown, citing it verbatim."""

    def __init__(self):
        self.calls: list[tuple[str, list[str]]] = []

    async def extract(self, bundle, run_id, field_key):
        shown = [i.chunk_id for i in bundle.items if i.kind == "evidence"]
        self.calls.append((field_key, shown))
        if field_key != "water_source":
            return []
        out = []
        for sid, src in bundle.sources.items():
            text = " ".join(src.passages)
            for name, kind in (("Ndakaini Dam", "reservoir"), ("Chania", "river"),
                               ("Thika rivers", "river")):
                if name in text:
                    out.append(FieldClaim(field_key=field_key, value=name, confidence=0.8,
                                          evidence=[bundle.evidence_ref(sid, passage=name)]))
        return out

def snapshot_parent(engine):
    with Session(engine) as s:
        out = {}
        for model in (SourceDocumentRecord, EvidenceChunkRecord, FieldClaimRecord,
                      ClaimCitationRecord):
            rows = s.scalars(select(model).where(model.run_id == PARENT)).all()
            out[model.__tablename__] = hashlib.sha256(json.dumps(sorted(
                [{k: str(v) for k, v in vars(r).items() if not k.startswith("_")}
                 for r in rows], key=str)).encode()).hexdigest()
        job = s.get(ResearchJobRecord, PARENT)
        out["job"] = (job.status, json.dumps(job.result, sort_keys=True))
        return out

def test_replay_introduces_rounds_in_order_and_reextracts(engine):
    before = snapshot_parent(engine)
    gateway = Gateway()
    _, final = asyncio.run(replay_run(engine, PARENT, gateway))
    assert final["replay"]["rounds_served"] == [1, 2, 3]
    ws_calls = [shown for f, shown in gateway.calls if f == "water_source"]
    # round 1 saw only round-1 evidence; the round-3 document came later
    assert "c3" not in ws_calls[0]
    assert len(ws_calls) >= 2 and any("c3" in shown for shown in ws_calls[1:])
    claims = final["research_state"]["fields"]["water_source"]["candidate_claims"]
    later = [c for c in claims if (c.get("qualifiers") or {}).get("extraction_round") == 3]
    assert {c["value"] for c in later} >= {"Chania", "Thika rivers"}
    assert final["reextractions"].get("water_source") == 1
    # the parent is untouched
    assert snapshot_parent(engine) == before

def test_replay_provenance_is_complete_and_separate(engine):
    run_id, _ = asyncio.run(replay_run(engine, PARENT, Gateway()))
    repo = EvidenceRepository(engine)
    parent_docs = {(d["canonical_url"], d["content_sha256"]): d for d in repo.documents(PARENT)}
    replay_docs = repo.documents(run_id)
    assert len(replay_docs) == len(parent_docs)
    for d in replay_docs:
        original = parent_docs[(d["canonical_url"], d["content_sha256"])]
        assert d["document_id"] != original["document_id"]  # its own row
        assert d["round"] == original["round"]
    with Session(engine) as s:
        job = s.get(ResearchJobRecord, run_id)
        assert (job.mode, job.parent_run_id, job.status) == ("REPLAY", PARENT, "SUCCEEDED")
        assert s.scalar(select(func.count()).select_from(FieldClaimRecord)
                        .where(FieldClaimRecord.run_id == PARENT)) == 0
    # every replay claim traces claim -> chunk -> replay document -> original document
    claims = [c for c in repo.claims(run_id) if c["field_key"] == "water_source"]
    assert claims
    chunks = {c["chunk_id"]: c for c in repo.chunks(run_id, limit=100)}
    docs = {d["document_id"]: d for d in replay_docs}
    for c in claims:
        for cite in c["citations"]:
            chunk = chunks[cite["chunk_id"]]
            assert cite["quote"] in chunk["text"]
            doc = docs[chunk["document_id"]]
            assert (doc["canonical_url"], doc["content_sha256"]) in parent_docs
            assert cite["url"] == doc["final_url"]
