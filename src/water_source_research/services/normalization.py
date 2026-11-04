"""Deterministic normalization and validation of extracted candidate claims.

Every claim gets a stable ``claim_uid``, a normalized value, qualifiers (reference year,
reported value/unit, measurement basis, source types) and a ``ClaimValidation`` listing each
check. Nothing is dropped: an INVALID claim stays a visible candidate that resolution never
selects. No model is called; the cited quotes and the run's evidence chunks are the only input.

Checks (codes):
  FIELD_KNOWN, VALUE_STATUS, TYPE, ENUM, RANGE, EVIDENCE_PRESENT, CITATION_LINKED,
  VALUE_IN_QUOTE, MEASUREMENT_BASIS, REFERENCE_YEAR, DEPENDENCY, FORMAT, RESERVOIR_AS_SOURCE,
  DERIVED_FIELD, GENERATED_FIELD, METHOD_NOT_REPORTABLE, SOURCE_TIER, APPROXIMATE_VALUE,
  GEOGRAPHIC_SCOPE
plus the semantic checks of services/semantics.py (does the evidence support what the field
asks for: city, jurisdiction level, measurement type, measurement year, freshness, a year tied
to its measurement, designated dataset, a utility shown to serve the city).
"""

import hashlib
import json
import math
import re
from urllib.parse import urlsplit
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any
from water_source_research.core.versions import NORMALIZATION_VERSION
from water_source_research.llm.extraction import normalize_quote
from water_source_research.schemas.common import (
    CheckResult, ValidationOutcome, ValueStatus,
)
from water_source_research.schemas.research import (
    CityResearchState, ClaimQualifiers, ClaimValidation, EvidenceRef, FieldClaim,
    FieldDefinition, ValidationCheck,
)
from .rules_engine import days_in_year
from .semantics import page_text, semantic_checks
from .structure import date_line_years, structural_year

OFFICIAL_SOURCE_TYPES = frozenset({"UTILITY", "GOVERNMENT", "REGULATORY"})
NUMERIC_TOLERANCE = 0.01  # relative; value-in-quote and equivalence of numbers
COORDINATE_TOLERANCE = 0.001  # degrees
MIN_YEAR = 1900

# Proper-name fields: Title Case when the value arrives all upper/lower case.
NAME_FIELDS = frozenset({"city_name", "jurisdiction", "country", "utility_name", "water_source",
                         "water_transfer_system", "reservoir_names"})
LIST_FIELDS = frozenset({"infrastructure_notes"})  # lowercase, comma-separated
WITHDRAWAL_FIELDS = frozenset({"total_city_water_withdrawals_m3_day",
                               "source_withdrawal_contribution_m3_day"})
WATER_TYPE_CUES = {
    "Surface": ("surface", "river", "lake", "reservoir", "stream", "creek"),
    "Groundwater": ("groundwater", "ground water", "aquifer", "well", "borehole", "spring"),
    "Reclaimed": ("reclaimed", "recycled", "reuse", "wastewater", "purified"),
    "Desalination": ("desalinat", "seawater", "sea water", "brackish", "reverse osmosis"),
}

# --- evidence index -------------------------------------------------------------------------

@dataclass(frozen=True)
class IndexedChunk:
    chunk_id: str
    url: str
    page_number: int | None
    text: str
    norm_text: str
    source_type: str | None

def exact_span(text: str, quote: str) -> tuple[int, int] | None:
    """(start, end) of the first verbatim occurrence of ``quote`` in ``text``, else None (an
    elided, re-spaced or re-cased quote has no exact span: none is estimated)."""
    q = quote or ""
    if not q or "…" in q or "..." in q:
        return None
    i = text.find(q)
    return (i, i + len(q)) if i >= 0 else None

def ground_ref(ref: EvidenceRef, loc: "Located") -> None:
    """Record where a located citation is: its chunk, and its span when exact."""
    if loc.chunk is None:
        return
    ref.chunk_id = loc.chunk.chunk_id
    span = exact_span(loc.chunk.text, ref.passage)
    ref.char_start, ref.char_end = span if span else (None, None)

def ground_relation(rel: dict, index: "EvidenceIndex") -> None:
    """A claim relation's quote located on any page of its document: chunk id, page and, when
    exact, span (keys left out when it isn't found)."""
    url, quote = rel.get("url"), rel.get("quote")
    if not url or not quote or rel.get("chunk_id"):
        return
    segments = quote_segments(quote)
    for (u, page), chunks in index.by_key.items():
        if u != url:
            continue
        for c in chunks:
            if segments and _contains_in_order(c.norm_text, segments):
                rel["chunk_id"], rel["page_number"] = c.chunk_id, page
                span = exact_span(c.text, quote)
                if span:
                    rel["char_start"], rel["char_end"] = span
                return

@dataclass
class Located:
    chunk: IndexedChunk | None  # chunk whose text contains the quote (or where it starts)
    url_known: bool  # the run has evidence chunks for this URL
    document: Mapping[str, Any] | None
    spans_chunks: bool = False  # the quote runs across adjacent chunks of the same page

_ELLIPSIS = re.compile(r"\s*(?:…|\.\.\.)\s*")

def quote_segments(quote: str) -> list[str]:
    """A quote's normalized parts (a quote elided with "…" or "..." has several)."""
    return [s for s in (normalize_quote(p) for p in _ELLIPSIS.split(quote)) if s]

def quote_in(normalized_text: str, quote: str) -> bool:
    """Whether ``quote`` occurs in an already-normalized text (elided parts in order)."""
    segments = quote_segments(quote)
    return bool(segments) and _contains_in_order(normalized_text, segments)

def _contains_in_order(text: str, segments: list[str]) -> bool:
    pos = 0
    for seg in segments:
        found = text.find(seg, pos)
        if found < 0:
            return False
        pos = found + len(seg)
    return True

