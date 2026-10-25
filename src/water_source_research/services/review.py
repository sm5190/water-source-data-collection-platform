"""Human review of a city run: which (field, entity) items need a person, checking a reviewer's
decisions against what they were shown, and applying decisions to the research state.

Pure functions plus ``ReviewRepository`` (review_items / append-only review_decisions). The
graph's human_review node pauses on the open items (LangGraph interrupt) and, when the run is
resumed, applies the latest decision per item; the rules then re-resolve whatever a reviewer
sent back (rejections, source assignments). Reviewer decisions are never recomputed.

Rules enforced here:
  - nothing is accepted that wasn't shown: APPROVE/REJECT/ASSIGN_SOURCE name candidate claims
    of the item; claims that failed validation can't be approved (CORRECT them instead);
  - approving a claim that failed validation needs a deliberate override
    (``override_validation``), recorded in the audit trail;
  - a corrected value needs evidence: a verbatim quote from one of the run's evidence chunks,
    or a reviewer-supplied source (kept as REVIEWER_SUPPLIED, unverified);
  - reasons are optional (the stored ``decision_basis`` describes every decision);
  - rejecting the candidates leaves no value: with no usable candidate left the field is
    NEEDS_RESEARCH (never an implicit Unknown); the highest-confidence claim is never picked.
"""

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from sqlalchemy import Engine, select, update
from sqlalchemy.orm import Session
from water_source_research.core.versions import RESOLUTION_POLICY_VERSION
from water_source_research.db.models import utcnow
from water_source_research.db.review_models import ReviewDecisionRecord, ReviewItemRecord
from water_source_research.llm.extraction import normalize_quote
from water_source_research.schemas.common import (
    FINAL_STATUSES, FieldResolutionStatus, ValidationOutcome, ValueStatus,
)
from water_source_research.schemas.research import (
    CityResearchState, EntityResolution, EvidenceRef, FieldClaim, FieldDefinition,
    FieldResolution, FieldState,
)
from water_source_research.schemas.review import DecisionType, ReviewDecisionIn, reviewer_key
from .normalization import EvidenceIndex, claim_uid, coerce_value, normalize_claim
from .resolution import (
    ALL_RECORDS, ENTITY_FIELD, UNATTRIBUTED, default_entity, refresh_field,
)

S = FieldResolutionStatus
D = DecisionType
REVIEWABLE = (S.CONFLICT, S.HITL_REQUIRED, S.UNRESOLVED, S.CANDIDATE)
REVIEWER_SUPPLIED = "REVIEWER_SUPPLIED"

@dataclass
class ReviewItemSpec:
    item_id: str
    round: int
    field_key: str
    entity_key: str
    label: str | None
    reason_codes: list[str]
    candidate_claim_uids: list[str]
    payload: dict[str, Any] = field(default_factory=dict)

OFFICIAL_DEFAULT = frozenset({"UTILITY", "GOVERNMENT", "REGULATORY"})

def proposal_key(outcome: str | None, source_types: set[str], value_in_quote: str | None,
                 official: set[str] | frozenset[str], order: int) -> tuple[int, int, int, int]:
    """Sort key for the value the system proposes among a field's candidates: passes
    validation, then an official source type, then the value re-found in its quote, then the
    order the claims were found. Confidence is never used."""
    return ({"VALID": 0, "VALID_WITH_WARNINGS": 1}.get(outcome or "", 2),
            0 if source_types & set(official or OFFICIAL_DEFAULT) else 1,
            0 if value_in_quote == "PASS" else 1, order)

def item_id(run_id: str, round_no: int, field_key: str, entity_key: str) -> str:
    raw = "\x1f".join([run_id, str(round_no), field_key, entity_key])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]

# --- what needs review ------------------------------------------------------------------------

