"""The relationship graph of a stored run, rebuilt on request from its stored evidence (for
review cards of runs not finalized yet, and for the offline builder). Read only, never
persisted from here, never read by validation, resolution or acceptance (guard test)."""

from typing import Any
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session
from water_source_research.db.evidence_models import EvidenceChunkRecord, SourceDocumentRecord
from water_source_research.db.research_models import ResearchJobRecord
from water_source_research.schemas.research import CityResearchState, EvidenceChunk
from water_source_research.services.evidence_store import EvidenceRepository
from water_source_research.services.revalidate import claims_as_extracted
from water_source_research.services.supply_relationships import build_relationships

def stored_inputs(engine: Engine, run_id: str
                  ) -> tuple[CityResearchState, list[EvidenceChunk], dict, dict]:
    """(research, chunks, documents by url, document ids by url) of a stored run; claims as
    extracted (reviewer corrections left out)."""
    evidence = EvidenceRepository(engine)
    with Session(engine) as s:
        job = s.get(ResearchJobRecord, run_id)
        if job is None:
            raise LookupError(run_id)
        docs = s.scalars(select(SourceDocumentRecord).where(
            SourceDocumentRecord.run_id == run_id)).all()
        by_id = {d.id: d for d in docs}
        rows = s.scalars(select(EvidenceChunkRecord).where(
            EvidenceChunkRecord.run_id == run_id).order_by(
            EvidenceChunkRecord.document_id, EvidenceChunkRecord.ordinal)).all()
        chunks = [EvidenceChunk(
            chunk_id=r.chunk_id, url=r.url, text=r.text, page_number=r.page_number,
            title=(by_id[r.document_id].title or "") if r.document_id in by_id else "",
            source_type=(by_id[r.document_id].source_type or "OTHER")
            if r.document_id in by_id else "OTHER") for r in rows]
        documents, ids = {}, {}
        for d in docs:
            for u in (d.final_url, d.requested_url, d.canonical_url):
                if u:
                    documents.setdefault(u, {"title": d.title, "source_type": d.source_type,
                                             "source_date": d.source_date,
                                             "media_type": d.media_type})
                    ids.setdefault(u, d.id)
        city, country = job.city_name, job.country
    research, _ = claims_as_extracted(evidence.claims(run_id), city, country)
    return research, chunks, documents, ids

def build_from_store(engine: Engine, run_id: str,
                     links: dict | None = None) -> dict[str, Any]:
    """The run's graph built now from its stored evidence and links (not stored)."""
    research, chunks, documents, ids = stored_inputs(engine, run_id)
    if links is None:
        links = EvidenceRepository(engine).document_links(run_id)
    graph = build_relationships(research, chunks, documents, None, ids, links)
    return graph.model_dump(mode="json")