class EvidenceIndex:
    """The run's acquired evidence: chunks by (url, page) and document records by URL."""

    def __init__(self, chunks: Iterable[Mapping[str, Any]],
                 documents: Iterable[Mapping[str, Any]] = ()) -> None:
        self.by_key: dict[tuple[str, int | None], list[IndexedChunk]] = {}
        self.urls: set[str] = set()
        for c in chunks:
            item = IndexedChunk(c["chunk_id"], c["url"], c.get("page_number"), c["text"],
                                normalize_quote(c["text"]), c.get("source_type"))
            self.by_key.setdefault((item.url, item.page_number), []).append(item)
            self.urls.add(item.url)
        self.documents: dict[str, Mapping[str, Any]] = {}
        for d in documents:
            for url in (d.get("final_url"), d.get("requested_url"), d.get("canonical_url")):
                if url:
                    self.documents.setdefault(url, d)

    def locate(self, ref: EvidenceRef) -> Located:
        """Find the cited quote in the run's chunks. A quote elided with "…" matches when its
        parts occur in order; a quote across a chunk boundary matches the page's text."""
        segments = quote_segments(ref.passage)
        candidates = self.by_key.get((ref.url, ref.page_number), [])
        document = self.documents.get(ref.url)
        known = ref.url in self.urls
        if not segments:
            return Located(None, known, document)
        chunk = next((c for c in candidates if _contains_in_order(c.norm_text, segments)), None)
        if chunk is not None:
            return Located(chunk, known, document)
        page = " ".join(c.norm_text for c in candidates)  # chunks are in document order
        if len(candidates) > 1 and _contains_in_order(page, segments):
            head = segments[0][:40]
            start = next((c for c in candidates if head in c.norm_text), candidates[0])
            return Located(start, known, document, spans_chunks=True)
        return Located(None, known, document)

    def source_type(self, ref: EvidenceRef, located: Located | None = None) -> str | None:
        located = located or self.locate(ref)
        if located.chunk and located.chunk.source_type:
            return located.chunk.source_type
        if located.document:
            return located.document.get("source_type")
        any_chunk = next(iter(self.by_key.get((ref.url, ref.page_number), [])), None)
        return any_chunk.source_type if any_chunk else None

# --- quantities in text ---------------------------------------------------------------------

@dataclass(frozen=True)
class Quantity:
    number: float  # as written, multiplier applied
    canonical: float | None  # converted to the field's canonical unit, if a unit was found
    unit: str | None
    period: str | None  # day | year
    start: int

_NUMBER = re.compile(r"(?<![\w.,])(\d{1,3}(?:[,\u202f\u2009\u00a0]\d{3})+|\d+)(\.\d+)?(?![\d])")
_MULTIPLIER = re.compile(r"^\s*(thousand|million|billion|mn|bn)\b", re.IGNORECASE)
_MULTIPLIERS = {"thousand": 1e3, "million": 1e6, "mn": 1e6, "billion": 1e9, "bn": 1e9}
# shorthand volume-rate units -> (m3 per unit, period); case-sensitive where "ML" matters
_SHORTHAND = [
    (re.compile(r"^\s*(?:MLD|ML/d(?:ay)?|Ml/d(?:ay)?)\b"), 1e3, "day", "ML/d"),
    (re.compile(r"^\s*(?:MGD|mgd)\b"), 3785.411784, "day", "MGD"),
    (re.compile(r"^\s*(?:m3|m³|cu\.?\s*m)\s*/\s*d(?:ay)?\b", re.IGNORECASE), 1.0, "day", "m3/d"),
    (re.compile(r"^\s*(?:GL|Gl)\s*/\s*(?:yr|year|a)\b"), 1e6, "year", "GL/yr"),
    (re.compile(r"^\s*(?:ML|Ml)\s*/\s*(?:yr|year|a)\b"), 1e3, "year", "ML/yr"),
    (re.compile(r"^\s*(?:hm3|hm³)(?:\s*/\s*(?:yr|year|a))?\b", re.IGNORECASE), 1e6, "year",
     "hm3/yr"),
    (re.compile(r"^\s*(?:m3|m³)\s*/\s*(?:yr|year|a)\b", re.IGNORECASE), 1.0, "year", "m3/yr"),
]
_VOLUME_WORDS = [
    (re.compile(r"^\s*(?:cubic\s+hectomet(?:re|er)s?)", re.IGNORECASE), 1e6, "hm3"),
    (re.compile(r"^\s*(?:cubic\s+met(?:re|er)s?|m3|m³)", re.IGNORECASE), 1.0, "m3"),
    (re.compile(r"^\s*(?:gigalit(?:re|er)s?|GL)\b"), 1e6, "GL"),
    (re.compile(r"^\s*(?:megalit(?:re|er)s?|ML|Ml)\b"), 1e3, "ML"),
    (re.compile(r"^\s*(?:kilolit(?:re|er)s?|kL)\b"), 1.0, "kL"),
    (re.compile(r"^\s*lit(?:re|er)s?\b", re.IGNORECASE), 1e-3, "L"),
    (re.compile(r"^\s*(?:US\s+)?gallons?\b", re.IGNORECASE), 0.003785411784, "gal"),
    (re.compile(r"^\s*acre[-\s]?f(?:ee|oo)t\b", re.IGNORECASE), 1233.48183754752, "acre-ft"),
]
# A word prefix needs a space ("and" is not "an d"); "/" doesn't ("m3/d").
_PERIOD = re.compile(r"^\s*(?:(?:(?:per|a|an|each|every)\s+|/\s*)(day|d|year|yr|annum)\b"
                     r"|(daily|a\s+day|annually|yearly|a\s+year|per\s+annum))", re.IGNORECASE)
# "of drinking water" between a volume and its period: up to four words, matched together with
# the period so the filler can backtrack to wherever the period starts.
_FILLER_PERIOD = re.compile(r"^\s*(?:of\s+(?:[A-Za-z-]+\s+){0,4}?)?"
                            + _PERIOD.pattern.removeprefix("^"), re.IGNORECASE)
_PERCENT = re.compile(r"^\s*(?:%|per\s?cent\b)", re.IGNORECASE)
_YEAR = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")
_DMS = re.compile(r"(\d{1,3})\s*°\s*(\d{1,2})\s*['′]\s*(?:(\d{1,2}(?:\.\d+)?)\s*[\"″])?\s*"
                  r"([NSEW])", re.IGNORECASE)