def open_review_items(run_id: str, round_no: int, research: CityResearchState,
                      fields: Mapping[str, FieldDefinition], researched: list[str] | None,
                      needs_evidence: list[str] | None,
                      index: EvidenceIndex | None = None) -> list[ReviewItemSpec]:
    """One item per (field, entity) the rules left for a person, for the fields this run
    researched. Final fields (resolved, N/A, Unknown) and reviewer-decided entities are done."""
    missing = set(needs_evidence or [])
    sources = known_sources(research)
    specs: list[ReviewItemSpec] = []
    for key, d in fields.items():
        if d.derived or d.generated or (researched is not None and key not in researched):
            continue
        fs = research.fields.get(key)
        if fs is None or fs.status in FINAL_STATUSES:
            continue
        pending = [e for e in (fs.resolution.entities if fs.resolution else [])
                   if e.decided_by == "RULES" and e.status in REVIEWABLE]
        if pending:
            for e in pending:
                specs.append(_spec(run_id, round_no, fs, d, e.entity_key, e.label,
                                   e.reason_codes, e.candidate_claim_uids, sources, index))
        elif not fs.resolution or not fs.resolution.entities:
            reason = "NO_EVIDENCE" if key in missing or not fs.candidate_claims \
                else "ALL_CLAIMS_REJECTED"
            if reason == "NO_EVIDENCE" and d.gap_filled_by:
                continue  # the catalog / derivations fill it at assemble
            specs.append(_spec(run_id, round_no, fs, d, default_entity(d), None, [reason], [],
                               sources, index))
    return specs

def known_sources(research: CityResearchState) -> list[dict[str, str]]:
    ws = research.fields.get(ENTITY_FIELD)
    if ws is None or ws.resolution is None:
        return []
    return [{"key": e.entity_key, "label": e.label or e.entity_key}
            for e in ws.resolution.entities
            if e.entity_key not in (UNATTRIBUTED, ALL_RECORDS)
            and e.status not in (S.NOT_APPLICABLE, S.UNRESOLVED)]

def _spec(run_id: str, round_no: int, fs: FieldState, d: FieldDefinition, entity_key: str,
          label: str | None, reasons: list[str], uids: list[str],
          sources: list[dict[str, str]], index: EvidenceIndex | None) -> ReviewItemSpec:
    claims = [c for c in (fs.claim(u) for u in uids) if c is not None]
    allowed = [D.CORRECT, D.MARK_UNKNOWN, D.MARK_NA, D.DEFER]
    if claims:
        allowed = [D.APPROVE, D.REJECT] + allowed
        if d.scope == "record" and sources and d.key != ENTITY_FIELD:
            allowed.append(D.ASSIGN_SOURCE)
    payload = {
        "field": {"key": d.key, "type": d.type, "allowed": d.allowed, "scope": d.scope,
                  "unit": d.unit, "multi_valued": d.multi_valued,
                  "review_always": d.review_always, "field_type": d.field_type},
        "entity": {"key": entity_key, "label": label},
        "field_status": fs.status.value,
        "reason_codes": list(reasons),
        "candidates": [claim_view(c, index) for c in claims],
        "sources": sources,
        "allowed_decisions": [a.value for a in allowed],
    }
    return ReviewItemSpec(item_id(run_id, round_no, d.key, entity_key), round_no, d.key,
                          entity_key, label, list(reasons), [c.claim_uid or "" for c in claims],
                          payload)

def claim_view(c: FieldClaim, index: EvidenceIndex | None = None) -> dict[str, Any]:
    """A claim as a reviewer sees it: value, validation and citations with their chunks."""
    citations = []
    for ref in c.evidence:
        located = index.locate(ref) if index is not None else None
        chunk = located.chunk if located else None
        citations.append({
            "url": ref.url, "title": ref.title, "page_number": ref.page_number,
            "quote": ref.passage, "retrieved_at": ref.retrieved_at,
            "chunk_id": chunk.chunk_id if chunk else None,
            "source_type": index.source_type(ref, located) if index is not None else None,
            "source_date": (located.document or {}).get("source_date") if located else None,
        })
    v = c.validation
    return {"claim_uid": c.claim_uid, "value": c.value, "normalized_value": c.normalized_value,
            "value_status": c.value_status.value, "value_origin": c.value_origin.value,
            "confidence": c.confidence, "entity_key": c.entity_key,
            "review_status": c.review_status,
            "validation": {"outcome": v.outcome.value,
                           "checks": [k.model_dump(mode="json") for k in v.checks]} if v else None,
            "qualifiers": c.qualifiers.model_dump(mode="json") if c.qualifiers else None,
            "citations": citations}

# --- checking a decision ----------------------------------------------------------------------

ChunkLookup = Callable[[str], Mapping[str, Any] | None]

