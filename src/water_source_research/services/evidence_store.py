"""Persist a run's evidence and claims (``db/evidence_models.py``).

Writes are idempotent so a retried or checkpoint-resumed run never duplicates rows:
documents/chunks are insert-or-skip on their (run, url) / (run, chunk) keys; claims are
upserted by their stable claim_uid (citations written once), resolution decisions are
insert-or-skip by seq, and only the field-state snapshot is replaced at finalize. Failures propagate: a run
is not recorded as succeeded without its evidence (the research queue retries it).
"""

from collections.abc import Mapping
from typing import Any
from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session
from water_source_research.core.versions import EXTRACTION_PROMPT_VERSION
from water_source_research.db.evidence_models import (
    ClaimCitationRecord, DiscoveryResultRecord, DocumentLinkRecord, EvidenceChunkRecord,
    FieldClaimRecord,
    FieldResolutionRecord, RunFieldStateRecord, RunSupplyRelationshipRecord,
    RunUtilityCandidateRecord, SourceDocumentRecord,
)
from water_source_research.llm.extraction import normalize_quote
from water_source_research.schemas.common import FINAL_STATUSES
from water_source_research.schemas.research import (
    CityResearchState, EvidenceChunk, FieldClaim, FieldState,
)
from water_source_research.services.normalization import claim_uid, quote_in

NO_REVIEW_NEEDED = FINAL_STATUSES