_DECIMAL_DEG = re.compile(r"(-?\d{1,3}\.\d+)\s*°?\s*([NSEW])?(?![\w])", re.IGNORECASE)

def _to_float(digits: str, decimals: str | None) -> float:
    return float(re.sub(r"[,\u202f\u2009\u00a0]", "", digits) + (decimals or ""))

def quantities(text: str, unit: str | None = None) -> list[Quantity]:
    """Numbers in ``text`` with any volume-rate, percent or population unit that follows."""
    out: list[Quantity] = []
    for m in _NUMBER.finditer(text):
        number = _to_float(m.group(1), m.group(2))
        tail = text[m.end():m.end() + 80]
        mult = _MULTIPLIER.match(tail)
        multiplier = 1.0
        if mult:
            multiplier = _MULTIPLIERS[mult.group(1).lower()]
            tail = tail[mult.end():]
        value = number * multiplier
        canonical: float | None = None
        unit_label: str | None = None
        period: str | None = None
        if unit in ("m3/day", None):
            for pattern, factor, per, label in _SHORTHAND:
                hit = pattern.match(tail)
                if hit:
                    canonical, unit_label, period = value * factor, label, per
                    break
            else:
                for pattern, factor, label in _VOLUME_WORDS:
                    hit = pattern.match(tail)
                    if not hit:
                        continue
                    per = _FILLER_PERIOD.match(tail[hit.end():])  # "of drinking water per day"
                    if per:
                        word = (per.group(1) or per.group(2) or "").lower()
                        period = "day" if word.startswith(("d", "a d")) else "year"
                        canonical, unit_label = value * factor, f"{label}/{period}"
                    break
        if unit == "percent" and _PERCENT.match(tail):
            canonical, unit_label = value, "%"
        if unit == "million_people":
            canonical, unit_label = value / 1e6, ("people" if not mult else mult.group(1).lower())
            if not mult and number < 100:  # tables already in millions
                canonical, unit_label = number, "millions"
        out.append(Quantity(value, canonical, unit_label, period, m.start()))
    return out

def years_in(text: str, max_year: int) -> list[int]:
    return sorted({int(y) for y in _YEAR.findall(text) if MIN_YEAR <= int(y) <= max_year})

def coordinates_in(text: str) -> list[tuple[float, str | None]]:
    """(absolute decimal degrees, hemisphere) found in ``text`` (DMS or decimal)."""
    found: list[tuple[float, str | None]] = []
    for m in _DMS.finditer(text):
        deg = float(m.group(1)) + float(m.group(2)) / 60 + float(m.group(3) or 0) / 3600
        found.append((deg, m.group(4).upper()))
    for m in _DECIMAL_DEG.finditer(text):
        found.append((abs(float(m.group(1))), (m.group(2) or "").upper() or None))
    return found

def close(a: float, b: float, rel: float = NUMERIC_TOLERANCE) -> bool:
    return math.isclose(a, b, rel_tol=rel, abs_tol=1e-9)

# --- claims ---------------------------------------------------------------------------------

def claim_uid(claim: FieldClaim) -> str:
    """Stable id: field, value as extracted, status and the cited (url, page, quote)s."""
    original = claim.original_value if claim.original_value is not None else claim.value
    refs = sorted((e.url, e.page_number or 0, normalize_quote(e.passage))
                  for e in claim.evidence)
    parts: list[Any] = [claim.field_key, original, claim.value_status.value, refs]
    if claim.review_status == "CORRECTED":  # a reviewer's value is a different claim
        parts.append(["CORRECTED", claim.corrected_from])
    payload = json.dumps(parts, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:24]

@dataclass
class _Checks:
    items: list[ValidationCheck] = field(default_factory=list)

    def add(self, code: str, result: CheckResult, detail: str | None = None) -> None:
        self.items.append(ValidationCheck(code=code, result=result, detail=detail))

    def outcome(self) -> ValidationOutcome:
        results = {c.result for c in self.items}
        if CheckResult.FAIL in results:
            return ValidationOutcome.INVALID
        if CheckResult.WARN in results:
            return ValidationOutcome.VALID_WITH_WARNINGS
        return ValidationOutcome.VALID

def normalize_research(research: CityResearchState, fields: Mapping[str, FieldDefinition],
                       index: EvidenceIndex, current_year: int | None = None
                       ) -> CityResearchState:
    """A copy of ``research`` with every candidate (and selected) claim normalized."""
    current_year = current_year or datetime.now(UTC).year
    out = research.model_copy(deep=True)
    for key, fs in out.fields.items():
        fs.candidate_claims = [normalize_claim(c, fields.get(key), index, out, current_year,
                                               fields)
                               for c in fs.candidate_claims]
        if fs.selected_claim is not None:
            uid = claim_uid(fs.selected_claim) if fs.selected_claim.claim_uid is None \
                else fs.selected_claim.claim_uid
            fs.selected_claim = fs.claim(uid) or normalize_claim(
                fs.selected_claim, fields.get(key), index, out, current_year, fields)
    return out

@lru_cache
def _all_definitions() -> Mapping[str, FieldDefinition]:
    from .field_state import load_field_definitions
    return load_field_definitions()