def validate_decision(dec: ReviewDecisionIn, item: Mapping[str, Any], d: FieldDefinition,
                      chunk: ChunkLookup) -> list[str]:
    """Problems that make ``dec`` unacceptable for ``item`` (empty list = valid)."""
    payload = item["payload"]
    problems: list[str] = []
    if dec.decision.value not in payload.get("allowed_decisions", []):
        return [f"{dec.decision.value} is not allowed for this item"]
    candidates = {c["claim_uid"]: c for c in payload.get("candidates", [])}
    unknown = [u for u in [*dec.claim_uids, *dec.rejected_claim_uids] if u not in candidates]
    if unknown:
        problems.append(f"claims not shown in this item: {', '.join(unknown)}")
    if dec.rejected_claim_uids and dec.decision is not D.APPROVE:
        problems.append("rejected_claim_uids go with APPROVE of another claim (use REJECT)")
    if set(dec.rejected_claim_uids) & set(dec.claim_uids):
        problems.append("a claim can't be both approved and rejected")
    if dec.override_validation and dec.decision is not D.APPROVE:
        problems.append("override_validation only applies to APPROVE")

    if dec.decision is D.APPROVE:
        if not dec.claim_uids:
            problems.append("APPROVE names the claim(s) to accept")
        elif len(dec.claim_uids) > 1 and not d.multi_valued:
            problems.append(f"{d.key} takes one value: approve exactly one claim")
        invalid = [uid for uid in dec.claim_uids if _invalid(candidates.get(uid))]
        for uid in invalid:
            if not dec.override_validation:
                v = candidates[uid]["validation"]
                failed = [k["code"] for k in v.get("checks", []) if k["result"] == "FAIL"]
                problems.append(f"claim {uid} failed validation ({', '.join(failed)}); "
                                "confirm it deliberately (override_validation) or use CORRECT "
                                "with supporting evidence")
        if dec.override_validation and not invalid:
            problems.append("override_validation is only for claims that failed validation")
        sources = payload.get("sources") or []
        if item["entity_key"] == UNATTRIBUTED and len(sources) > 1:
            problems.append("attribute the claim to a water source (ASSIGN_SOURCE) first")
    elif dec.decision is D.REJECT:
        if not dec.claim_uids:
            problems.append("REJECT names the claim(s) to reject")
    elif dec.decision is D.ASSIGN_SOURCE:
        if len(dec.claim_uids) != 1:
            problems.append("ASSIGN_SOURCE names exactly one claim")
        if dec.entity_key not in {s["key"] for s in payload.get("sources") or []}:
            problems.append("ASSIGN_SOURCE needs entity_key = one of the item's sources")
    elif dec.decision is D.CORRECT:
        if dec.value is None:
            problems.append("CORRECT needs a value (use MARK_NA or MARK_UNKNOWN otherwise)")
        else:
            _, value_problems = coerce_value(dec.value, d)
            problems += [f"value: {p}" for p in value_problems]
        if dec.evidence is None:
            problems.append("CORRECT needs supporting evidence (a chunk quote or a source)")
        elif dec.evidence.chunk_id:
            row = chunk(dec.evidence.chunk_id)
            if row is None:
                problems.append(f"evidence chunk {dec.evidence.chunk_id} is not in this run")
            elif normalize_quote(dec.evidence.quote or "") not in normalize_quote(row["text"]):
                problems.append("the quote does not occur in the cited chunk")
    return problems

def _invalid(candidate: Mapping[str, Any] | None) -> bool:
    v = (candidate or {}).get("validation") or {}
    return v.get("outcome") == ValidationOutcome.INVALID.value

def decision_basis(dec: ReviewDecisionIn) -> str:
    """A plain description of what the reviewer decided (stored with every decision, so a
    reviewer never has to type an explanation)."""
    kind = dec.decision
    if kind is D.APPROVE:
        text = "Accepted another value found (rejected the proposed value)" \
            if dec.rejected_claim_uids else "Accepted the proposed value"
        if dec.override_validation:
            text += "; reviewer confirmed it despite a failed validation check"
        return text
    if kind is D.CORRECT:
        evidence = dec.evidence
        source = "a quote from the run's evidence" if evidence and evidence.chunk_id \
            else "a reviewer-supplied source (not machine-verified)"
        return f"Corrected the value, supported by {source}"
    return {D.REJECT: "Rejected the proposed value",
            D.ASSIGN_SOURCE: "Assigned the value to a water source",
            D.MARK_UNKNOWN: "Marked Unknown (applies, no reliable value)",
            D.MARK_NA: "Marked Not applicable",
            D.DEFER: "Deferred (finalized as Unknown for now)"}[kind]