_DOC_COLUMNS = ("round", "requested_url", "canonical_url", "final_url", "title", "source_type",
                "publisher_kind", "classification_version", "classification_signals",
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

    def save_discovery_results(self, run_id: str, round_no: int,
                               rows: list[Mapping[str, Any]]) -> int:
        """Raw search hits of one discovery round (insert-or-skip, so a resumed round doesn't
        duplicate them). Returns the number of new rows."""
        if not rows:
            return 0
        with Session(self.engine) as session, session.begin():
            existing = {tuple(row) for row in session.execute(select(
                DiscoveryResultRecord.call_index, DiscoveryResultRecord.result_index).where(
                DiscoveryResultRecord.run_id == run_id,
                DiscoveryResultRecord.round == round_no))}
            new = [r for r in rows
                   if (r["call_index"], r["result_index"]) not in existing]
            session.add_all(DiscoveryResultRecord(
                run_id=run_id, round=round_no, call_index=r["call_index"],
                result_index=r["result_index"], provider=r["provider"][:64],
                strategy=r["strategy"][:64], query=_cut(r.get("query"), 1000),
                field_key=r.get("field_key"), rank=r.get("rank"), url=r["url"][:2048],
                canonical_url=_cut(r.get("canonical_url"), 2048),
                title=_cut(r.get("title"), 1000), source_type=r.get("source_type"),
                publisher_kind=r.get("publisher_kind"),
                classification_version=r.get("classification_version"),
                doi=_cut(r.get("doi"), 255), retained=bool(r.get("retained")))
                for r in new)
        return len(new)

    def save_utility_registry(self, run_id: str, round_no: int,
                              registry: Mapping[str, Any]) -> int:
        """The run's utility registry at the start of a round (insert-or-skip by candidate,
        so a resumed round doesn't duplicate it). Returns the number of new rows."""
        candidates = registry.get("candidates") or []
        if not candidates:
            return 0
        with Session(self.engine) as session, session.begin():
            existing = set(session.scalars(select(RunUtilityCandidateRecord.candidate_key).where(
                RunUtilityCandidateRecord.run_id == run_id,
                RunUtilityCandidateRecord.round == round_no)))
            new = [c for c in candidates if c["key"][:255] not in existing]
            session.add_all(RunUtilityCandidateRecord(
                run_id=run_id, round=round_no, candidate_key=c["key"][:255],
                name=c["name"][:500], status=c["status"],
                status_reason=_cut(c.get("status_reason"), 1000),
                verified_roles=c.get("verified_roles") or [], aliases=c.get("aliases") or [],
                bound_hosts=c.get("bound_hosts") or [],
                predecessor_of=c.get("predecessor_of") or [],
                successor_of=c.get("successor_of") or [], origins=c.get("origins") or [],
                evidence=c.get("evidence") or [],
                policy_version=str(registry.get("policy_version"))[:32],
                identity=str(registry.get("identity"))[:32]) for c in new)
        return len(new)

    def save_document_links(self, run_id: str, links_by_url: Mapping[str, list[Mapping]],
                            basis: str = "PARSED", observed_at: str | None = None) -> int:
        """Outbound links of the run's documents (url -> links), insert-or-skip per
        (document, target, anchor). Returns the number of new rows."""
        import hashlib
        if not links_by_url:
            return 0
        added = 0
        with Session(self.engine) as session, session.begin():
            docs = session.scalars(select(SourceDocumentRecord).where(
                SourceDocumentRecord.run_id == run_id)).all()
            ids: dict[str, SourceDocumentRecord] = {}
            for d in docs:
                for u in (d.final_url, d.requested_url, d.canonical_url):
                    if u:
                        ids.setdefault(u, d)
            for url, links in links_by_url.items():
                doc = ids.get(url)
                if doc is None:
                    continue
                existing = set(session.scalars(select(DocumentLinkRecord.link_key).where(
                    DocumentLinkRecord.run_id == run_id,
                    DocumentLinkRecord.document_id == doc.id)))
                for link in links:
                    key = hashlib.sha1(f"{link['target_url']}|{link.get('anchor_text', '')}"
                                       .encode()).hexdigest()
                    if key in existing:
                        continue
                    existing.add(key)
                    session.add(DocumentLinkRecord(
                        run_id=run_id, document_id=doc.id, source_url=url[:2048], link_key=key,
                        target_url=link["target_url"][:2048],
                        target_host=link["target_host"][:255],
                        anchor_text=(link.get("anchor_text") or "")[:500],
                        context=(link.get("context") or "")[:300],
                        heading=(link.get("heading") or "")[:300],
                        in_footer=bool(link.get("in_footer")), basis=basis,
                        observed_at=observed_at or doc.retrieved_at))
                    added += 1
        return added

    def document_links(self, run_id: str) -> dict[str, list[dict[str, Any]]]:
        """source document URL -> its stored links."""
        out: dict[str, list[dict[str, Any]]] = {}
        with Session(self.engine) as session:
            for r in session.scalars(select(DocumentLinkRecord).where(
                    DocumentLinkRecord.run_id == run_id).order_by(DocumentLinkRecord.id)):
                out.setdefault(r.source_url, []).append({
                    "document_id": r.document_id, "target_url": r.target_url,
                    "target_host": r.target_host, "anchor_text": r.anchor_text,
                    "context": r.context, "heading": r.heading, "in_footer": r.in_footer,
                    "basis": r.basis, "observed_at": r.observed_at})
        return out

    def save_relationships(self, run_id: str, graph: Mapping[str, Any]) -> int:
        """The run's relationship graph (services/supply_relationships.py) for its policy
        version: replaces that version's rows, so a rebuild never duplicates; rows of other
        versions are kept. Rejected statements are stored with status REJECTED."""
        version = str(graph.get("policy_version"))[:32]
        meta = {"identity": str(graph.get("identity"))[:32],
                "geo_basis": str(graph.get("geo_basis"))[:24], "policy_version": version}
        rows = [RunSupplyRelationshipRecord(
            run_id=run_id, relationship_key=r["key"][:1000], kind=r["kind"],
            subject_id=r["subject"]["entity_id"][:500], object_id=r["object"]["entity_id"][:500],
            status=r["status"], temporal=r.get("temporal"), payload=r, **meta)
            for r in graph.get("relationships") or []]
        seen: set[str] = set()
        for r in graph.get("rejected") or []:
            key = f"REJECTED|{r['kind']}|{r['subject']}|{r['object']}|{r['url']}|{r['quote']}"
            key = key[:1000]
            if key in seen:
                continue
            seen.add(key)
            rows.append(RunSupplyRelationshipRecord(
                run_id=run_id, relationship_key=key, kind=r["kind"],
                subject_id=str(r["subject"])[:500], object_id=str(r["object"])[:500],
                status="REJECTED", temporal=None, payload=r, **meta))
        with Session(self.engine) as session, session.begin():
            session.execute(delete(RunSupplyRelationshipRecord).where(
                RunSupplyRelationshipRecord.run_id == run_id,
                RunSupplyRelationshipRecord.policy_version == version))
            session.add_all(rows)
        return len(rows)

    def relationships(self, run_id: str, policy_version: str | None = None
                      ) -> dict[str, Any]:
        """{policy_version, geo_basis, identity, relationships, rejected} for the run's newest
        (or the given) policy version; empty lists when none was built."""
        with Session(self.engine) as session:
            q = select(RunSupplyRelationshipRecord).where(
                RunSupplyRelationshipRecord.run_id == run_id)
            rows = session.scalars(q.order_by(RunSupplyRelationshipRecord.id)).all()
        versions = sorted({r.policy_version for r in rows})
        version = policy_version or (versions[-1] if versions else None)
        rows = [r for r in rows if r.policy_version == version]
        return {"policy_version": version, "versions": versions,
                "geo_basis": rows[0].geo_basis if rows else None,
                "identity": rows[0].identity if rows else None,
                "relationships": [r.payload for r in rows if r.status != "REJECTED"],
                "rejected": [r.payload for r in rows if r.status == "REJECTED"]}

    def utility_candidates(self, run_id: str) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            rows = session.scalars(select(RunUtilityCandidateRecord).where(
                RunUtilityCandidateRecord.run_id == run_id).order_by(
                RunUtilityCandidateRecord.round, RunUtilityCandidateRecord.id)).all()
            return [{"round": r.round, "key": r.candidate_key, "name": r.name,
                     "status": r.status, "status_reason": r.status_reason,
                     "verified_roles": r.verified_roles, "aliases": r.aliases,
                     "bound_hosts": r.bound_hosts, "predecessor_of": r.predecessor_of,
                     "successor_of": r.successor_of, "origins": r.origins,
                     "evidence": r.evidence, "policy_version": r.policy_version,
                     "identity": r.identity} for r in rows]

    def discovery_results(self, run_id: str) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            rows = session.scalars(select(DiscoveryResultRecord).where(
                DiscoveryResultRecord.run_id == run_id).order_by(
                DiscoveryResultRecord.round, DiscoveryResultRecord.call_index,
                DiscoveryResultRecord.result_index)).all()
            return [{"round": r.round, "provider": r.provider, "strategy": r.strategy,
                     "query": r.query, "field_key": r.field_key, "rank": r.rank,
                     "position": r.result_index + 1, "url": r.url,
                     "canonical_url": r.canonical_url, "title": r.title,
                     "source_type": r.source_type, "doi": r.doi, "retained": r.retained,
                     "retrieved_at": r.retrieved_at.isoformat() if r.retrieved_at else None}
                    for r in rows]

    def save_results(self, run_id: str, research_state: Mapping[str, Any],
                     needs_evidence: list[str] | None = None,
                     field_keys: list[str] | None = None,
                     resolution_log: list[Mapping[str, Any]] | None = None) -> int:
        """Store the run's claims, citations, resolution decisions and field-state snapshot.
        Returns the number of claims written or updated.

        Claims are upserted by their stable ``claim_uid`` (never deleted, so ids referenced by
        reviews and exports stay valid); citations are written once per claim. Resolution log
        entries are append-only (insert-or-skip by seq). The field-state rows are a current
        snapshot and are replaced. ``field_keys`` limits rows to the fields researched in this
        run (None = all)."""
        research = CityResearchState.model_validate(research_state)
        missing = set(needs_evidence or [])
        keys = list(field_keys) if field_keys is not None else list(research.fields)
        with Session(self.engine) as session, session.begin():
            session.execute(delete(RunFieldStateRecord).where(
                RunFieldStateRecord.run_id == run_id))
            chunks = self._chunk_index(session, run_id)
            existing = {r.claim_uid: r for r in session.scalars(select(FieldClaimRecord).where(
                FieldClaimRecord.run_id == run_id, FieldClaimRecord.claim_uid.is_not(None)))}
            stored = 0
            for key in keys:
                fs = research.fields.get(key)
                if fs is None:
                    continue
                selected = _selected_uids(fs)
                claims = list(fs.candidate_claims)
                if fs.selected_claim is not None and fs.selected_claim not in claims:
                    claims.append(fs.selected_claim)
                selected_id = None
                for claim in claims:
                    uid = claim.claim_uid or claim_uid(claim)
                    row = existing.get(uid)
                    if row is None:
                        row = FieldClaimRecord(run_id=run_id, field_key=key, claim_uid=uid,
                                               prompt_version=EXTRACTION_PROMPT_VERSION)
                        session.add(row)
                        _set_claim_columns(row, claim, uid in selected)
                        session.flush()
                        existing[uid] = row
                        for ref in claim.evidence:
                            doc_id, chunk_id = _match_chunk(chunks, ref.url, ref.page_number,
                                                            ref.passage)
                            session.add(ClaimCitationRecord(
                                claim_id=row.id, run_id=run_id, document_id=doc_id,
                                chunk_id=chunk_id, url=ref.url[:2048],
                                title=(ref.title or None) and ref.title[:1000],
                                page_number=ref.page_number, quote=ref.passage,
                                retrieved_at=ref.retrieved_at))
                    else:
                        _set_claim_columns(row, claim, uid in selected)
                    stored += 1
                    if uid in selected and selected_id is None:
                        selected_id = row.id
                session.add(RunFieldStateRecord(
                    run_id=run_id, field_key=key, status=fs.status.value,
                    review_required=fs.status not in NO_REVIEW_NEEDED,
                    needs_evidence=key in missing, selected_claim_id=selected_id,
                    resolution=fs.resolution.model_dump(mode="json") if fs.resolution else None))
            self._append_resolutions(session, run_id, resolution_log or [])
        return stored

    @staticmethod
    def _append_resolutions(session: Session, run_id: str,
                            entries: list[Mapping[str, Any]]) -> None:
        stored = set(session.scalars(select(FieldResolutionRecord.seq).where(
            FieldResolutionRecord.run_id == run_id)))
        for e in entries:
            if e["seq"] in stored:
                continue
            session.add(FieldResolutionRecord(
                run_id=run_id, seq=e["seq"], field_key=e["field_key"],
                entity_key=str(e["entity_key"])[:255], from_status=e["from_status"],
                to_status=e["to_status"], rule=e["rule"][:64],
                reason_codes=list(e.get("reason_codes") or []),
                selected_claim_uids=list(e.get("selected_claim_uids") or []),
                candidate_claim_uids=list(e.get("candidate_claim_uids") or []),
                decided_by=e.get("decided_by", "RULES"),
                policy_version=e.get("policy_version", ""),
                review_decision_id=e.get("review_decision_id")))

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

    def copy_run_evidence(self, from_run: str, to_run: str, reclassify_city: str | None = None,
                          changes: list[dict[str, Any]] | None = None) -> int:
        """Copy a run's document and chunk rows to another run (reprocessing reuses stored
        evidence; raw files are content-addressed and shared). Insert-or-skip; returns the
        number of chunks copied. With ``reclassify_city`` each copied document gets its source
        type under the current rules (discovery/normalize.py::reclassify); every change is
        appended to ``changes`` ({url, from, to}). The source rows are never changed."""
        from water_source_research.discovery.normalize import reclassified_columns
        copied = 0
        with Session(self.engine) as session, session.begin():
            sources = session.scalars(select(SourceDocumentRecord).where(
                SourceDocumentRecord.run_id == from_run).order_by(SourceDocumentRecord.id)).all()
            for src in sources:
                doc = session.scalar(select(SourceDocumentRecord).where(
                    SourceDocumentRecord.run_id == to_run,
                    SourceDocumentRecord.canonical_url == src.canonical_url))
                if doc is None:
                    columns = {k: getattr(src, k) for k in _DOC_COLUMNS}
                    if reclassify_city is not None:
                        update = reclassified_columns(columns, reclassify_city)
                        if update and update["source_type"] != columns["source_type"] \
                                and changes is not None:
                            changes.append({"url": src.final_url or src.requested_url,
                                            "from": columns["source_type"],
                                            "to": update["source_type"]})
                        columns.update(update)
                    doc = SourceDocumentRecord(run_id=to_run, chunk_count=src.chunk_count,
                                               **columns)
                    session.add(doc)
                    session.flush()
                existing = set(session.scalars(select(EvidenceChunkRecord.chunk_id).where(
                    EvidenceChunkRecord.run_id == to_run)))
                for c in session.scalars(select(EvidenceChunkRecord).where(
                        EvidenceChunkRecord.document_id == src.id)
                        .order_by(EvidenceChunkRecord.ordinal)):
                    if c.chunk_id in existing:
                        continue
                    session.add(EvidenceChunkRecord(
                        run_id=to_run, chunk_id=c.chunk_id, document_id=doc.id, url=c.url,
                        page_number=c.page_number, ordinal=c.ordinal, text=c.text,
                        char_count=c.char_count))
                    copied += 1
                have = set(session.scalars(select(DocumentLinkRecord.link_key).where(
                    DocumentLinkRecord.run_id == to_run,
                    DocumentLinkRecord.document_id == doc.id)))
                for link in session.scalars(select(DocumentLinkRecord).where(
                        DocumentLinkRecord.document_id == src.id,
                        DocumentLinkRecord.run_id == from_run)):
                    if link.link_key not in have:
                        session.add(DocumentLinkRecord(
                            run_id=to_run, document_id=doc.id, source_url=link.source_url,
                            link_key=link.link_key, target_url=link.target_url,
                            target_host=link.target_host, anchor_text=link.anchor_text,
                            context=link.context, heading=link.heading,
                            in_footer=link.in_footer, basis=link.basis,
                            observed_at=link.observed_at))
        return copied

    def evidence_chunks(self, run_id: str) -> list[dict[str, Any]]:
        """Every chunk of the run as a serialized EvidenceChunk (graph state shape)."""
        with Session(self.engine) as session:
            docs = {d.id: d for d in session.scalars(select(SourceDocumentRecord).where(
                SourceDocumentRecord.run_id == run_id))}
            rows = session.scalars(select(EvidenceChunkRecord).where(
                EvidenceChunkRecord.run_id == run_id).order_by(
                EvidenceChunkRecord.document_id, EvidenceChunkRecord.ordinal)).all()
            out = []
            for r in rows:
                doc = docs.get(r.document_id)
                out.append(EvidenceChunk(
                    chunk_id=r.chunk_id, url=r.url, text=r.text, page_number=r.page_number,
                    title=doc.title if doc else None,
                    source_type=doc.source_type if doc else None,
                    acquisition_method=doc.acquisition_method if doc else None,
                    retrieved_at=doc.retrieved_at if doc else None).model_dump(mode="json"))
            return out

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

    def chunk(self, run_id: str, chunk_id: str) -> dict[str, Any] | None:
        """One evidence chunk with its document's title, source type and date."""
        with Session(self.engine) as session:
            row = session.scalar(select(EvidenceChunkRecord).where(
                EvidenceChunkRecord.run_id == run_id, EvidenceChunkRecord.chunk_id == chunk_id))
            if row is None:
                return None
            doc = session.get(SourceDocumentRecord, row.document_id)
            return {"chunk_id": row.chunk_id, "document_id": row.document_id, "url": row.url,
                    "page_number": row.page_number, "text": row.text,
                    "title": doc.title if doc else None,
                    "source_type": doc.source_type if doc else None,
                    "source_date": doc.source_date if doc else None}

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
                    "claim_id": c.id, "claim_uid": c.claim_uid, "field_key": c.field_key,
                    "entity_key": c.entity_key, "value": c.value,
                    "normalized_value": c.normalized_value,
                    "value_status": c.value_status, "value_origin": c.value_origin,
                    "confidence": c.confidence, "is_selected": c.is_selected,
                    "validation_outcome": c.validation_outcome,
                    "review_status": c.review_status, "original_value": c.original_value,
                    "validation": c.validation, "qualifiers": c.qualifiers,
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
                     "selected_claim_id": r.selected_claim_id, "resolution": r.resolution}
                    for r in rows]

    def resolutions(self, run_id: str) -> list[dict[str, Any]]:
        """The run's append-only resolution log, in order."""
        with Session(self.engine) as session:
            rows = session.scalars(select(FieldResolutionRecord).where(
                FieldResolutionRecord.run_id == run_id).order_by(FieldResolutionRecord.seq)).all()
            return [{"seq": r.seq, "field_key": r.field_key, "entity_key": r.entity_key,
                     "from_status": r.from_status, "to_status": r.to_status, "rule": r.rule,
                     "reason_codes": r.reason_codes, "selected_claim_uids": r.selected_claim_uids,
                     "candidate_claim_uids": r.candidate_claim_uids, "decided_by": r.decided_by,
                     "policy_version": r.policy_version,
                     "review_decision_id": r.review_decision_id,
                     "created_at": r.created_at.isoformat() if r.created_at else None}
                    for r in rows]

    def counts(self, run_id: str) -> dict[str, int]:
        with Session(self.engine) as session:
            def n(model) -> int:
                return session.scalar(select(func.count()).select_from(model)
                                      .where(model.run_id == run_id)) or 0
            return {"documents": n(SourceDocumentRecord), "chunks": n(EvidenceChunkRecord),
                    "claims": n(FieldClaimRecord), "citations": n(ClaimCitationRecord),
                    "field_states": n(RunFieldStateRecord),
                    "resolutions": n(FieldResolutionRecord)}

