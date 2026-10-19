"""Failure diagnosis from saved provenance: for a field the pipeline got wrong or missed, find
the earliest stage where the failure can be demonstrated, and record what was observed.

Probes, in pipeline order (each records facts and links; none guesses):
  evidence    does any acquired chunk contain the reference value? are the reference's own
              source URLs among the discovery candidates / acquired documents?
  discovery   reference source absent from the retained candidates (raw search results are
              not persisted: PROBABLE, never CONFIRMED)
  acquisition candidate not fetched, fetch failed, or fetched without the value (PROBABLE)
  retrieval   a chunk with the value never entered the field's context bundles
  extraction  it did, and no claim carries the value
  validation  a correct claim was marked INVALID
  resolution  a wrong claim was accepted automatically (or a correct one escalated: noted)
  review      a reviewer decision moved the value away from the reference
  derivation  a calculated value disagrees with its recomputation

Symptoms (TEMPORAL_MISMATCH, GEOGRAPHIC_SCOPE_MISMATCH, FIELD_SEMANTICS_MISMATCH,
SOURCE_CLASSIFICATION_ERROR, GROUNDING) are kept separate from the root cause. Without enough
saved history the root cause is UNKNOWN.
"""

import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit
from water_source_research.schemas.research import FieldDefinition
from water_source_research.services.normalization import (
    _MULTIPLIER, _NUMBER, _PERIOD, _VOLUME_WORDS, close, coordinates_in, quantities, years_in,
)
from water_source_research.services.resolution import name_key
from water_source_research.services.rules_engine import days_in_year
from .compare import NOT_SCORED, _concept_words, entity_keys
from .evidence import suspicious_official
from .policy import DESCRIPTIVE_FIELDS, ComparePolicy
from .reference import ReferenceValue

ROOT_CAUSES = (
    "DISCOVERY_FAILURE", "ACQUISITION_FAILURE", "RETRIEVAL_FAILURE", "EXTRACTION_FAILURE",
    "VALIDATION_FAILURE", "RESOLUTION_FAILURE", "GROUNDING_FAILURE", "NORMALIZATION_FAILURE",
    "DERIVATION_FAILURE", "EXPORT_FAILURE", "REVIEW_INTRODUCED_DIFFERENCE",
    "REFERENCE_DISAGREEMENT", "EVIDENCE_UNAVAILABLE", "UNNECESSARY_ESCALATION", "UNKNOWN")
STAGE_OF = {"DISCOVERY_FAILURE": "discovery", "ACQUISITION_FAILURE": "acquisition",
            "RETRIEVAL_FAILURE": "retrieval", "EXTRACTION_FAILURE": "extraction",
            "GROUNDING_FAILURE": "extraction", "VALIDATION_FAILURE": "validation",
            "NORMALIZATION_FAILURE": "validation", "RESOLUTION_FAILURE": "resolution",
            "UNNECESSARY_ESCALATION": "resolution", "REVIEW_INTRODUCED_DIFFERENCE": "review",
            "DERIVATION_FAILURE": "derivation", "EXPORT_FAILURE": "export"}

AVAILABILITY_TOLERANCE = 0.01  # relative: "the acquired text states the reference value"

# normalization.quantities stops at "of " before the period in "980,000 cubic metres of
# drinking water per day" (its filler is lazy and anchored), leaving the number unitless. The
# evaluator re-reads such numbers with a filler that can backtrack; the research parser is not
# changed here.
_RATE_TAILS = [(re.compile(p.pattern + r"\s*(?:of\s+(?:[A-Za-z-]+\s+){0,4})?"
                           + _PERIOD.pattern.removeprefix("^"), p.flags), factor)
               for p, factor, _ in _VOLUME_WORDS]

