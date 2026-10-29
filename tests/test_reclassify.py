"""Stored documents are re-checked under the current source-type rules when a run is replayed,
reprocessed or revalidated; the stored rows of the original run are never changed."""

import hashlib
import pytest
from procedure_helpers import make_engine
from water_source_research.acquisition.replay import ReplayAcquisition
from water_source_research.db.models import Base
from water_source_research.discovery.normalize import reclassify
from water_source_research.schemas.research import EvidenceChunk
from water_source_research.services.evidence_store import EvidenceRepository

MEMBER_PAGE = {"final_url": "https://www.leadingutilities.org/members/houston-water",
               "title": "Houston Water | Leading Utilities of the World",
               "source_type": "UTILITY", "discovery_provider": "searxng"}

def test_a_member_directory_page_is_no_longer_a_utility():
    # stored as UTILITY on Oct 19 (Houston replay 759d7681); the directory rule came later
    assert reclassify(MEMBER_PAGE, "Houston") == "OTHER"

@pytest.mark.parametrize("record, expected", [
    ({"final_url": "https://nairobiwater.co.ke/thika-dam/",
      "title": "Thika Dam - Nairobi City Water and Sewerage Company", "source_type": "UTILITY",
      "discovery_provider": "searxng", "city": "Nairobi"}, "UTILITY"),
    ({"final_url": "https://www.toronto.ca/services-payments/water-environment/"
                   "tap-water-in-toronto/",
      "title": "Tap Water in Toronto – City of Toronto", "source_type": "GOVERNMENT",
      "discovery_provider": "searxng", "city": "Toronto"}, "GOVERNMENT"),
    ({"final_url": "https://www.twdb.texas.gov/surfacewater/rivers/reservoirs/houston/",
      "title": "Lake Houston | Texas Water Development Board", "source_type": "GOVERNMENT",
      "discovery_provider": "searxng"}, "GOVERNMENT"),
    # academic from a scholarly provider stays academic whatever the host
    ({"final_url": "https://example.org/paper", "title": "Paper", "source_type": "ACADEMIC",
      "discovery_provider": "openalex"}, "ACADEMIC"),
    ({"final_url": "https://example.org/paper", "title": "Paper", "source_type": "ACADEMIC",
      "discovery_provider": None}, "ACADEMIC"),
])
def test_current_rules_keep_what_they_still_support(record, expected):
    assert reclassify(record, record.get("city", "Houston")) == expected

def test_replay_serves_the_current_source_type_and_lists_the_change():
    doc = {**MEMBER_PAGE, "document_id": 1, "round": 1, "status": "OK",
           "requested_url": MEMBER_PAGE["final_url"], "canonical_url": MEMBER_PAGE["final_url"]}
    chunk = {"chunk_id": "c1", "document_id": 1, "url": MEMBER_PAGE["final_url"],
             "text": "Houston Water July 2018", "page_number": None, "ordinal": 0}
    import asyncio
    replay = ReplayAcquisition([doc], [chunk], city="Houston")
    out = asyncio.run(replay.acquire_round([], [], 1))
    assert out.records[0]["source_type"] == "OTHER"
    assert out.chunks[0].source_type == "OTHER"
    assert replay.reclassified == [{"url": MEMBER_PAGE["final_url"], "from": "UTILITY",
                                    "to": "OTHER"}]

def test_copied_evidence_is_reclassified_and_the_original_is_not(tmp_path):
    engine = make_engine(tmp_path, "reclassify.db")
    Base.metadata.create_all(engine)
    repo = EvidenceRepository(engine)
    url = MEMBER_PAGE["final_url"]
    record = {**MEMBER_PAGE, "round": 1, "requested_url": url, "canonical_url": url,
              "status": "OK", "content_sha256": hashlib.sha256(b"x").hexdigest(), "chunks": 1}
    repo.save_documents("orig", [record], [EvidenceChunk(chunk_id="c1", url=url,
                                                         text="Houston Water",
                                                         source_type="UTILITY")])
    changes: list = []
    repo.copy_run_evidence("orig", "copy", reclassify_city="Houston", changes=changes)
    assert changes == [{"url": url, "from": "UTILITY", "to": "OTHER"}]
    assert [d["source_type"] for d in repo.documents("copy")] == ["OTHER"]
    assert [d["source_type"] for d in repo.documents("orig")] == ["UTILITY"]
    assert {c["source_type"] for c in repo.evidence_chunks("copy")} == {"OTHER"}
