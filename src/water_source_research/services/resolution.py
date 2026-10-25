"""Deterministic, evidence-based resolution of normalized candidate claims.

Per field (and, for source-level fields, per water source) the engine groups equivalent claims,
orders differing values by reference year (older values are history, not conflicts), lets
legitimately multiple values coexist, and accepts a value automatically only under explicit,
conservative rules. Everything else becomes CONFLICT (incompatible values) or HITL_REQUIRED
(needs a reviewer). Confidence never decides acceptance.

Entities:
  "city"          the city (city-scope fields)
  <source key>    one water source (``water_source`` values; record-scope fields attributed to it)
  "*"             every SupplyRecord (one utility serving all sources)
  "unattributed"  a source-level claim that could not be attributed to one water source

Status transitions written here (reviewer decisions are applied elsewhere and preserved):
  UNRESOLVED/CANDIDATE -> RESOLVED (AUTO_STRICT) | HITL_REQUIRED | CONFLICT
  a field without valid claims never becomes RESOLVED.
"""

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from water_source_research.core.versions import RESOLUTION_POLICY_VERSION
from water_source_research.schemas.common import (
    FINAL_STATUSES, CheckResult, FieldResolutionStatus, ValidationOutcome, ValueStatus,
)
from water_source_research.schemas.research import (
    CityResearchState, EntityResolution, FieldClaim, FieldDefinition, FieldResolution,
    FieldState,
)
from .normalization import (
    COORDINATE_TOLERANCE, NUMERIC_TOLERANCE, OFFICIAL_SOURCE_TYPES, EvidenceIndex, close,
    sentence_around,
)

CITY = "city"
ALL_RECORDS = "*"
UNATTRIBUTED = "unattributed"
ENTITY_FIELD = "water_source"
SHARED_RECORD_FIELDS = frozenset({"utility_name"})  # one value usually serves every source

S = FieldResolutionStatus

@dataclass(frozen=True)
class ResolutionPolicy:
    numeric_tolerance: float = NUMERIC_TOLERANCE
    coordinate_tolerance: float = COORDINATE_TOLERANCE
    official_source_types: frozenset[str] = OFFICIAL_SOURCE_TYPES
    # Warnings that do not block automatic acceptance (everything else does).
    allowed_warnings: frozenset[str] = frozenset({"REFERENCE_YEAR"})
    version: str = RESOLUTION_POLICY_VERSION

@dataclass
class Group:
    value: Any
    year: int | None
    claims: list[FieldClaim] = field(default_factory=list)

@dataclass(frozen=True)
class Source:
    key: str
    label: str
    aliases: tuple[str, ...]

# --- keys -----------------------------------------------------------------------------------

def name_key(name: str) -> str:
    """Normalized key of a name (accents, case, punctuation and a leading 'the' ignored)."""
    text = unicodedata.normalize("NFKD", str(name))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()
    text = re.sub(r"[^\w\s]", " ", text)
    text = " ".join(text.split())
    return text.removeprefix("the ")

def _aliases(label: str) -> tuple[str, ...]:
    out = {name_key(label)}
    outer = re.sub(r"\([^)]*\)", " ", label)
    inner = re.findall(r"\(([^)]*)\)", label)
    for part in [outer, *inner]:
        key = name_key(part)
        if len(key) >= 5:
            out.add(key)
    return tuple(sorted(out, key=len, reverse=True))

# --- engine ---------------------------------------------------------------------------------

def resolve_research(research: CityResearchState, fields: Mapping[str, FieldDefinition],
                     index: EvidenceIndex | None = None,
                     policy: ResolutionPolicy | None = None,
                     ) -> tuple[CityResearchState, list[dict[str, Any]]]:
    """Resolve every field that has claims. Returns (new state, resolution log entries).

    Claims must be normalized first (services/normalization.py); unnormalized claims are
    treated as unverified and never accepted automatically."""
    policy = policy or ResolutionPolicy()
    out = research.model_copy(deep=True)
    log: list[dict[str, Any]] = []
    sources: list[Source] = []

    ws_def = fields.get(ENTITY_FIELD)
    ws = out.fields.get(ENTITY_FIELD)
    if ws_def is not None and ws is not None:
        if not _preserved(ws) and (ws.candidate_claims or _reviewer_entities(ws)):
            entities = _resolve_entities(ws, ws_def, policy)
            _apply(ws, ws_def, entities, policy, log)
        sources = _accepted_sources(ws)

    for key, d in fields.items():
        if key == ENTITY_FIELD or d.scope == "derived" or d.generated:
            continue
        fs = out.fields.get(key)
        if fs is None or _preserved(fs) or not (fs.candidate_claims or _reviewer_entities(fs)):
            continue
        if d.scope == "city" or d.scope == "time_series":
            entities = [e for e in [_decide(CITY, None, fs.candidate_claims, d, policy)] if e]
        else:
            entities = _resolve_record_field(fs, d, sources, index, policy)
        _apply(fs, d, entities, policy, log)
    return out, log