def normalize_claim(claim: FieldClaim, definition: FieldDefinition | None, index: EvidenceIndex,
                    research: CityResearchState, current_year: int,
                    fields: Mapping[str, FieldDefinition] | None = None) -> FieldClaim:
    original = claim.original_value if claim.original_value is not None else claim.value
    claim = claim.model_copy(update={"original_value": original, "value": original})
    uid = claim.claim_uid or claim_uid(claim)
    checks = _Checks()
    previous = claim.qualifiers
    qualifiers = ClaimQualifiers(
        extraction_round=previous.extraction_round if previous else None,
        source_kind=previous.source_kind if previous else None,
        relations=list(previous.relations) if previous else [],
        corroborating_urls=list(previous.corroborating_urls) if previous else [],
        evidence_class=previous.evidence_class if previous else None)
    normalized: Any = None

    if definition is None:
        checks.add("FIELD_KNOWN", CheckResult.FAIL, f"unknown field {claim.field_key!r}")
        return _finish(claim, uid, normalized, qualifiers, checks)
    checks.add("FIELD_KNOWN", CheckResult.PASS)
    if definition.derived:
        checks.add("DERIVED_FIELD", CheckResult.FAIL,
                   "derived fields are calculated by the rules engine, never extracted")
    if definition.key == "references":
        checks.add("GENERATED_FIELD", CheckResult.WARN,
                   "the References column is generated from the citations")

    status_ok = _check_value_status(claim, checks)
    located = _check_citations(claim, index, checks, qualifiers, definition, research)
    quotes = [e.passage for e in claim.evidence]
    if claim.value_status is ValueStatus.KNOWN and status_ok:
        normalized = _check_type(claim.value, definition, checks)
        if normalized is not None:
            _check_range(normalized, definition, checks, current_year)
            normalized = _check_value_in_quote(normalized, definition, quotes, checks,
                                               qualifiers, located, index, current_year)
            normalized = _check_format(normalized, definition, checks)
            _check_scope(definition, quotes, checks)
    _check_reference_year(claim, definition, normalized, quotes, located, checks, qualifiers,
                          current_year, index)
    _check_dependencies(claim, definition, normalized, research, checks, qualifiers)
    if status_ok:
        semantic_checks(claim, definition, normalized, located, index, research, qualifiers,
                        checks.add, fields or _all_definitions(), current_year)
    if definition.key in SOURCE_FIELDS and claim.value_status is ValueStatus.KNOWN:
        _check_source_kind(claim, definition, qualifiers, checks)
    _check_out_of_area(located, research, checks)
    if previous is not None and previous.year_basis == "reviewer":
        # the year a reviewer gave with a corrected value is kept as given
        qualifiers.reference_year, qualifiers.year_basis = previous.reference_year, "reviewer"
        if previous.source_dates:
            qualifiers.source_dates = list(dict.fromkeys(
                [*previous.source_dates, *qualifiers.source_dates]))
    if qualifiers.evidence_class == "REVIEWER_SUPPLIED":
        _reviewer_supplied(checks)
    elif qualifiers.evidence_class is None and claim.review_status is None:
        qualifiers.evidence_class = "MACHINE_VERIFIED" if any(
            loc.chunk is not None for _, loc in located) else None
    return _finish(claim, uid, normalized, qualifiers, checks)

# Checks that test the evidence against the run's acquired text: for a reviewer-supplied
# source they can't pass, which means "not machine-verified", not "invalid".
_EVIDENCE_CHECKS = frozenset({"CITATION_LINKED", "VALUE_IN_QUOTE", "SOURCE_TIER"})

def _reviewer_supplied(checks: _Checks) -> None:
    checks.items = [
        c.model_copy(update={"result": CheckResult.WARN,
                             "detail": "reviewer-supplied source: not machine-verified"
                                       + (f" ({c.detail})" if c.detail else "")})
        if c.code in _EVIDENCE_CHECKS and c.result is CheckResult.FAIL else c
        for c in checks.items]

# --- water sources: kinds per the standardization rules ---------------------------------------

SOURCE_FIELDS = frozenset({"water_source", "reservoir_names"})
# Valid Water Source values (methodology): natural water bodies, Reclaimed, desalinated sea.
NATURAL_SOURCE_KINDS = frozenset({"river", "lake", "spring", "aquifer", "wellfield", "sea",
                                  "reclaimed", "desalination"})
_RESERVOIR_WORD = re.compile(r"\b(reservoirs?|dams?|impoundments?)\b", re.IGNORECASE)
_GENERAL_WORDS = re.compile(r"^\W*(?:local\s+|municipal\s+|private\s+|shallow\s+|deep\s+)*"
                            r"(?:groundwater|ground\s+water|wells?|boreholes?|surface\s+water)"
                            r"\b", re.IGNORECASE)

def infer_source_kind(value: Any) -> str | None:
    """A kind only where the wording is explicit (claims extracted before kinds existed);
    otherwise None: a value is never rejected for lacking a proper noun."""
    text = str(value or "").strip()
    if text.casefold() == "reclaimed":
        return "reclaimed"
    if _RESERVOIR_WORD.search(text) and not re.search(r"\b(river|lake|spring|aquifer)\b",
                                                       text, re.IGNORECASE):
        return "reservoir"
    if _GENERAL_WORDS.match(text):
        return "general_groundwater" if re.search(r"ground|well|bore", text, re.IGNORECASE) \
            else "category"
    return None

def _check_source_kind(claim: FieldClaim, d: FieldDefinition, qualifiers: ClaimQualifiers,
                       checks: _Checks) -> None:
    kind = qualifiers.source_kind or infer_source_kind(claim.value)
    qualifiers.source_kind = kind
    if d.key == "reservoir_names":
        if kind not in (None, "reservoir", "lake"):
            checks.add("SOURCE_KIND", CheckResult.WARN, f"named as a {kind}, not a reservoir")
        else:
            checks.add("SOURCE_KIND", CheckResult.PASS, kind or "unspecified")
        return
    if kind is None or kind in NATURAL_SOURCE_KINDS:
        checks.add("SOURCE_KIND", CheckResult.PASS, kind or "unspecified")
    elif kind == "general_groundwater":
        checks.add("SOURCE_KIND", CheckResult.WARN,
                   "a general groundwater description (allowed when no named aquifer is "
                   "reported; confirm)")
    else:
        checks.add("SOURCE_KIND", CheckResult.WARN,
                   f"a {kind.replace('_', ' ')} is not a Water Source under the methodology "
                   "(reservoirs go to Reservoir Name(s); basins/systems to Water System)")

# --- out-of-area documents ------------------------------------------------------------------