def _selected_uids(fs: FieldState) -> set[str]:
    uids = {u for e in (fs.resolution.entities if fs.resolution else [])
            for u in e.selected_claim_uids}
    if fs.selected_claim is not None:
        uids.add(fs.selected_claim.claim_uid or claim_uid(fs.selected_claim))
    return uids

def _set_claim_columns(row: FieldClaimRecord, claim: FieldClaim, selected: bool) -> None:
    """Fields that normalization/resolution may refine on a later pass (the extracted
    value, status, origin and citations of a claim_uid never change)."""
    row.value = claim.value
    row.value_status = claim.value_status.value
    row.value_origin = claim.value_origin.value
    row.confidence = claim.confidence
    row.is_selected = selected
    row.entity_key = claim.entity_key and claim.entity_key[:255]
    row.original_value = claim.original_value
    row.normalized_value = claim.normalized_value
    row.qualifiers = claim.qualifiers.model_dump(mode="json") if claim.qualifiers else None
    row.validation_outcome = claim.validation.outcome.value if claim.validation else None
    row.validation = claim.validation.model_dump(mode="json") if claim.validation else None
    row.review_status = claim.review_status

def _match_chunk(index, url: str, page: int | None, quote: str) -> tuple[int | None, str | None]:
    """(document id, chunk id) of the chunk holding ``quote`` — the same matching as
    validation (normalization.quote_in: elided quotes match their parts in order)."""
    candidates = index.get((url, page), [])
    for doc_id, chunk_id, text in candidates:
        if quote_in(text, quote):
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

def _cut(value: str | None, n: int) -> str | None:
    return value[:n] if isinstance(value, str) else value