def _reviewer_entities(fs: FieldState) -> dict[str, EntityResolution]:
    if fs.resolution is None:
        return {}
    return {e.entity_key: e for e in fs.resolution.entities if e.decided_by == "REVIEWER"}

def _preserved(fs: FieldState) -> bool:
    """Final states set before rules existed, or by reviewers for every entity, are never
    recomputed (reviewer-decided entities of a partly reviewed field are kept by _apply)."""
    if fs.status not in FINAL_STATUSES:
        return False
    if fs.resolution is None:
        return True
    return bool(fs.resolution.entities) and all(
        e.decided_by == "REVIEWER" for e in fs.resolution.entities)

def _accepted_sources(ws: FieldState) -> list[Source]:
    """Water sources that SupplyRecords may be built from (accepted or still pending)."""
    if ws.resolution is not None:
        out = []
        for e in ws.resolution.entities:
            if e.status in (S.CONFLICT, S.NOT_APPLICABLE, S.UNRESOLVED, S.NEEDS_RESEARCH) \
                    or e.entity_key == UNATTRIBUTED:
                continue
            if not (set(e.selected_claim_uids) | set(e.candidate_claim_uids)):
                continue
            label = e.label or e.entity_key
            out.append(Source(e.entity_key, label, _aliases(label)))
        return out
    if ws.selected_claim is not None and ws.status in FINAL_STATUSES:  # reviewer-set
        label = str(ws.selected_claim.comparable_value)
        return [Source(name_key(label), label, _aliases(label))]
    return []

def _resolve_entities(ws: FieldState, d: FieldDefinition,
                      policy: ResolutionPolicy) -> list[EntityResolution]:
    """water_source: every distinct valid value is its own (legitimate) source entity."""
    valid = [c for c in ws.candidate_claims if _is_valid(c)]
    invalid = [c for c in ws.candidate_claims if not _is_valid(c)]
    buckets: dict[str, list[FieldClaim]] = {}
    for c in sorted(valid, key=lambda c: c.claim_uid or ""):
        buckets.setdefault(name_key(str(c.comparable_value)), []).append(c)
    labels = {key: str(claims[0].comparable_value) for key, claims in buckets.items()}
    entities: list[EntityResolution] = []
    for key in sorted(buckets):
        claims = buckets[key]
        group = Group(claims[0].comparable_value, None, claims)
        reasons = _strict_failures(group, d, policy)
        uids = [c.claim_uid or "" for c in claims]
        if reasons:
            entity = EntityResolution(entity_key=key, label=labels[key], status=S.HITL_REQUIRED,
                                      rule="STRICT_NOT_MET", reason_codes=reasons,
                                      candidate_claim_uids=uids)
        else:
            entity = EntityResolution(
                entity_key=key, label=labels[key], status=S.RESOLVED, rule="AUTO_STRICT",
                candidate_claim_uids=uids,
                selected_claim_uids=[_representative(group, d, policy).claim_uid or ""])
        if any(_possibly_same(labels[key], labels[o]) for o in buckets if o != key):
            entity = _to_review(entity, "POSSIBLE_EQUIVALENCE")
        entities.append(entity)
    unsettled = _decide(UNATTRIBUTED, None, invalid, d, policy) if invalid else None
    if unsettled is not None:  # None: every invalid claim was rejected by a reviewer
        entities.append(unsettled)
    for c in ws.candidate_claims:
        c.entity_key = name_key(str(c.comparable_value)) if _is_valid(c) else None
    return entities