US_STATES = {"alabama": "al", "alaska": "ak", "arizona": "az", "arkansas": "ar",
             "california": "ca", "colorado": "co", "connecticut": "ct", "delaware": "de",
             "florida": "fl", "georgia": "ga", "hawaii": "hi", "idaho": "id", "illinois": "il",
             "indiana": "in", "iowa": "ia", "kansas": "ks", "kentucky": "ky",
             "louisiana": "la", "maine": "me", "maryland": "md", "massachusetts": "ma",
             "michigan": "mi", "minnesota": "mn", "mississippi": "ms", "missouri": "mo",
             "montana": "mt", "nebraska": "ne", "nevada": "nv", "new hampshire": "nh",
             "new jersey": "nj", "new mexico": "nm", "new york": "ny",
             "north carolina": "nc", "north dakota": "nd", "ohio": "oh", "oklahoma": "ok",
             "oregon": "or", "pennsylvania": "pa", "rhode island": "ri",
             "south carolina": "sc", "south dakota": "sd", "tennessee": "tn", "texas": "tx",
             "utah": "ut", "vermont": "vt", "virginia": "va", "washington": "wa",
             "west virginia": "wv", "wisconsin": "wi", "wyoming": "wy"}
_STATE_HOST = re.compile(r"(?:^|\.)([a-z]{2})\.gov$|(?:^|\.)state\.([a-z]{2})\.us$")

def _check_out_of_area(located: list[tuple[EvidenceRef, Located]], research: CityResearchState,
                       checks: _Checks) -> None:
    """Every citation is a US state government document of another state than the city's
    (e.g. a same-named city elsewhere): the evidence is about another place."""
    state = _city_state_code(research)
    if state is None or not located:
        return
    hosts = [urlsplit(ref.url).hostname or "" for ref, _ in located]
    codes = [next((g for g in m.groups() if g), None) if (m := _STATE_HOST.search(h)) else None
             for h in hosts]
    if all(codes) and all(c != state for c in codes):
        checks.add("OUT_OF_AREA", CheckResult.WARN,
                   f"cited only from another US state's government ({', '.join(sorted(set(codes)))}"
                   f"; the city is in {state})")

def _city_state_code(research: CityResearchState) -> str | None:
    fs = research.fields.get("jurisdiction")
    if fs is None:
        return None
    values = [str(c.comparable_value or "") for c in fs.candidate_claims
              if c.value_status is ValueStatus.KNOWN]
    if fs.selected_claim is not None:
        values.insert(0, str(fs.selected_claim.comparable_value or ""))
    for v in values:
        code = US_STATES.get(v.strip().casefold())
        if code:
            return code
    return None

def _finish(claim: FieldClaim, uid: str, normalized: Any, qualifiers: ClaimQualifiers,
            checks: _Checks) -> FieldClaim:
    return claim.model_copy(update={
        "claim_uid": uid, "normalized_value": normalized, "qualifiers": qualifiers,
        "validation": ClaimValidation(outcome=checks.outcome(), checks=checks.items,
                                      version=NORMALIZATION_VERSION)})

def coerce_value(value: Any, d: FieldDefinition, current_year: int | None = None
                 ) -> tuple[Any, list[str]]:
    """(typed value, problems) for a value entered by a person (type, enum and range)."""
    checks = _Checks()
    typed = _check_type(value, d, checks)
    if typed is not None:
        _check_range(typed, d, checks, current_year or datetime.now(UTC).year)
    problems = [c.detail or c.code for c in checks.items if c.result is CheckResult.FAIL]
    return typed, problems

def _check_value_status(claim: FieldClaim, checks: _Checks) -> bool:
    if claim.value_status is ValueStatus.KNOWN and claim.value in (None, ""):
        checks.add("VALUE_STATUS", CheckResult.FAIL, "KNOWN claim without a value")
        return False
    if claim.value_status is not ValueStatus.KNOWN and claim.value is not None:
        checks.add("VALUE_STATUS", CheckResult.FAIL,
                   f"{claim.value_status.value} claim must not carry a value")
        return False
    if claim.value_status is ValueStatus.UNKNOWN:
        checks.add("VALUE_STATUS", CheckResult.FAIL,
                   "UNKNOWN is a review outcome, not an extractable claim")
        return False
    checks.add("VALUE_STATUS", CheckResult.PASS)
    return True

@lru_cache
def authority_roles() -> tuple[tuple[str, ...], dict[str, tuple[str, ...]]]:
    """(default roles, field -> roles) from config/authority_roles.yaml."""
    import yaml
    from water_source_research.core.paths import CONFIG_DIR
    data = yaml.safe_load((CONFIG_DIR / "authority_roles.yaml").read_text(encoding="utf-8"))
    return (tuple(data.get("default") or ()),
            {k: tuple(v) for k, v in (data.get("fields") or {}).items()})

def _authority(located: list[tuple[EvidenceRef, Located]], index: EvidenceIndex,
               definition: FieldDefinition | None, research: CityResearchState | None,
               qualifiers: ClaimQualifiers, checks: _Checks) -> None:
    """Authority of each cited source for this field. A UTILITY page (or any page on a host
    the registry binds to a utility) is a verified serving utility's only when the utility
    registry verified, from explicit statements, a role the field accepts; otherwise it is
    UNVERIFIED_UTILITY and doesn't count as official."""
    from water_source_research.services.utility_registry import registry_for_index
    default, by_field = authority_roles()
    allowed = set(by_field.get(definition.key, default) if definition else default)
    registry = None
    authority: list[str] = []
    official: list[str] = []
    unverified: list[str] = []
    for ref, loc in located:
        st = index.source_type(ref, loc)
        host = (urlsplit(ref.url).hostname or "").casefold().removeprefix("www.")
        if research is not None and registry is None:  # cached on the index
            registry = registry_for_index(research, index)
        roles = set(registry.host_roles(host)) if registry is not None else set()
        bound = registry is not None and any(host in c.bound_hosts for c in registry.candidates)
        if st == "UTILITY" or (bound and roles):
            if roles & allowed:
                label, effective = "VERIFIED_SERVING_UTILITY", "UTILITY"
            else:
                label, effective = "UNVERIFIED_UTILITY", None
                unverified.append(host)
        else:
            label, effective = st or "UNKNOWN", st
        if label not in authority:
            authority.append(label)
        if effective and effective not in official:
            official.append(effective)
    qualifiers.authority, qualifiers.official_types = authority, official
    if unverified and not set(official) & OFFICIAL_SOURCE_TYPES:
        checks.add("UTILITY_RELATIONSHIP_UNVERIFIED", CheckResult.WARN,
                   f"{', '.join(dict.fromkeys(unverified))}: not shown to be a utility serving "
                   f"the city in a role this field needs ({', '.join(sorted(allowed))})")

