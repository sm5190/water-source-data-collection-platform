"""Persist a run's evidence and claims (``db/evidence_models.py``).

Writes are idempotent so a retried or checkpoint-resumed run never duplicates rows:
documents/chunks are insert-or-skip on their (run, url) / (run, chunk) keys; field states,
claims and citations are replaced wholesale for the run at finalize. Failures propagate: a run
is not recorded as succeeded without its evidence (the research queue retries it).
"""

from collections.abc import Mapping
from typing import Any
from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session
from water_source_research.core.versions import EXTRACTION_PROMPT_VERSION
from water_source_research.db.evidence_models import (
    ClaimCitationRecord, EvidenceChunkRecord, FieldClaimRecord, RunFieldStateRecord,
    SourceDocumentRecord,
)
from water_source_research.llm.extraction import normalize_quote
from water_source_research.schemas.common import FieldResolutionStatus
from water_source_research.schemas.research import CityResearchState, EvidenceChunk

NO_REVIEW_NEEDED = {FieldResolutionStatus.RESOLVED, FieldResolutionStatus.NOT_APPLICABLE}

_DOC_COLUMNS = ("round", "requested_url", "canonical_url", "final_url", "title", "source_type",
                "acquisition_method", "media_type", "retrieved_at", "source_date",
                "content_sha256", "byte_size", "page_count", "status", "error_kind",
                "error_message", "raw_object_key", "artifact_error", "discovery_provider",
                "discovery_query", "duration_ms")
_LIMITS = {"requested_url": 2048, "canonical_url": 2048, "final_url": 2048, "title": 1000,
           "media_type": 255, "error_message": 1000, "artifact_error": 500,
           "discovery_query": 1000}

def _clip(key: str, value: Any) -> Any:
    limit = _LIMITS.get(key)
    return value[:limit] if limit and isinstance(value, str) else value

class EvidenceRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    # --- writes (graph) ----------------------------------------------------------------

    def save_documents(self, run_id: str, records: list[Mapping[str, Any]],
                       chunks: list[EvidenceChunk]) -> int:
        """Store one acquisition round. Returns the number of new chunk rows."""
        by_url: dict[str, list[EvidenceChunk]] = {}
        for c in chunks:
            by_url.setdefault(c.url, []).append(c)
        inserted = 0
        with Session(self.engine) as session, session.begin():
            for rec in records:
                doc = session.scalar(select(SourceDocumentRecord).where(
                    SourceDocumentRecord.run_id == run_id,
                    SourceDocumentRecord.canonical_url == _clip("canonical_url",
                                                                rec["canonical_url"])))
                if doc is None:
                    doc = SourceDocumentRecord(
                        run_id=run_id, chunk_count=rec.get("chunks") or 0,
                        **{k: _clip(k, rec.get(k)) for k in _DOC_COLUMNS if k in rec})
                    session.add(doc)
                    session.flush()
                doc_chunks = by_url.get(rec.get("final_url") or "", []) \
                    if rec.get("status") == "OK" else []
                existing = set(session.scalars(select(EvidenceChunkRecord.chunk_id).where(
                    EvidenceChunkRecord.run_id == run_id,
                    EvidenceChunkRecord.document_id == doc.id)))
                for ordinal, c in enumerate(doc_chunks):
                    if c.chunk_id in existing:
                        continue
                    existing.add(c.chunk_id)
                    session.add(EvidenceChunkRecord(
                        run_id=run_id, chunk_id=c.chunk_id, document_id=doc.id, url=c.url,
                        page_number=c.page_number, ordinal=ordinal, text=c.text,
                        char_count=len(c.text)))
                    inserted += 1
        return inserted

    def save_results(self, run_id: str, research_state: Mapping[str, Any],
                     needs_evidence: list[str] | None = None,
                     field_keys: list[str] | None = None) -> int:
        """Replace the run's field states, claims and citations. Returns claims stored.

        ``field_keys`` limits rows to the fields researched in this run (None = all)."""
        research = CityResearchState.model_validate(research_state)
        missing = set(needs_evidence or [])
        keys = list(field_keys) if field_keys is not None else list(research.fields)
        with Session(self.engine) as session, session.begin():
            claim_ids = select(FieldClaimRecord.id).where(FieldClaimRecord.run_id == run_id)
            session.execute(delete(ClaimCitationRecord).where(
                ClaimCitationRecord.claim_id.in_(claim_ids)))
            session.execute(delete(FieldClaimRecord).where(FieldClaimRecord.run_id == run_id))
            session.execute(delete(RunFieldStateRecord).where(
                RunFieldStateRecord.run_id == run_id))
            chunks = self._chunk_index(session, run_id)
            stored = 0
            for key in keys:
                fs = research.fields.get(key)
                if fs is None:
                    continue
                selected_id = None
                claims = list(fs.candidate_claims)
                if fs.selected_claim is not None and fs.selected_claim not in claims:
                    claims.append(fs.selected_claim)
                for claim in claims:
                    row = FieldClaimRecord(
                        run_id=run_id, field_key=key, value=claim.value,
                        value_status=claim.value_status.value,
                        value_origin=claim.value_origin.value, confidence=claim.confidence,
                        is_selected=claim == fs.selected_claim,
                        prompt_version=EXTRACTION_PROMPT_VERSION)
                    session.add(row)
                    session.flush()
                    stored += 1
                    if row.is_selected:
                        selected_id = row.id
                    for ref in claim.evidence:
                        doc_id, chunk_id = _match_chunk(chunks, ref.url, ref.page_number,
                                                        ref.passage)
                        session.add(ClaimCitationRecord(
                            claim_id=row.id, run_id=run_id, document_id=doc_id,
                            chunk_id=chunk_id, url=ref.url[:2048],
                            title=(ref.title or None) and ref.title[:1000],
                            page_number=ref.page_number, quote=ref.passage,
                            retrieved_at=ref.retrieved_at))
                session.add(RunFieldStateRecord(
                    run_id=run_id, field_key=key, status=fs.status.value,
                    review_required=fs.status not in NO_REVIEW_NEEDED,
                    needs_evidence=key in missing, selected_claim_id=selected_id))
        return stored

    @staticmethod
    def _chunk_index(session: Session, run_id: str
                     ) -> dict[tuple[str, int | None], list[tuple[int, str, str]]]:
        rows = session.execute(select(
            EvidenceChunkRecord.url, EvidenceChunkRecord.page_number,
            EvidenceChunkRecord.document_id, EvidenceChunkRecord.chunk_id,
            EvidenceChunkRecord.text).where(EvidenceChunkRecord.run_id == run_id)).all()
        index: dict[tuple[str, int | None], list[tuple[int, str, str]]] = {}
        for url, page, doc_id, chunk_id, text in rows:
            index.setdefault((url, page), []).append((doc_id, chunk_id, normalize_quote(text)))
        return index

    # --- reads (API / smoke test) ------------------------------------------------------

    def documents(self, run_id: str) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            rows = session.scalars(select(SourceDocumentRecord).where(
                SourceDocumentRecord.run_id == run_id).order_by(SourceDocumentRecord.id)).all()
            return [_doc_dict(r) for r in rows]

    def chunks(self, run_id: str, document_id: int | None = None,
               limit: int = 200) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            q = select(EvidenceChunkRecord).where(EvidenceChunkRecord.run_id == run_id)
            if document_id is not None:
                q = q.where(EvidenceChunkRecord.document_id == document_id)
            rows = session.scalars(q.order_by(EvidenceChunkRecord.document_id,
                                              EvidenceChunkRecord.ordinal).limit(limit)).all()
            return [{"chunk_id": r.chunk_id, "document_id": r.document_id, "url": r.url,
                     "page_number": r.page_number, "ordinal": r.ordinal, "text": r.text}
                    for r in rows]

    def claims(self, run_id: str) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            docs = {d.id: d for d in session.scalars(select(SourceDocumentRecord).where(
                SourceDocumentRecord.run_id == run_id))}
            claims = session.scalars(select(FieldClaimRecord).where(
                FieldClaimRecord.run_id == run_id).order_by(FieldClaimRecord.id)).all()
            cites: dict[int, list[ClaimCitationRecord]] = {}
            for c in session.scalars(select(ClaimCitationRecord).where(
                    ClaimCitationRecord.run_id == run_id).order_by(ClaimCitationRecord.id)):
                cites.setdefault(c.claim_id, []).append(c)
            out = []
            for c in claims:
                out.append({
                    "claim_id": c.id, "field_key": c.field_key, "value": c.value,
                    "value_status": c.value_status, "value_origin": c.value_origin,
                    "confidence": c.confidence, "is_selected": c.is_selected,
                    "prompt_version": c.prompt_version,
                    "citations": [_citation_dict(x, docs.get(x.document_id or -1))
                                  for x in cites.get(c.id, [])],
                })
            return out

    def field_states(self, run_id: str) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            rows = session.scalars(select(RunFieldStateRecord).where(
                RunFieldStateRecord.run_id == run_id).order_by(RunFieldStateRecord.id)).all()
            return [{"field_key": r.field_key, "status": r.status,
                     "review_required": r.review_required, "needs_evidence": r.needs_evidence,
                     "selected_claim_id": r.selected_claim_id} for r in rows]

    def counts(self, run_id: str) -> dict[str, int]:
        with Session(self.engine) as session:
            def n(model) -> int:
                return session.scalar(select(func.count()).select_from(model)
                                      .where(model.run_id == run_id)) or 0
            return {"documents": n(SourceDocumentRecord), "chunks": n(EvidenceChunkRecord),
                    "claims": n(FieldClaimRecord), "citations": n(ClaimCitationRecord),
                    "field_states": n(RunFieldStateRecord)}

def _match_chunk(index, url: str, page: int | None, quote: str) -> tuple[int | None, str | None]:
    q = normalize_quote(quote)
    candidates = index.get((url, page), [])
    for doc_id, chunk_id, text in candidates:
        if q in text:
            return doc_id, chunk_id
    if candidates:  # quote spans a chunk boundary: still the right document
        return candidates[0][0], None
    return None, None

def _doc_dict(r: SourceDocumentRecord) -> dict[str, Any]:
    return {"document_id": r.id, "chunks": r.chunk_count,
            **{k: getattr(r, k) for k in _DOC_COLUMNS}}

def _citation_dict(c: ClaimCitationRecord, doc: SourceDocumentRecord | None) -> dict[str, Any]:
    return {"url": c.url, "title": c.title, "page_number": c.page_number, "quote": c.quote,
            "retrieved_at": c.retrieved_at, "chunk_id": c.chunk_id,
            "document_id": c.document_id,
            "source_type": doc.source_type if doc else None,
            "acquisition_method": doc.acquisition_method if doc else None,
            "source_date": doc.source_date if doc else None,
            "raw_object_key": doc.raw_object_key if doc else None}