# --- applying decisions -----------------------------------------------------------------------

@dataclass
class Applied:
    research: CityResearchState
    log: list[dict[str, Any]]  # resolution log entries (decided_by REVIEWER)
    hitl: list[dict[str, Any]]  # one per decision: field, result, decision, attrs
    accepted: list[tuple[str, FieldClaim]]  # (field, claim) whose evidence was accepted

HITL_RESULT = {D.APPROVE: "APPROVED", D.CORRECT: "CORRECTED", D.REJECT: "REJECTED"}

def apply_decisions(research: CityResearchState, fields: Mapping[str, FieldDefinition],
                    items: list[Mapping[str, Any]], decisions: list[Mapping[str, Any]],
                    index: EvidenceIndex, chunk: ChunkLookup) -> Applied:
    """Apply the latest decision per item (``decisions`` in submission order)."""
    out = research.model_copy(deep=True)
    by_item = {i["id"]: i for i in items}
    latest: dict[str, Mapping[str, Any]] = {}
    for dec in decisions:
        if dec["item_id"] in by_item:
            latest[dec["item_id"]] = dec
    applied = Applied(out, [], [], [])
    for item_key, dec in latest.items():
        item = by_item[item_key]
        _apply_one(out, fields[item["field_key"]], item, dec, index, chunk, applied)
    return applied

def _apply_one(research: CityResearchState, d: FieldDefinition, item: Mapping[str, Any],
               dec: Mapping[str, Any], index: EvidenceIndex, chunk: ChunkLookup,
               applied: Applied) -> None:
    fs = research.fields[d.key]
    kind = D(dec["decision"])
    entity = item["entity_key"]
    if entity == UNATTRIBUTED and kind in (D.APPROVE, D.CORRECT, D.MARK_NA, D.MARK_UNKNOWN,
                                            D.DEFER):
        entity = ALL_RECORDS  # no (or a single) water source: the value serves every record
    before = _entity_status(fs, item["entity_key"])
    uids = list(dec.get("claim_uids") or [])
    resolution: EntityResolution | None = None
    common = {"entity_key": entity, "label": item.get("label"), "decided_by": "REVIEWER",
              "review_decision_id": dec.get("id"), "note": dec.get("reason"),
              "candidate_claim_uids": list(item.get("candidate_claim_uids") or [])}

    if kind is D.APPROVE:
        _reject(fs, dec.get("rejected_claim_uids") or [])
        for uid in uids:
            c = fs.claim(uid)
            if c is not None and d.scope == "record":
                c.entity_key = entity
            if c is not None:
                applied.accepted.append((d.key, c))
        rule = "REVIEW_APPROVE_OVERRIDE" if dec.get("validation_override") else "REVIEW_APPROVE"
        resolution = EntityResolution(**common, status=S.RESOLVED, rule=rule,
                                      selected_claim_uids=uids)
    elif kind is D.CORRECT:
        corrected = _corrected_claim(d, dec, uids, entity, index, chunk)
        if fs.claim(corrected.claim_uid or "") is None:
            fs.candidate_claims.append(corrected)
        applied.accepted.append((d.key, corrected))
        resolution = EntityResolution(**common, status=S.RESOLVED, rule="REVIEW_CORRECT",
                                      selected_claim_uids=[corrected.claim_uid or ""])
    elif kind is D.REJECT:
        _reject(fs, uids)
        usable = [c for c in (fs.claim(u) for u in item.get("candidate_claim_uids") or [])
                  if c is not None and c.review_status != "REJECTED"
                  and not (c.validation and c.validation.outcome is ValidationOutcome.INVALID)]
        if not usable:
            # Nothing left the rules could accept: the field needs further research. Not an
            # Unknown (no one decided that), and no new review or research round.
            if entity == UNATTRIBUTED:
                entity = ALL_RECORDS
                common["entity_key"] = entity
            resolution = EntityResolution(**common, status=S.NEEDS_RESEARCH,
                                          rule="REVIEW_REJECT")
    elif kind is D.ASSIGN_SOURCE:
        c = fs.claim(uids[0]) if uids else None
        if c is not None:
            c.assigned_entity_key = dec.get("target_entity_key")
    elif kind is D.MARK_NA:
        resolution = EntityResolution(**common, status=S.NOT_APPLICABLE, rule="REVIEW_MARK_NA")
    else:  # MARK_UNKNOWN / DEFER
        resolution = EntityResolution(**common, status=S.UNAVAILABLE,
                                      rule=f"REVIEW_{kind.value}")

    if fs.resolution is None:
        fs.resolution = FieldResolution(scope=d.scope or "record",
                                        policy_version=RESOLUTION_POLICY_VERSION)
    # The decided entity replaces what the rules had for this item (and for a value now
    # serving every record, the unattributed placeholder).
    replaced = {item["entity_key"], entity}
    fs.resolution.entities = [e for e in fs.resolution.entities if e.entity_key not in replaced]
    if resolution is not None:
        fs.resolution.entities.append(resolution)
        fs.resolution.entities.sort(key=lambda e: e.entity_key)
    if fs.resolution.entities and all(e.decided_by == "REVIEWER"
                                      for e in fs.resolution.entities):
        fs.resolution.decided_by = "REVIEWER"
    refresh_field(fs)

    after = resolution.status.value if resolution else "SENT_BACK_TO_RULES"
    applied.log.append({
        "field_key": d.key, "entity_key": entity, "from_status": before,
        "to_status": after, "field_from_status": before, "field_to_status": fs.status.value,
        "rule": resolution.rule if resolution else f"REVIEW_{kind.value}",
        "reason_codes": [], "selected_claim_uids":
        list(resolution.selected_claim_uids) if resolution else [],
        "candidate_claim_uids": list(item.get("candidate_claim_uids") or []),
        "decided_by": "REVIEWER", "policy_version": RESOLUTION_POLICY_VERSION,
        "review_decision_id": dec.get("id")})
    applied.hitl.append({"field_key": d.key, "decision": kind.value,
                         "result": HITL_RESULT.get(kind, "SKIPPED"), "entity_key": entity,
                         "decision_id": dec.get("id"), "to_status": after,
                         "field_to_status": fs.status.value,
                         "validation_override": bool(dec.get("validation_override")),
                         "correction_category": dec.get("correction_category")})