def _check_citations(claim: FieldClaim, index: EvidenceIndex, checks: _Checks,
                     qualifiers: ClaimQualifiers, definition: FieldDefinition | None = None,
                     research: CityResearchState | None = None
                     ) -> list[tuple[EvidenceRef, Located]]:
    if not claim.evidence:
        checks.add("EVIDENCE_PRESENT", CheckResult.FAIL, "no citation")
        checks.add("CITATION_LINKED", CheckResult.FAIL, "no citation")
        return []
    checks.add("EVIDENCE_PRESENT", CheckResult.PASS)
    located = [(ref, index.locate(ref)) for ref in claim.evidence]
    for ref, loc in located:
        ground_ref(ref, loc)
    for rel in qualifiers.relations or []:  # stated relations: their own chunk and span
        ground_relation(rel, index)
    linked = [loc for _, loc in located if loc.chunk is not None]
    failed_docs = [ref.url for ref, loc in located
                   if loc.document is not None and loc.document.get("status") not in (None, "OK")]
    unlinked = [ref.url for ref, loc in located if loc.chunk is None]
    if not linked:
        checks.add("CITATION_LINKED", CheckResult.FAIL,
                   "no cited quote was found in the run's acquired evidence")
    elif failed_docs:
        checks.add("CITATION_LINKED", CheckResult.FAIL,
                   "cited document was not acquired successfully: " + ", ".join(failed_docs))
    elif unlinked:
        checks.add("CITATION_LINKED", CheckResult.WARN,
                   f"{len(unlinked)} citation(s) not found in an evidence chunk")
    else:
        spanning = sum(loc.spans_chunks for loc in linked)
        checks.add("CITATION_LINKED", CheckResult.PASS, f"{len(linked)} chunk(s)"
                   + (f", {spanning} across a chunk boundary" if spanning else ""))
    types = []
    dates = []
    for ref, loc in located:
        st = index.source_type(ref, loc)
        if st and st not in types:
            types.append(st)
        date = loc.document.get("source_date") if loc.document else None
        if date and date not in dates:
            dates.append(date)
    qualifiers.source_types = types
    qualifiers.source_dates = dates
    _authority(located, index, definition, research, qualifiers, checks)
    official = [t for t in qualifiers.official_types or [] if t in OFFICIAL_SOURCE_TYPES]
    checks.add("SOURCE_TIER", CheckResult.PASS,
               ("official: " + ", ".join(official)) if official
               else "no verified utility, government or regulatory source")
    return located

def _check_type(value: Any, d: FieldDefinition, checks: _Checks) -> Any:
    if d.allowed:
        canonical = {a.casefold(): a for a in d.allowed}
        hit = canonical.get(" ".join(str(value).split()).casefold())
        if hit is None:
            checks.add("ENUM", CheckResult.FAIL,
                       f"{value!r} is not one of: {', '.join(d.allowed)}")
            return None
        checks.add("ENUM", CheckResult.PASS)
        return hit
    if d.type in ("number", "percent", "coordinate", "integer"):
        number = _as_number(value)
        if number is None:
            checks.add("TYPE", CheckResult.FAIL, f"{value!r} is not a number")
            return None
        if d.type == "integer":
            if not float(number).is_integer():
                checks.add("TYPE", CheckResult.FAIL, f"{value!r} is not an integer")
                return None
            checks.add("TYPE", CheckResult.PASS)
            return int(number)
        checks.add("TYPE", CheckResult.PASS)
        return float(number)
    if not isinstance(value, str) or not value.strip():
        checks.add("TYPE", CheckResult.FAIL, "expected non-empty text")
        return None
    checks.add("TYPE", CheckResult.PASS)
    return " ".join(value.split()).strip(" .;")

def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, str):
        m = re.fullmatch(r"\s*(-?\d{1,3}(?:,\d{3})+|-?\d+)(\.\d+)?\s*", value)
        if m:
            return _to_float(m.group(1), m.group(2))
    return None

def _check_range(value: Any, d: FieldDefinition, checks: _Checks, current_year: int) -> None:
    problem: str | None = None
    if d.type == "coordinate":
        limit = 90 if d.key.endswith("latitude") else 180
        if not -limit <= value <= limit:
            problem = f"{value} is outside ±{limit}"
    elif d.type == "percent":
        if not 0 <= value <= 100:
            problem = f"{value} is outside 0-100"
    elif d.type == "integer" and d.key.endswith("_year"):
        if not MIN_YEAR <= value <= current_year + 1:
            problem = f"{value} is not a plausible year"
    elif d.unit == "million_people":
        if value <= 0:
            problem = "population must be positive"
        elif value > 40:
            problem = (f"{value} million exceeds any city; if this is a head count it is "
                       f"{value / 1e6:.3f} million")
    elif d.unit == "m3/day":
        if value <= 0:
            problem = "volume must be positive"
        elif value > 5e7:
            problem = f"{value} m3/day exceeds any city's supply"
    checks.add("RANGE", CheckResult.FAIL if problem else CheckResult.PASS, problem)