def _resolve_record_field(fs: FieldState, d: FieldDefinition, sources: list[Source],
                          index: EvidenceIndex | None,
                          policy: ResolutionPolicy) -> list[EntityResolution]:
    claims = fs.candidate_claims
    if d.key in SHARED_RECORD_FIELDS and not any(c.assigned_entity_key for c in claims):
        valid_groups = _group([c for c in claims if _is_valid(c)], d, policy)
        if len(valid_groups) <= 1:  # one utility for every source
            for c in claims:
                c.entity_key = ALL_RECORDS
            entity = _decide(ALL_RECORDS, None, claims, d, policy)
            return [entity] if entity else []
    by_entity: dict[str, list[FieldClaim]] = {}
    reasons: dict[str, set[str]] = {}
    for c in claims:
        key, reason = _attribute(c, sources, index)
        c.entity_key = key
        by_entity.setdefault(key, []).append(c)
        if reason:
            reasons.setdefault(key, set()).add(reason)
    labels = {s.key: s.label for s in sources}
    entities = []
    for key in sorted(by_entity):
        entity = _decide(key, labels.get(key), by_entity[key], d, policy)
        if entity is None:
            continue
        for reason in sorted(reasons.get(key, ())):
            entity = _to_review(entity, reason)
        entities.append(entity)
    return entities

def _attribute(claim: FieldClaim, sources: list[Source],
               index: EvidenceIndex | None) -> tuple[str, str | None]:
    if claim.assigned_entity_key:  # a reviewer pinned it
        return claim.assigned_entity_key, None
    if not sources:
        return UNATTRIBUTED, "NO_WATER_SOURCE"
    if len(sources) == 1:
        return sources[0].key, None
    def named_in(text: str) -> set[str]:
        padded = f" {name_key(text)} "
        return {s.key for s in sources if any(f" {a} " in padded for a in s.aliases)}

    # The quote, then the sentence around it, then the whole chunk.
    hits = named_in(" ".join(e.passage for e in claim.evidence))
    for widen in (_sentences, _chunks):
        if hits or index is None:
            break
        hits = named_in(" ".join(widen(claim, index)))
    if len(hits) == 1:
        return hits.pop(), None
    return UNATTRIBUTED, "ATTRIBUTION_AMBIGUOUS" if hits else "ATTRIBUTION_UNKNOWN"

def _chunks(claim: FieldClaim, index: EvidenceIndex) -> list[str]:
    return [loc.chunk.text for loc in map(index.locate, claim.evidence) if loc.chunk]

def _sentences(claim: FieldClaim, index: EvidenceIndex) -> list[str]:
    out = []
    for ref in claim.evidence:
        located = index.locate(ref)
        if located.chunk is not None:
            out.append(sentence_around(located.chunk.text, ref.passage) or "")
    return out

# --- one entity -----------------------------------------------------------------------------

def _decide(entity_key: str, label: str | None, claims: list[FieldClaim], d: FieldDefinition,
            policy: ResolutionPolicy) -> EntityResolution | None:
    """None when a reviewer rejected every claim (the entity has nothing left to resolve)."""
    claims = [c for c in claims if c.review_status != "REJECTED"]
    if not claims:
        return None
    uids = [c.claim_uid or "" for c in claims]
    valid = [c for c in claims if _is_valid(c)]
    ignored = len(claims) - len(valid)
    base = {"entity_key": entity_key, "label": label, "candidate_claim_uids": uids}
    if not valid:
        fails = sorted({code for c in claims for code in _fail_codes(c)})
        return EntityResolution(**base, status=S.HITL_REQUIRED, rule="INVALID_ONLY",
                                reason_codes=fails or ["NOT_VALIDATED"])
    groups = _group(valid, d, policy)
    notes = [f"IGNORED_INVALID:{ignored}"] if ignored else []

    if d.multi_valued:  # several values coexist (e.g. reservoir names of one source)
        reasons = sorted({r for g in groups for r in _strict_failures(g, d, policy)})
        selected = [_representative(g, d, policy).claim_uid or "" for g in groups]
        if reasons:
            return EntityResolution(**base, status=S.HITL_REQUIRED, rule="STRICT_NOT_MET",
                                    reason_codes=reasons + notes)
        return EntityResolution(**base, status=S.RESOLVED, rule="AUTO_STRICT",
                                reason_codes=notes, selected_claim_uids=selected)

    current, historical = _temporal(groups)
    history_uids = [c.claim_uid or "" for g in historical for c in g.claims]
    if historical:
        notes.append("OLDER_VALUES_KEPT_AS_HISTORY")
    if len(current) > 1:
        values = [g.value for g in current]
        if d.descriptive:
            code, status = "MULTIPLE_DESCRIPTIONS", S.HITL_REQUIRED
        elif any(_possibly_same(a, b) for i, a in enumerate(values) for b in values[i + 1:]):
            code, status = "POSSIBLE_EQUIVALENCE", S.HITL_REQUIRED
        else:
            years = {g.year for g in current}
            code = "VALUES_DISAGREE" if len(years) == 1 and None not in years \
                else "VALUES_DISAGREE_UNDATED"
            status = S.CONFLICT
        return EntityResolution(**base, status=status, rule=code, reason_codes=[code] + notes,
                                historical_claim_uids=history_uids)

    group = current[0]
    reasons = _strict_failures(group, d, policy)
    if reasons:
        return EntityResolution(**base, status=S.HITL_REQUIRED, rule="STRICT_NOT_MET",
                                reason_codes=reasons + notes,
                                historical_claim_uids=history_uids)
    chosen = _representative(group, d, policy)
    return EntityResolution(**base, status=S.RESOLVED, rule="AUTO_STRICT", reason_codes=notes,
                            selected_claim_uids=[chosen.claim_uid or ""],
                            historical_claim_uids=history_uids)