def volume_rates(text: str) -> list[tuple[float, str, int]]:
    """(m3 per period, period, offset) for every volume rate stated in ``text``."""
    out = []
    for q in quantities(text, "m3/day"):
        if q.canonical is not None and q.period:
            out.append((q.canonical, q.period, q.start))
            continue
        m = _NUMBER.match(text, q.start)
        tail = text[m.end():m.end() + 120] if m else ""
        mult = _MULTIPLIER.match(tail)
        value = q.number
        if mult:  # quantities already applied it to q.number
            tail = tail[mult.end():]
        for pattern, factor in _RATE_TAILS:
            hit = pattern.match(tail)
            if hit:
                word = next(g for g in hit.groups() if g).lower()
                period = "day" if word.startswith(("d", "a d")) else "year"
                out.append((value * factor, period, q.start))
                break
    return out

def _contains_phrase(words: list[str], phrase: list[str]) -> bool:
    """Whether ``phrase`` (stemmed words) occurs contiguously in ``words``."""
    if not phrase:
        return False
    n = len(phrase)
    return any(words[i:i + n] == phrase for i in range(len(words) - n + 1))

def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    path = parts.path.rstrip("/")
    return f"{(parts.hostname or '').casefold().removeprefix('www.')}{path}".casefold()

class Diagnoser:
    """Per-snapshot indexes shared by every field diagnosis."""

    def __init__(self, snap: dict[str, Any], fields: Mapping[str, FieldDefinition],
                 policy: ComparePolicy) -> None:
        self.snap, self.fields, self.policy = snap, fields, policy
        self.chunks = snap["chunks"]
        self.candidates = {canonical_url(c["url"]): c for c in snap["discovery_candidates"]
                           if c.get("url")}
        self.documents: dict[str, dict[str, Any]] = {}
        for d in snap["documents"]:
            for u in (d.get("requested_url"), d.get("final_url"), d.get("canonical_url")):
                if u:
                    self.documents.setdefault(canonical_url(u), d)
        self.bundled: dict[str, dict[str, list[str]]] = {}  # field -> chunk -> bundle ids
        for b in snap["context_bundles"]:
            for item in b["items"]:
                if item.get("kind") != "evidence" or not item.get("chunk_id"):
                    continue
                for f in item.get("field_keys") or b.get("target_fields") or []:
                    self.bundled.setdefault(f, {}).setdefault(item["chunk_id"], []).append(
                        b["id"])

    # --- probes -------------------------------------------------------------------------------

    def evidence_with_value(self, d: FieldDefinition, ref: ReferenceValue) -> list[dict]:
        """Acquired chunks whose text contains the reference value (field-aware)."""
        hits = []
        for c in self.chunks:
            if self._contains(d, ref, c["text"]):
                hits.append({"chunk_id": c["chunk_id"], "document_id": c["document_id"],
                             "url": c["url"], "page_number": c.get("page_number")})
        return hits

    def _contains(self, d: FieldDefinition, ref: ReferenceValue, text: str) -> bool:
        value = ref.normalized
        if value is None or ref.value_status != "KNOWN":
            return False
        if d.type in ("number", "percent"):
            # The same quantity, stated with its unit or scale: close numbers or bare digits
            # (phone numbers, page numbers, other years' figures) are not this evidence.
            unit = "percent" if d.type == "percent" else d.unit
            tol = min(self.policy.tolerance(d.key), AVAILABILITY_TOLERANCE)
            if unit == "m3/day":
                found = volume_rates(text)
            else:
                found = [(q.canonical, q.period, q.start) for q in quantities(text, unit)
                         if q.canonical is not None and q.unit not in (None, "millions")]
            for canonical, period, start in found:
                options = [canonical]
                if period == "year":
                    options = [canonical / days_in_year(ref.reference_year), canonical / 365.25]
                if not any(close(float(value), o, tol) for o in options):
                    continue
                # A similar figure stated for another year is a different observation.
                around = years_in(text[max(0, start - 120):start + 120], 2100)
                if ref.reference_year and around and ref.reference_year not in around:
                    continue
                return True
            return False
        if d.type == "coordinate":
            return any(abs(abs(float(value)) - deg) <= 0.01 for deg, _ in coordinates_in(text))
        if d.type == "integer":
            return False  # years appear everywhere: not evidence of the value
        if d.key in DESCRIPTIVE_FIELDS or d.descriptive:
            concepts = [c for c in re.split(r"[;,]", str(value)) if c.strip()] \
                if not isinstance(value, list) else value
            words = _concept_words(text)
            return any(_contains_phrase(words, _concept_words(c)) for c in concepts)
        values = value if isinstance(value, list) else [value]
        padded = f" {name_key(text)} "
        return any(f" {k} " in padded for v in values for k in entity_keys(str(v), self.policy)
                   if k)

    def availability_confidence(self, d: FieldDefinition) -> str:
        """Numbers and coordinates found in the text are strong evidence; a name or phrase
        appearing somewhere is weaker (it may not state this field's value)."""
        return "CONFIRMED" if d.type in ("number", "percent", "coordinate") else "PROBABLE"

    def source_trail(self, urls: list[str]) -> list[dict[str, Any]]:
        """Where each of the reference's own source URLs got to in the pipeline."""
        trail = []
        for url in urls:
            key = canonical_url(url)
            doc = self.documents.get(key)
            trail.append({"url": url, "in_discovery_candidates": key in self.candidates,
                          "document_id": doc["document_id"] if doc else None,
                          "document_status": doc["status"] if doc else None,
                          "chunks": doc.get("chunks") if doc else None})
        return trail

    # --- one field ----------------------------------------------------------------------------

    def diagnose(self, field_key: str, record_key: str, ref: ReferenceValue,
                 stages: dict[str, dict[str, Any]], row_urls: list[str] | None = None
                 ) -> list[dict[str, Any]]:
        """Diagnoses for one reference cell from its RAW / AUTO / FINAL results.
        ``row_urls``: all sources of the reference row, used (with weaker conclusions) when
        the reference names no source for this field."""
        self._row_urls = row_urls or []
        d = self.fields[field_key]
        raw, auto, final = stages.get("RAW"), stages.get("AUTO"), stages.get("FINAL")
        out: list[dict[str, Any]] = []
        symptoms = self._symptoms(raw, auto)
        raw_ok = raw is not None and raw["status"] == "MATCH"

        if raw is not None and not raw_ok and not d.derived \
                and raw["status"] not in NOT_SCORED:
            out.append(self._upstream(d, field_key, record_key, ref, raw, symptoms))

        if auto is not None and auto["predicted"].get("outcome") == "RESOLVED" \
                and auto["status"] != "MATCH" and auto["status"] not in NOT_SCORED:
            out.append(_diag("AUTO", record_key, field_key, "RESOLUTION_FAILURE", "CONFIRMED",
                             symptoms, [{"probe": "resolution", "observed": {
                                 "accepted": auto["predicted"].get("claim_uids"),
                                 "rule": auto["predicted"].get("rule"),
                                 "status": auto["status"]}}],
                             "a claim that disagrees with the reference was accepted "
                             "automatically"))
        elif raw_ok and auto is not None and auto["predicted"].get("outcome") == "ESCALATED":
            matching = [c["claim_uid"] for c in raw["predicted"].get("candidates", [])
                        if c["status"] == "MATCH"]
            out.append(_diag("AUTO", record_key, field_key, "UNNECESSARY_ESCALATION",
                             "CONFIRMED", symptoms, [{"probe": "resolution", "observed": {
                                 "matching_candidates": matching,
                                 "reasons": auto["predicted"].get("reason_codes")}}],
                             "a candidate matching the reference existed but the rules sent "
                             "the field to review (not a failure; a coverage cost)"))

        if final is not None and final["status"] != "MATCH" \
                and final["status"] not in NOT_SCORED:
            origin = final["predicted"].get("origin")
            # A rules/gap value that differs is explained by the earlier stages' diagnoses.
            if origin in ("REVIEWER", "DERIVED") or not out:
                out.append(self._final(field_key, record_key, final, symptoms))
        return out

    def _symptoms(self, *results: dict[str, Any] | None) -> list[str]:
        tags: set[str] = set()
        for r in results:
            if not r:
                continue
            comparisons = [r["comparison"]] + [c for c in r["predicted"].get("candidates", [])]
            for c in comparisons:
                ts = c.get("tags") or []
                if c.get("status") == "TEMPORAL_MISMATCH" or "TEMPORAL" in ts:
                    tags.add("TEMPORAL_MISMATCH")
                if "SCOPE:GEOGRAPHIC_SCOPE" in ts:
                    tags.add("GEOGRAPHIC_SCOPE_MISMATCH")
                if {"SCOPE:MEASUREMENT_BASIS", "SCOPE:APPROXIMATE_VALUE"} & set(ts):
                    tags.add("MEASUREMENT_DEFINITION_MISMATCH")
                if {"VALUE_WHERE_NOT_APPLICABLE", "NO_FIELD_KEYWORDS"} & set(ts) or (
                        c.get("status") == "NEEDS_REVIEW" and c.get("coverage") == 0):
                    tags.add("FIELD_SEMANTICS_MISMATCH")
                if "INVALID_CATEGORY" in ts:
                    tags.add("INVALID_CATEGORY")
            for cand in r["predicted"].get("candidates", []):
                if "VALUE_IN_QUOTE" in (cand.get("fail_codes") or []):
                    tags.add("GROUNDING")
                for url, stype in cand.get("sources", []):
                    if suspicious_official(url, stype):
                        tags.add("SOURCE_CLASSIFICATION_ERROR")
        return sorted(tags)

    def _upstream(self, d: FieldDefinition, field_key: str, record_key: str,
                  ref: ReferenceValue, raw: dict[str, Any], symptoms: list[str]
                  ) -> dict[str, Any]:
        probes: list[dict[str, Any]] = []
        hits = self.evidence_with_value(d, ref)
        fallback = not ref.source_urls
        trail = self.source_trail(ref.source_urls or self._row_urls)
        probes.append({"probe": "evidence", "observed": {
            "chunks_with_value": hits[:20], "chunks_with_value_count": len(hits),
            "reference_sources": trail,
            "reference_sources_basis": "row (no source labelled for this field)" if fallback
            else "field"}})
        candidates = raw["predicted"].get("candidates", [])
        if d.type == "integer":
            return _diag("RAW", record_key, field_key, "UNKNOWN", "UNKNOWN", symptoms, probes,
                         "years can't be traced to evidence deterministically")
        if hits:
            bundled = self.bundled.get(field_key, {})
            in_bundle = [h for h in hits if h["chunk_id"] in bundled]
            probes.append({"probe": "retrieval", "observed": {
                "value_chunks_in_bundles": [{**h, "bundles": bundled[h["chunk_id"]]}
                                            for h in in_bundle],
                "bundles_for_field": len({b for ids in bundled.values() for b in ids}),
                "bundle_linkage": self.snap["context_bundle_basis"]}})
            if not in_bundle:
                return _diag("RAW", record_key, field_key, "RETRIEVAL_FAILURE", "PROBABLE",
                             symptoms, probes,
                             f"{len(hits)} acquired chunk(s) contain the reference value but "
                             "none was selected into the field's context (earlier bundles are "
                             "matched by time window: instrumentation gap)")
            probes.append({"probe": "extraction", "observed": {
                "candidates": [{"claim_uid": c["claim_uid"], "value": c["value"],
                                "status": c["status"]} for c in candidates]}})
            return _diag("RAW", record_key, field_key, "EXTRACTION_FAILURE",
                         self.availability_confidence(d), symptoms, probes,
                         "the value was in the evidence the model saw for this field, but no "
                         "claim carried it")
        # The value isn't in anything acquired: look at the reference's own sources.
        if not trail:
            return _diag("RAW", record_key, field_key, "EVIDENCE_UNAVAILABLE", "UNKNOWN",
                         symptoms, probes,
                         "no acquired chunk contains the reference value, and the reference "
                         "names no source for this field")
        if fallback:
            reached = [t for t in trail if t["in_discovery_candidates"] or t["document_status"]]
            if not reached:
                return _diag("RAW", record_key, field_key, "DISCOVERY_FAILURE", "PROBABLE",
                             symptoms, probes,
                             "no acquired chunk contains the reference value, and none of the "
                             "reference row's sources (no source is labelled for this field) "
                             "is among the retained discovery candidates")
            return _diag("RAW", record_key, field_key, "EVIDENCE_UNAVAILABLE", "UNKNOWN",
                         symptoms, probes,
                         "no acquired chunk contains the reference value; some of the "
                         "reference row's sources reached the run, but the reference doesn't "
                         "say which one gave this field — needs review")
        if any(t["document_status"] == "OK" for t in trail):
            ok = [t for t in trail if t["document_status"] == "OK"]
            if d.type not in ("number", "percent", "coordinate"):
                # A paraphrased reference ("Peak Demand Periods") needn't appear verbatim.
                return _diag("RAW", record_key, field_key, "UNKNOWN", "UNKNOWN", symptoms,
                             probes,
                             f"the reference's source {ok[0]['url']} was acquired; its text "
                             "doesn't contain the reference wording, which may be paraphrased "
                             "— needs review")
            return _diag("RAW", record_key, field_key, "ACQUISITION_FAILURE", "PROBABLE",
                         symptoms, probes,
                         f"the reference's source {ok[0]['url']} was fetched but its stored "
                         "text lacks the value (content not captured, e.g. a scripted page)")
        if any(t["document_status"] for t in trail):
            bad = next(t for t in trail if t["document_status"])
            return _diag("RAW", record_key, field_key, "ACQUISITION_FAILURE", "CONFIRMED",
                         symptoms, probes,
                         f"the reference's source was attempted but {bad['document_status']}")
        if any(t["in_discovery_candidates"] for t in trail):
            return _diag("RAW", record_key, field_key, "ACQUISITION_FAILURE", "CONFIRMED",
                         symptoms, probes,
                         "the reference's source was a discovery candidate but was never "
                         "fetched (acquisition budget or ranking)")
        return _diag("RAW", record_key, field_key, "DISCOVERY_FAILURE", "PROBABLE", symptoms,
                     probes,
                     "none of the reference's sources is among the retained discovery "
                     "candidates (raw search results are not stored, so 'never found' and "
                     "'found but ranked out' can't be told apart)")

    def _final(self, field_key: str, record_key: str, final: dict[str, Any],
               symptoms: list[str]) -> dict[str, Any]:
        origin = final["predicted"].get("origin")
        probes = [{"probe": "final", "observed": {
            "origin": origin, "value": final["predicted"].get("value"),
            "review_decision_ids": final["predicted"].get("review_decision_ids"),
            "calculation": final["predicted"].get("calculation")}}]
        if origin == "REVIEWER":
            return _diag("FINAL", record_key, field_key, "REVIEW_INTRODUCED_DIFFERENCE",
                         "CONFIRMED", symptoms, probes,
                         "the final value comes from a reviewer decision and differs from the "
                         "reference: a reviewer error or a reference disagreement — needs "
                         "adjudication")
        if origin == "DERIVED":
            return _diag("FINAL", record_key, field_key, "DERIVATION_FAILURE", "PROBABLE",
                         symptoms, probes,
                         "a calculated value differs from the reference; check its inputs "
                         "(an input may itself differ)")
        return _diag("FINAL", record_key, field_key, "UNKNOWN", "UNKNOWN", symptoms, probes,
                     f"the final value ({origin}) differs from the reference and no earlier "
                     "stage explains it")

def _diag(stage: str, record_key: str, field_key: str, cause: str, confidence: str,
          symptoms: list[str], probes: list[dict[str, Any]], explanation: str
          ) -> dict[str, Any]:
    return {"stage": stage, "record_key": record_key, "field_key": field_key,
            "root_cause": cause, "earliest_stage": STAGE_OF.get(cause),
            "confidence": confidence, "symptoms": symptoms, "probes": probes,
            "explanation": explanation}