def _check_value_in_quote(value: Any, d: FieldDefinition, quotes: list[str], checks: _Checks,
                          q: ClaimQualifiers, located, index: EvidenceIndex,
                          current_year: int) -> Any:
    text = " … ".join(quotes)
    if not text:
        checks.add("VALUE_IN_QUOTE", CheckResult.WARN, "no quote to verify against")
        return value
    if d.type == "integer" and d.key.endswith("_year"):
        if value in years_in(text, current_year + 1):
            checks.add("VALUE_IN_QUOTE", CheckResult.PASS)
        else:
            checks.add("VALUE_IN_QUOTE", CheckResult.WARN, f"{value} does not appear in the quote")
        return value
    if d.type == "coordinate":
        found = coordinates_in(text)
        if any(abs(abs(value) - deg) <= COORDINATE_TOLERANCE for deg, _ in found):
            checks.add("VALUE_IN_QUOTE", CheckResult.PASS)
        elif found:
            checks.add("VALUE_IN_QUOTE", CheckResult.FAIL,
                       f"quote gives {', '.join(f'{deg:.4f}' for deg, _ in found)}")
        else:
            checks.add("VALUE_IN_QUOTE", CheckResult.WARN, "no coordinates in the quote")
        return round(value, 4)
    if d.type in ("number", "percent"):
        return _number_in_quote(value, d, text, checks, q, current_year)
    if d.allowed:
        cues = WATER_TYPE_CUES.get(value) if d.key == "water_type" else None
        lowered = text.casefold()
        if value.casefold() in lowered or (cues and any(c in lowered for c in cues)):
            checks.add("VALUE_IN_QUOTE", CheckResult.PASS)
        else:
            checks.add("VALUE_IN_QUOTE", CheckResult.WARN,
                       f"the quote does not mention {value!r} or its cues")
        return value
    return _text_in_quote(value, text, checks)

def _number_in_quote(value: float, d: FieldDefinition, text: str, checks: _Checks,
                     q: ClaimQualifiers, current_year: int) -> float:
    unit = "percent" if d.type == "percent" else d.unit
    found = quantities(text, unit)
    years = set(years_in(text, current_year + 1))
    meaningful = [x for x in found if not (x.unit is None and x.number in years)]
    ref_years = sorted(years) or [None]
    for x in meaningful:
        candidates: list[tuple[float, str | None]] = []
        if x.canonical is not None and x.period == "year" and unit == "m3/day":
            for y in [*ref_years, None]:
                candidates.append((x.canonical / days_in_year(y),
                                   f"{x.unit} ÷ {days_in_year(y):g}"))
        elif x.canonical is not None:
            candidates.append((x.canonical, x.unit))
        elif unit != "percent":
            candidates.append((x.number, None))  # bare number in the field's unit
        for converted, how in candidates:
            if close(value, converted):
                q.reported_value = x.number
                q.reported_unit = x.unit
                if x.period == "year":
                    q.measurement_basis = "annual"
                checks.add("VALUE_IN_QUOTE", CheckResult.PASS,
                           f"{x.number:g} {x.unit or ''}".strip()
                           + (f" -> {converted:g} ({how})" if how and how != x.unit else ""))
                hedge = _APPROXIMATE.search(text[max(0, x.start - 25):x.start])
                if hedge:
                    checks.add("APPROXIMATE_VALUE", CheckResult.WARN,
                               f"the source qualifies the number ({hedge.group(0).strip()!r})")
                # Methodology conversion (365/366/365.25) replaces any model arithmetic.
                return round(converted, 6)
    if meaningful:
        checks.add("VALUE_IN_QUOTE", CheckResult.FAIL,
                   f"{value:g} does not match the quote's numbers: "
                   + ", ".join(f"{x.number:g}{' ' + x.unit if x.unit else ''}"
                               for x in meaningful[:5]))
    else:
        checks.add("VALUE_IN_QUOTE", CheckResult.WARN, "the quote contains no number")
    return value

_APPROXIMATE = re.compile(r"\b(more than|over|nearly|almost|approximately|approx\.?|about|"
                          r"around|roughly|some|up to|in excess of|estimated|an estimated)\s*$|"
                          r"[~≈<>]\s*$", re.IGNORECASE)
# The figure covers more than the city (e.g. "Toronto, and portions of York and Peel").
_WIDER_AREA = re.compile(r"\b(and|plus|including|as well as)\s+(portions|parts|areas)\s+of\b|"
                         r"\b(surrounding|neighbou?ring|greater)\b|\bregions?\b|"
                         r"\bmetropolitan area\b|\bGTA\b", re.IGNORECASE)

def _check_scope(d: FieldDefinition, quotes: list[str], checks: _Checks) -> None:
    """City-level figures quoted for a wider area aren't the city's figure."""
    if d.scope != "city" or d.type not in ("number", "integer") or d.key.endswith("_year"):
        return
    hit = _WIDER_AREA.search(" ".join(quotes))
    if hit:
        checks.add("GEOGRAPHIC_SCOPE", CheckResult.WARN,
                   f"the figure may cover more than the city ({hit.group(0)!r})")

_STOP = frozenset({"the", "and", "for", "from", "with", "its", "that", "this", "city", "of"})

def _words(text: str) -> list[str]:
    """Lowercase words, possessive 's dropped ("Toronto's" -> "toronto")."""
    return [re.sub(r"['’]s$", "", w) for w in re.findall(r"[\w'’]+", text.casefold())]

def _text_in_quote(value: str, text: str, checks: _Checks) -> str:
    quote = normalize_quote(text)
    if normalize_quote(value) in quote:
        checks.add("VALUE_IN_QUOTE", CheckResult.PASS)
        return value
    tokens = [t for t in _words(value) if len(t) > 2 and t not in _STOP]
    quoted = set(_words(text))
    present = [t for t in tokens if t in quoted]
    if tokens and len(present) == len(tokens):
        checks.add("VALUE_IN_QUOTE", CheckResult.PASS, "all words of the value are quoted")
    elif present:
        checks.add("VALUE_IN_QUOTE", CheckResult.WARN,
                   f"only {len(present)}/{len(tokens)} words of the value are in the quote")
    else:
        checks.add("VALUE_IN_QUOTE", CheckResult.WARN, "the value is not in the quote")
    return value