def _to_review(entity: EntityResolution, reason: str) -> EntityResolution:
    if entity.status is S.CONFLICT:
        return entity.model_copy(update={"reason_codes": entity.reason_codes + [reason]})
    return entity.model_copy(update={
        "status": S.HITL_REQUIRED, "rule": reason, "selected_claim_uids": [],
        "reason_codes": [reason] + [r for r in entity.reason_codes if r != reason]})

def _temporal(groups: list[Group]) -> tuple[list[Group], list[Group]]:
    """(current groups, historical groups): differing values for different, known reference
    years are a time series, so only the most recent year is current."""
    if len(groups) <= 1 or any(g.year is None for g in groups):
        return groups, []
    latest = max(g.year for g in groups if g.year is not None)
    return ([g for g in groups if g.year == latest], [g for g in groups if g.year != latest])

def _strict_failures(group: Group, d: FieldDefinition, policy: ResolutionPolicy) -> list[str]:
    """Why this group can't be accepted automatically ([] = accept). One claim in the group
    must meet every rule on its own."""
    if d.review_always:
        return ["REVIEW_ALWAYS"]
    best: list[str] | None = None
    for c in group.claims:
        reasons = _claim_failures(c, d, policy)
        if not reasons:
            return []
        if best is None or len(reasons) < len(best):
            best = reasons
    return best or ["NOT_VALIDATED"]

def _claim_failures(c: FieldClaim, d: FieldDefinition, policy: ResolutionPolicy) -> list[str]:
    v = c.validation
    if v is None:
        return ["NOT_VALIDATED"]
    reasons: list[str] = []
    if c.value_status is ValueStatus.NOT_APPLICABLE:
        reasons.append("NOT_APPLICABLE_CLAIM")  # N/A is always a reviewer decision
    if v.outcome is ValidationOutcome.INVALID:
        reasons.append("INVALID")
    reasons += [f"WARN_{code}" for code in v.codes(CheckResult.WARN)
                if code not in policy.allowed_warnings]
    if c.value_status is ValueStatus.KNOWN \
            and v.result_of("VALUE_IN_QUOTE") is not CheckResult.PASS:
        reasons.append("VALUE_NOT_VERIFIED")
    if v.result_of("CITATION_LINKED") is not CheckResult.PASS:
        reasons.append("CITATION_NOT_LINKED")
    if not _official(c, d, policy):
        reasons.append("NO_OFFICIAL_SOURCE")
    return sorted(set(reasons))

def _official(c: FieldClaim, d: FieldDefinition, policy: ResolutionPolicy) -> bool:
    """Cited from a source type authoritative for this field (e.g. city population from a
    statistics agency, not from the utility)."""
    types = set(c.qualifiers.source_types) if c.qualifiers else set()
    return bool(types & (set(d.official_sources) or policy.official_source_types))

def _representative(group: Group, d: FieldDefinition, policy: ResolutionPolicy) -> FieldClaim:
    """Deterministic choice among equivalent claims: one meeting every rule, then official,
    then verified; ties by claim id (never by model confidence)."""
    def rank(c: FieldClaim):
        verified = c.validation is not None and \
            c.validation.result_of("VALUE_IN_QUOTE") is CheckResult.PASS
        return (bool(_claim_failures(c, d, policy)), not _official(c, d, policy),
                not verified, c.claim_uid or "")
    return min(group.claims, key=rank)

# --- grouping -------------------------------------------------------------------------------