def _reject(fs: FieldState, uids: list[str]) -> None:
    for uid in uids:
        c = fs.claim(uid)
        if c is not None:
            c.review_status = "REJECTED"

def _entity_status(fs: FieldState, entity_key: str) -> str:
    for e in fs.resolution.entities if fs.resolution else []:
        if e.entity_key == entity_key:
            return e.status.value
    return fs.status.value

def _corrected_claim(d: FieldDefinition, dec: Mapping[str, Any], uids: list[str], entity: str,
                     index: EvidenceIndex, chunk: ChunkLookup) -> FieldClaim:
    value, _ = coerce_value(dec["reviewed_value"], d)
    evidence = dec.get("evidence") or {}
    supplied = not evidence.get("chunk_id")
    if supplied:
        ref = EvidenceRef(url=evidence["url"], title=evidence.get("title"),
                          passage=evidence.get("note") or dec.get("reason") or "")
    else:
        row = chunk(evidence["chunk_id"]) or {}
        ref = EvidenceRef(url=row.get("url", ""), page_number=row.get("page_number"),
                          title=row.get("title"), passage=evidence.get("quote") or "")
    claim = FieldClaim(field_key=d.key, value=value, value_status=ValueStatus.KNOWN,
                       value_origin=dec.get("value_origin") or "REPORTED", confidence=1.0,
                       evidence=[ref], review_status="CORRECTED",
                       corrected_from=uids[0] if uids else None,
                       entity_key=entity if d.scope == "record" else None)
    claim = claim.model_copy(update={"claim_uid": claim_uid(claim)})
    # A reviewer-supplied source can't be found in the run's evidence: the claim is accepted
    # on the reviewer's authority and stays marked as such (CITATION_LINKED fails).
    normalized = normalize_claim(claim, d, index, CityResearchState(city_name=""),
                                 datetime.now(UTC).year)
    if supplied and normalized.qualifiers is not None:
        normalized.qualifiers.source_types = [REVIEWER_SUPPLIED]
    return normalized.model_copy(update={"entity_key": claim.entity_key})

# --- legacy decisions (state["review_decisions"]: field-level APPROVED/CORRECTED/REJECTED) ----