def _check_format(value: Any, d: FieldDefinition, checks: _Checks) -> Any:
    if not isinstance(value, str):
        return value
    formatted = value
    if d.key in LIST_FIELDS:
        parts = [p.strip().casefold() for p in re.split(r"[;,]|\band\b", value) if p.strip()]
        formatted = ", ".join(dict.fromkeys(parts))
    elif d.key in NAME_FIELDS and (value.islower() or value.isupper()):
        formatted = _title_case(value)
    if formatted != value:
        checks.add("FORMAT", CheckResult.PASS, f"normalized to {formatted!r}")
    if d.key == "water_source" and re.search(r"\b(reservoir|dam|impoundment)\b", value,
                                             re.IGNORECASE):
        checks.add("RESERVOIR_AS_SOURCE", CheckResult.WARN,
                   "water_source must name the river, lake, aquifer or sea, not a reservoir")
    return formatted

_MINOR_WORDS = frozenset({"of", "the", "and", "de", "del", "la", "le", "da", "do", "di", "von"})

def _title_case(value: str) -> str:
    words = value.split()
    return " ".join(w.lower() if i and w.lower() in _MINOR_WORDS else w[:1].upper() + w[1:].lower()
                    for i, w in enumerate(words))

def _check_reference_year(claim: FieldClaim, d: FieldDefinition | None, normalized: Any,
                          quotes: list[str], located, checks: _Checks, q: ClaimQualifiers,
                          current_year: int, index: "EvidenceIndex | None" = None) -> None:
    if d is not None and d.type == "integer" and d.key.endswith("_year"):
        if isinstance(normalized, int):
            q.reference_year, q.year_basis = normalized, "quote"
        return
    max_year = current_year + 1
    in_quote = sorted({y for text in quotes for y in years_in(text, max_year)})
    if len(in_quote) == 1:
        q.reference_year, q.year_basis = in_quote[0], "quote"
        checks.add("REFERENCE_YEAR", CheckResult.PASS, f"{in_quote[0]} (quote)")
        return
    if len(in_quote) > 1:
        checks.add("REFERENCE_YEAR", CheckResult.WARN,
                   f"several years in the quote ({', '.join(map(str, in_quote))})")
        return
    for ref, loc in located:
        if loc.chunk is None:
            continue
        sentence = sentence_around(loc.chunk.text, ref.passage)
        dated = date_line_years(loc.chunk.text)  # a page date run into the sentence
        years = [y for y in years_in(sentence, max_year) if y not in dated] if sentence else []
        if len(years) == 1:
            q.reference_year, q.year_basis = years[0], "passage"
            checks.add("REFERENCE_YEAR", CheckResult.PASS, f"{years[0]} (same sentence)")
            return
    # A table row/header, heading/caption or record line attaching a year to the passage.
    for ref, loc in located:
        if loc.chunk is None or index is None:
            continue
        link = structural_year(page_text(index, ref), ref.passage,
                               _figure_as_written(ref.passage))
        if link.year is not None:
            q.reference_year, q.year_basis = link.year, link.basis
            checks.add("REFERENCE_YEAR", CheckResult.PASS,
                       f"{link.year} ({link.basis}: {link.detail})")
            return
        if link.ambiguous:
            checks.add("REFERENCE_YEAR", CheckResult.WARN, link.detail)
            return
    for date in q.source_dates:
        years = years_in(date, max_year)
        if years:
            q.reference_year, q.year_basis = years[0], "document"
            checks.add("REFERENCE_YEAR", CheckResult.PASS,
                       f"{years[0]} (document date; publication, not measurement, year)")
            return
    checks.add("REFERENCE_YEAR", CheckResult.WARN, "no reference year found")

def _figure_as_written(quote: str) -> str | None:
    """The first number in a quote that isn't a year, as written (to find its table cell)."""
    for m in _NUMBER.finditer(quote):
        if not _YEAR.fullmatch(m.group(0)):
            return m.group(0)
    return None

def sentence_around(text: str, quote: str) -> str | None:
    norm_quote = " ".join(quote.split()).casefold()
    flat = " ".join(text.split())
    pos = flat.casefold().find(norm_quote[:60])
    if pos < 0:
        return None
    start = max(flat.rfind(". ", 0, pos), flat.rfind("\n", 0, pos)) + 1
    end = flat.find(". ", pos + len(norm_quote))
    return flat[start:end if end >= 0 else len(flat)]

def _check_dependencies(claim: FieldClaim, d: FieldDefinition | None, normalized: Any,
                        research: CityResearchState, checks: _Checks,
                        q: ClaimQualifiers) -> None:
    if d is None:
        return
    if d.unit == "m3/day" or d.key in WITHDRAWAL_FIELDS:
        quote = " ".join(e.passage for e in claim.evidence).casefold()
        if re.search(r"\b(capacity|rated|design(ed)?)\b", quote):
            q.measurement_basis = "capacity"
            checks.add("MEASUREMENT_BASIS", CheckResult.WARN,
                       "the quote describes capacity, not withdrawals")
        elif re.search(r"\b(max(imum)?\s+day|peak|maximum)\b", quote):
            q.measurement_basis = "maximum"
            checks.add("MEASUREMENT_BASIS", CheckResult.WARN,
                       "the quote describes a maximum/peak, not an average")
        elif re.search(r"\b(average|avg|mean)\b", quote):
            q.measurement_basis = q.measurement_basis or "average"
            checks.add("MEASUREMENT_BASIS", CheckResult.PASS, "average")
    if d.type == "integer" and d.key.endswith("_year") and d.depends_on:
        parents = [research.fields.get(p) for p in d.depends_on]
        if not any(p and (p.candidate_claims or p.selected_claim) for p in parents):
            checks.add("DEPENDENCY", CheckResult.WARN,
                       f"no claim for {', '.join(d.depends_on)} that this year qualifies")
        else:
            checks.add("DEPENDENCY", CheckResult.PASS)
    if d.key == "pod_coordinate_method" and normalized == "Utility Source" and not (
            set(q.official_types if q.official_types is not None else q.source_types)
            & OFFICIAL_SOURCE_TYPES):
        checks.add("DEPENDENCY", CheckResult.WARN,
                   "'Utility Source' requires coordinates from an official document")
    if d.key == "source_withdrawal_contribution_method" and normalized == "Remainder Allocated":
        checks.add("METHOD_NOT_REPORTABLE", CheckResult.WARN,
                   "remainder allocation is a calculation, not a reported method")