def _is_valid(c: FieldClaim) -> bool:
    return c.review_status != "REJECTED" and c.validation is not None \
        and c.validation.outcome is not ValidationOutcome.INVALID

def _fail_codes(c: FieldClaim) -> list[str]:
    return c.validation.codes(CheckResult.FAIL) if c.validation else ["NOT_VALIDATED"]

def _group(claims: list[FieldClaim], d: FieldDefinition, policy: ResolutionPolicy) -> list[Group]:
    groups: list[Group] = []
    for c in sorted(claims, key=lambda c: c.claim_uid or ""):
        value = None if c.value_status is not ValueStatus.KNOWN else c.comparable_value
        year = c.qualifiers.reference_year if c.qualifiers else None
        for g in groups:
            if _equivalent(g.value, value, d, policy) and (
                    g.year is None or year is None or g.year == year):
                g.claims.append(c)
                if g.year is None:
                    g.year = year
                break
        else:
            groups.append(Group(value, year, [c]))
    return groups

def _equivalent(a: Any, b: Any, d: FieldDefinition, policy: ResolutionPolicy) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if d.type == "coordinate":
        return abs(float(a) - float(b)) <= policy.coordinate_tolerance
    if d.type in ("number", "percent"):
        return close(float(a), float(b), policy.numeric_tolerance)
    if d.type == "integer":
        return int(a) == int(b)
    return name_key(str(a)) == name_key(str(b))

def _possibly_same(a: Any, b: Any) -> bool:
    """Names that may denote the same thing (one's words contain the other's)."""
    if not isinstance(a, str) or not isinstance(b, str):
        return False
    ta, tb = set(name_key(a).split()), set(name_key(b).split())
    return bool(ta and tb) and ta != tb and (ta <= tb or tb <= ta)

# --- write back -----------------------------------------------------------------------------

def _apply(fs: FieldState, d: FieldDefinition, entities: list[EntityResolution],
           policy: ResolutionPolicy, log: list[dict[str, Any]]) -> None:
    previous = fs.status
    previous_entities = {e.entity_key: e.status for e in fs.resolution.entities} \
        if fs.resolution else {}
    reviewer = _reviewer_entities(fs)
    computed = [e for e in entities if e.entity_key not in reviewer]
    merged = sorted(computed + list(reviewer.values()), key=lambda e: e.entity_key)
    fs.resolution = FieldResolution(
        scope=d.scope or "record", policy_version=policy.version, entities=merged,
        decided_by="REVIEWER" if merged and not computed else "RULES")
    refresh_field(fs)
    for e in computed:  # reviewer decisions were logged when they were applied
        before = previous_entities.get(e.entity_key, previous)
        log.append({"field_key": fs.field_key, "entity_key": e.entity_key,
                    "from_status": before.value, "to_status": e.status.value,
                    "field_from_status": previous.value, "field_to_status": fs.status.value,
                    "rule": e.rule, "reason_codes": list(e.reason_codes),
                    "selected_claim_uids": list(e.selected_claim_uids),
                    "candidate_claim_uids": list(e.candidate_claim_uids),
                    "decided_by": "RULES", "policy_version": policy.version})

def refresh_field(fs: FieldState) -> None:
    """Field status and selected claim from its entity resolutions."""
    entities = fs.resolution.entities if fs.resolution else []
    fs.status = _aggregate(entities)
    fs.selected_claim = None
    if fs.status is S.RESOLVED and len(entities) == 1 \
            and len(entities[0].selected_claim_uids) == 1:
        fs.selected_claim = fs.claim(entities[0].selected_claim_uids[0])

def default_entity(d: FieldDefinition) -> str:
    """Entity of a field that has no candidate to attribute (e.g. nothing was found)."""
    return CITY if d.scope in ("city", "time_series") else ALL_RECORDS

def _aggregate(entities: list[EntityResolution]) -> FieldResolutionStatus:
    statuses = {e.status for e in entities}
    if not statuses:
        return S.UNRESOLVED
    for status in (S.CONFLICT, S.HITL_REQUIRED, S.UNRESOLVED, S.CANDIDATE):
        if status in statuses:
            return status
    if S.RESOLVED in statuses:
        return S.RESOLVED  # at least one value (other entities N/A or Unknown)
    if statuses == {S.NOT_APPLICABLE}:
        return S.NOT_APPLICABLE
    if S.NEEDS_RESEARCH in statuses:
        return S.NEEDS_RESEARCH
    return S.UNAVAILABLE
