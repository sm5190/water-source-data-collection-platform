"""Saved-document replay: an acquisition adapter that serves a finished run's stored
documents and evidence chunks round by round, exactly in their original acquisition rounds,
instead of fetching anything. Used to measure retrieval / re-extraction / resolution changes
on identical evidence (scripts/replay_run.py).

Each round returns the parent's document records for that round (with their original status,
source type, URLs and content hash) and the chunks of its successfully acquired documents
(same text and content-addressed chunk ids). The acquire node persists them under the replay
run, so the replay gets its own document ids; the original is found again by
(canonical_url, content_sha256), which both copies share. Nothing of the parent is changed.
"""

from collections.abc import Mapping
from typing import Any
from water_source_research.discovery.normalize import reclassify
from water_source_research.schemas.research import EvidenceChunk
from water_source_research.services.evidence_store import EvidenceRepository
from .service import AcquisitionOutcome

# Columns copied from the parent's document records (the rest are recomputed by the run).
_COPIED = ("requested_url", "canonical_url", "final_url", "title", "source_type",
           "acquisition_method", "media_type", "retrieved_at", "source_date", "content_sha256",
           "byte_size", "page_count", "status", "error_kind", "error_message", "raw_object_key",
           "discovery_provider", "discovery_query", "duration_ms")

class ReplayAcquisition:
    """``city``: re-check each document's source type under the current rules
    (discovery/normalize.py::reclassify); changes are listed in ``reclassified``."""

    def __init__(self, documents: list[Mapping[str, Any]],
                 chunks: list[Mapping[str, Any]], city: str | None = None) -> None:
        self.city = city
        self.reclassified: list[dict[str, Any]] = []
        self.by_round: dict[int, list[Mapping[str, Any]]] = {}
        for d in documents:
            self.by_round.setdefault(int(d.get("round") or 1), []).append(d)
        self.chunks: dict[int, list[Mapping[str, Any]]] = {}
        for c in chunks:
            self.chunks.setdefault(c["document_id"], []).append(c)
        self.served: list[int] = []  # rounds served, in order (for tests and reports)

    @classmethod
    def from_run(cls, evidence: EvidenceRepository, run_id: str,
                 city: str | None = None) -> "ReplayAcquisition":
        return cls(evidence.documents(run_id), evidence.chunks(run_id, limit=1_000_000), city)

    @property
    def rounds(self) -> int:
        return max(self.by_round, default=0)

    async def acquire_round(self, candidates: list[Mapping[str, Any]],
                            previous: list[Mapping[str, Any]],
                            round_no: int = 1, priority_hosts: Any = ()) -> AcquisitionOutcome:
        """The parent's documents of ``round_no`` (candidates are ignored: nothing is
        discovered or fetched in a replay)."""
        self.served.append(round_no)
        outcome = AcquisitionOutcome()
        done = {r.get("canonical_url") for r in previous}
        for doc in self.by_round.get(round_no, []):
            if doc.get("canonical_url") in done:
                continue
            record = {k: doc.get(k) for k in _COPIED}
            if self.city is not None:
                current = reclassify(record, self.city)
                if current != record["source_type"]:
                    self.reclassified.append({"url": record.get("final_url")
                                              or record.get("requested_url"),
                                              "from": record["source_type"], "to": current})
                    record["source_type"] = current
            record["round"] = round_no
            record["chunks"] = 0
            if doc.get("status") == "OK":
                parts = sorted(self.chunks.get(doc["document_id"], []),
                               key=lambda c: (c.get("page_number") or 0, c.get("ordinal") or 0))
                for c in parts:
                    outcome.chunks.append(EvidenceChunk(
                        chunk_id=c["chunk_id"], url=c["url"], text=c["text"],
                        title=doc.get("title"), page_number=c.get("page_number"),
                        source_type=record["source_type"],
                        acquisition_method=doc.get("acquisition_method"),
                        retrieved_at=doc.get("retrieved_at")))
                record["chunks"] = len(parts)
            outcome.records.append(record)
        return outcome