def legacy_decisions(research: CityResearchState, fields: Mapping[str, FieldDefinition],
                     raw: list[Mapping[str, Any]], index: EvidenceIndex
                     ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Field-level decisions passed in the graph input (tests, scripts) as (items, decisions)
    for ``apply_decisions``. APPROVED accepts the rules' selection or the best valid claim."""
    items: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    for n, raw_dec in enumerate(raw):
        key = raw_dec["field_key"]
        fs = research.fields.get(key)
        d = fields.get(key)
        if fs is None or d is None:
            continue
        valid = [c for c in fs.candidate_claims if c.review_status != "REJECTED" and not (
            c.validation and c.validation.outcome is ValidationOutcome.INVALID)]
        chosen = fs.selected_claim or (sorted(valid, key=lambda c: -c.confidence) or [None])[0]
        entity = (chosen.entity_key if chosen and chosen.entity_key else None) \
            or default_entity(d)
        item = {"id": f"legacy-{n}", "field_key": key, "entity_key": entity, "label": None,
                "candidate_claim_uids": [c.claim_uid for c in fs.candidate_claims]}
        dec: dict[str, Any] = {"id": None, "item_id": item["id"],
                               "correction_category": raw_dec.get("correction_category"),
                               "reason": raw_dec.get("reason")}
        verdict = raw_dec["decision"]
        if verdict == "REJECTED":
            dec.update(decision=D.REJECT.value,
                       claim_uids=[c.claim_uid for c in fs.candidate_claims])
        elif chosen is None or not chosen.evidence:
            continue  # nothing evidence-backed to accept
        elif verdict == "CORRECTED" and "value" in raw_dec:
            ref = chosen.evidence[0]
            located = index.locate(ref)
            evidence = {"chunk_id": located.chunk.chunk_id, "quote": ref.passage} \
                if located.chunk else {"url": ref.url, "title": ref.title}
            dec.update(decision=D.CORRECT.value, claim_uids=[chosen.claim_uid],
                       reviewed_value=raw_dec["value"], evidence=evidence,
                       value_origin=chosen.value_origin.value)
        else:
            dec.update(decision=D.APPROVE.value, claim_uids=[chosen.claim_uid])
        items.append(item)
        decisions.append(dec)
    return items, decisions

# --- persistence ------------------------------------------------------------------------------

class DecisionConflict(Exception):
    """A decision_key was reused for a different decision."""

class ReviewRepository:
    """review_items (one per round/field/entity) and append-only review_decisions."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def save_items(self, run_id: str, specs: list[ReviewItemSpec]) -> int:
        """Insert-or-skip (a node re-executed on resume saves the same items again)."""
        with Session(self.engine) as session, session.begin():
            existing = set(session.scalars(select(ReviewItemRecord.id).where(
                ReviewItemRecord.id.in_([s.item_id for s in specs]))))
            new = [s for s in specs if s.item_id not in existing]
            session.add_all(ReviewItemRecord(
                id=s.item_id, run_id=run_id, round=s.round, field_key=s.field_key,
                entity_key=s.entity_key[:255], label=s.label and s.label[:255],
                reason_codes=s.reason_codes, candidate_claim_uids=s.candidate_claim_uids,
                payload=s.payload, status="OPEN") for s in new)
        return len(new)

    def items(self, run_id: str, round_no: int | None = None,
              status: str | None = None) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            q = select(ReviewItemRecord).where(ReviewItemRecord.run_id == run_id)
            if round_no is not None:
                q = q.where(ReviewItemRecord.round == round_no)
            if status is not None:
                q = q.where(ReviewItemRecord.status == status)
            rows = session.scalars(q.order_by(ReviewItemRecord.round, ReviewItemRecord.field_key,
                                              ReviewItemRecord.entity_key)).all()
            return [_item_dict(r) for r in rows]

    def item(self, run_id: str, item_key: str) -> dict[str, Any] | None:
        with Session(self.engine) as session:
            row = session.get(ReviewItemRecord, item_key)
            return _item_dict(row) if row is not None and row.run_id == run_id else None

    def add_decision(self, run_id: str, reviewer: str, dec: ReviewDecisionIn,
                     item: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
        """Append one decision. Returns (decision, created); a repeated ``decision_key`` with
        the same content returns the stored one, with different content raises."""
        with Session(self.engine) as session, session.begin():
            existing = session.scalar(select(ReviewDecisionRecord).where(
                ReviewDecisionRecord.decision_key == dec.decision_key))
            if existing is not None:
                if existing.item_id != dec.item_id or existing.decision != dec.decision.value \
                        or existing.claim_uids != dec.claim_uids \
                        or existing.reviewed_value != dec.value:
                    raise DecisionConflict(f"decision_key {dec.decision_key} was already used "
                                           "for a different decision")
                return _decision_dict(existing), False
            previous = session.scalar(select(ReviewDecisionRecord.id).where(
                ReviewDecisionRecord.item_id == dec.item_id)
                .order_by(ReviewDecisionRecord.id.desc()).limit(1))
            candidates = {c["claim_uid"]: c for c in item["payload"].get("candidates", [])}
            original = [candidates[u]["value"] for u in dec.claim_uids if u in candidates]
            row = ReviewDecisionRecord(
                decision_key=dec.decision_key, run_id=run_id, item_id=dec.item_id,
                round=dec.round, field_key=item["field_key"], entity_key=item["entity_key"],
                reviewer=reviewer, reviewer_key=reviewer_key(reviewer),
                decision=dec.decision.value, claim_uids=list(dec.claim_uids),
                rejected_claim_uids=list(dec.rejected_claim_uids) or None,
                validation_override=dec.override_validation,
                decision_basis=decision_basis(dec),
                original_value=original[0] if len(original) == 1 else (original or None),
                reviewed_value=dec.value,
                value_origin=dec.value_origin.value if dec.decision is D.CORRECT else None,
                reason=dec.reason, correction_category=dec.correction_category,
                evidence=dec.evidence.model_dump(mode="json") if dec.evidence else None,
                target_entity_key=dec.entity_key if dec.decision is D.ASSIGN_SOURCE else None,
                status_before=item["payload"].get("field_status", ""),
                supersedes_id=previous)
            session.add(row)
            session.flush()
            return _decision_dict(row), True

    def decisions(self, run_id: str, round_no: int | None = None) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            q = select(ReviewDecisionRecord).where(ReviewDecisionRecord.run_id == run_id)
            if round_no is not None:
                q = q.where(ReviewDecisionRecord.round == round_no)
            return [_decision_dict(r) for r in session.scalars(q.order_by(ReviewDecisionRecord.id))]

    def record_outcomes(self, outcomes: Mapping[int, str]) -> None:
        """The field status each applied decision led to (written once; decisions are
        otherwise never changed)."""
        if not outcomes:
            return
        with Session(self.engine) as session, session.begin():
            for decision_id, status in outcomes.items():
                session.execute(update(ReviewDecisionRecord).where(
                    ReviewDecisionRecord.id == decision_id,
                    ReviewDecisionRecord.status_after.is_(None)).values(status_after=status))

    def mark_applied(self, run_id: str, round_no: int) -> None:
        with Session(self.engine) as session, session.begin():
            session.execute(update(ReviewItemRecord).where(
                ReviewItemRecord.run_id == run_id, ReviewItemRecord.round == round_no,
                ReviewItemRecord.status == "OPEN").values(status="APPLIED",
                                                          applied_at=utcnow()))

def _item_dict(r: ReviewItemRecord) -> dict[str, Any]:
    return {"id": r.id, "run_id": r.run_id, "round": r.round, "field_key": r.field_key,
            "entity_key": r.entity_key, "label": r.label, "reason_codes": r.reason_codes,
            "candidate_claim_uids": r.candidate_claim_uids, "payload": r.payload,
            "status": r.status, "created_at": _iso(r.created_at),
            "applied_at": _iso(r.applied_at)}

def _decision_dict(r: ReviewDecisionRecord) -> dict[str, Any]:
    return {"id": r.id, "decision_key": r.decision_key, "run_id": r.run_id,
            "item_id": r.item_id, "round": r.round, "field_key": r.field_key,
            "entity_key": r.entity_key, "reviewer": r.reviewer, "decision": r.decision,
            "claim_uids": r.claim_uids, "original_value": r.original_value,
            "reviewed_value": r.reviewed_value, "value_origin": r.value_origin,
            "reviewer_key": r.reviewer_key, "decision_basis": r.decision_basis,
            "rejected_claim_uids": r.rejected_claim_uids or [],
            "validation_override": bool(r.validation_override),
            "status_after": r.status_after,
            "reason": r.reason, "correction_category": r.correction_category,
            "evidence": r.evidence, "target_entity_key": r.target_entity_key,
            "status_before": r.status_before, "supersedes_id": r.supersedes_id,
            "created_at": _iso(r.created_at)}

def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).isoformat()
