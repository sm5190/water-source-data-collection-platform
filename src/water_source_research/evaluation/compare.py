"""Deterministic, field-aware comparison of a predicted value with a reference value.

Comparison *outcomes* only; causes are diagnosed separately (evaluation/diagnosis.py).

Statuses:
  MATCH, MISMATCH, PARTIAL_MATCH, MISSING_PREDICTION, MISSING_REFERENCE, TEMPORAL_MISMATCH,
  SCOPE_MISMATCH, NOT_COMPARABLE, NEEDS_REVIEW

Rules (policy version in evaluation/policy.py):
  numbers      canonical units, relative tolerance; differing known years -> TEMPORAL_MISMATCH,
               a claim flagged for scope/measurement definition -> SCOPE_MISMATCH
  years        exact
  categories   canonical allowed values; anything else is invalid
  entities     normalized names and configured aliases; word containment is only a partial
               match, and string similarity never makes a match
  descriptions concept coverage -> PARTIAL_MATCH at best, otherwise NEEDS_REVIEW (a person
               judges free text)
  coordinates  great-circle distance against a threshold
  lists        sets of normalized entries: matched / missing / extra
"""

import math
import re
from typing import Any, Literal
from pydantic import BaseModel, Field
from water_source_research.schemas.research import FieldDefinition
from water_source_research.services.normalization import close
from water_source_research.services.resolution import name_key
from .policy import DESCRIPTIVE_FIELDS, LIST_FIELDS, ComparePolicy
from .reference import ReferenceValue

Status = Literal["MATCH", "MISMATCH", "PARTIAL_MATCH", "MISSING_PREDICTION",
                 "MISSING_REFERENCE", "TEMPORAL_MISMATCH", "SCOPE_MISMATCH", "NOT_COMPARABLE",
                 "NEEDS_REVIEW", "NOT_RESEARCHED"]
# Statuses that count as correct / as a comparable outcome in metrics. NOT_RESEARCHED: the run
# researched a field subset that excludes this field (and produced nothing for it).
CORRECT = frozenset({"MATCH"})
NOT_SCORED = frozenset({"MISSING_REFERENCE", "NOT_COMPARABLE", "NOT_RESEARCHED"})

class Prediction(BaseModel):
    """A value produced by the pipeline at some stage (a claim, a resolution, an export)."""

    value: Any = None
    value_status: Literal["KNOWN", "UNKNOWN", "NOT_APPLICABLE", "NONE"] = "KNOWN"
    reference_year: int | None = None  # year stated with the value (quote/passage only)
    candidate_years: list[int] = Field(default_factory=list)  # years in its quote
    flags: list[str] = Field(default_factory=list)  # claim validation WARN/FAIL codes
    claim_uid: str | None = None

class Comparison(BaseModel):
    status: Status
    detail: str | None = None
    relative_error: float | None = None
    ratio: float | None = None  # predicted / reference
    distance_km: float | None = None
    coverage: float | None = None  # descriptive concept coverage
    matched: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    extra: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)  # TEMPORAL, SCOPE, VALUE_WHERE_NA, ...

def compare(d: FieldDefinition, ref: ReferenceValue | None, pred: Prediction | None,
            policy: ComparePolicy, other_lat: float | None = None) -> Comparison:
    """Compare one prediction (None = nothing predicted) with one reference value.
    ``other_lat`` (the reference latitude) scales longitude distances."""
    if ref is None or ref.value_status in ("UNKNOWN", "BLANK"):
        return Comparison(status="MISSING_REFERENCE",
                          detail="the reference has no value (Unknown/blank)")
    if ref.value_status == "PLACEHOLDER":
        return Comparison(status="NOT_COMPARABLE",
                          detail=f"reference cell is a placeholder ({ref.raw!r}), not a value")
    if any(i.startswith("INVALID") for i in ref.issues):
        return Comparison(status="NOT_COMPARABLE", tags=["REFERENCE_INVALID"],
                          detail="the reference value failed the field's own rules")
    has_pred = pred is not None and pred.value_status == "KNOWN" and pred.value not in (None, "")
    if ref.value_status == "NOT_APPLICABLE":
        if pred is None or pred.value_status in ("NOT_APPLICABLE", "NONE"):
            return Comparison(status="MATCH", detail="both: not applicable / nothing reported",
                              tags=["NOT_APPLICABLE"])
        if not has_pred:  # an explicit "Unknown" is not "N/A" (applies but not found)
            return Comparison(status="MISMATCH", tags=["UNKNOWN_WHERE_NOT_APPLICABLE"],
                              detail="predicted Unknown where the reference is N/A")
        return Comparison(status="MISMATCH", tags=["VALUE_WHERE_NOT_APPLICABLE"],
                          detail=f"predicted {pred.value!r} where the reference is N/A")
    if ref.value_status == "NONE_REPORTED":
        if not has_pred or (isinstance(pred.value, str)
                            and name_key(pred.value) == "none reported"):
            return Comparison(status="MATCH", tags=["NONE_REPORTED"])
        return Comparison(status="NEEDS_REVIEW", tags=["VALUE_WHERE_NONE_REPORTED"],
                          detail="the reference reports no pattern; the prediction describes one")
    if not has_pred:
        if pred is not None and pred.value_status == "NOT_APPLICABLE":
            return Comparison(status="MISMATCH", tags=["NOT_APPLICABLE_WHERE_VALUE"],
                              detail="predicted N/A where the reference has a value")
        return Comparison(status="MISSING_PREDICTION")
    assert pred is not None
    expected = ref.normalized
    if d.key in LIST_FIELDS or d.multi_valued:
        return compare_sets(_as_list(expected), _as_list(pred.value), policy)
    if d.type == "coordinate":
        return _coordinate(d, float(expected), pred, policy, other_lat)
    if d.type == "integer":
        return _integer(expected, pred)
    if d.type in ("number", "percent"):
        return _number(d, float(expected), pred, ref, policy)
    if d.allowed:
        return _category(d, expected, pred)
    if d.key in DESCRIPTIVE_FIELDS or d.descriptive:
        return _description(d, str(expected), str(pred.value))
    return _entity(str(expected), str(pred.value), policy)

# --- numbers ----------------------------------------------------------------------------------

def _number(d: FieldDefinition, expected: float, pred: Prediction, ref: ReferenceValue,
            policy: ComparePolicy) -> Comparison:
    try:
        got = float(pred.value)
    except (TypeError, ValueError):
        return Comparison(status="MISMATCH", tags=["NOT_A_NUMBER"],
                          detail=f"{pred.value!r} is not a number")
    tol = policy.tolerance(d.key)
    rel = abs(got - expected) / abs(expected) if expected else (0.0 if got == 0 else math.inf)
    ratio = got / expected if expected else None
    base = {"relative_error": round(rel, 6) if math.isfinite(rel) else None,
            "ratio": round(ratio, 6) if ratio is not None else None}
    tags: list[str] = []
    years = _years(pred)
    if ref.reference_year and years and ref.reference_year not in years:
        tags.append("TEMPORAL")
    scope = sorted(set(pred.flags) & set(policy.scope_flags))
    tags += [f"SCOPE:{f}" for f in scope]
    if close(got, expected, tol) or (expected == 0 and got == 0):
        return Comparison(status="MATCH", tags=tags, **base,
                          detail=f"{got:g} vs {expected:g} (tolerance {tol:.0%})")
    detail = f"{got:g} vs {expected:g} ({rel:+.1%} off)"
    if "TEMPORAL" in tags:
        return Comparison(status="TEMPORAL_MISMATCH", tags=tags, **base,
                          detail=f"{detail}; prediction years {years} vs reference "
                                 f"{ref.reference_year}")
    if scope:
        return Comparison(status="SCOPE_MISMATCH", tags=tags, **base,
                          detail=f"{detail}; claim flagged {', '.join(scope)}")
    return Comparison(status="MISMATCH", tags=tags, **base, detail=detail)

def _years(pred: Prediction) -> list[int]:
    if pred.reference_year:
        return [pred.reference_year]
    return sorted(set(pred.candidate_years))

def _integer(expected: Any, pred: Prediction) -> Comparison:
    try:
        got = int(float(pred.value))
    except (TypeError, ValueError):
        return Comparison(status="MISMATCH", tags=["NOT_A_NUMBER"])
    if got == int(expected):
        return Comparison(status="MATCH")
    return Comparison(status="MISMATCH", detail=f"{got} vs {int(expected)}")

# --- categories, entities, descriptions ---------------------------------------------------------

def _category(d: FieldDefinition, expected: str, pred: Prediction) -> Comparison:
    canonical = {a.casefold(): a for a in d.allowed}
    got = canonical.get(" ".join(str(pred.value).split()).casefold())
    if got is None:
        return Comparison(status="MISMATCH", tags=["INVALID_CATEGORY"],
                          detail=f"{pred.value!r} is not one of {', '.join(d.allowed)}")
    if got == expected:
        return Comparison(status="MATCH")
    return Comparison(status="MISMATCH", detail=f"{got} vs {expected}")

def entity_keys(name: str, policy: ComparePolicy) -> set[str]:
    """The normalized name plus every configured alias of it (both directions)."""
    key = name_key(name)
    out = {key}
    for canonical, aliases in policy.aliases.items():
        group = {name_key(canonical), *(name_key(a) for a in aliases)}
        if key in group:
            out |= group
    return out

def _entity(expected: str, got: str, policy: ComparePolicy) -> Comparison:
    ref_keys, pred_keys = entity_keys(expected, policy), entity_keys(got, policy)
    if ref_keys & pred_keys:
        aliased = name_key(expected) != name_key(got)
        return Comparison(status="MATCH", tags=["ALIAS"] if aliased else [])
    rw, pw = set(name_key(expected).split()), set(name_key(got).split())
    if rw and pw and (rw <= pw or pw <= rw):
        return Comparison(status="PARTIAL_MATCH", tags=["NAME_CONTAINMENT"],
                          detail=f"{got!r} vs {expected!r}: one name contains the other")
    return Comparison(status="MISMATCH", detail=f"{got!r} vs {expected!r}")

_STEM = re.compile(r"(ing|ed|es|s)$")
_STOP = frozenset({"the", "and", "of", "for", "to", "in", "a", "an", "with", "on", "at", "by"})

def _concept_words(text: str) -> list[str]:
    words = [w for w in re.findall(r"[a-z0-9]+", text.casefold()) if w not in _STOP]
    return [_STEM.sub("", w) if len(w) > 4 else w for w in words]

def _description(d: FieldDefinition, expected: str, got: str) -> Comparison:
    """Concept coverage only; free-text agreement is a person's call (never an automatic
    MATCH)."""
    concepts = [c.strip() for c in re.split(r"[;,]", expected) if c.strip()]
    got_words = set(_concept_words(got))
    covered = [c for c in concepts if (w := _concept_words(c)) and set(w) <= got_words]
    coverage = len(covered) / len(concepts) if concepts else 0.0
    missing = [c for c in concepts if c not in covered]
    tags = []
    own = {w for k in d.keywords for w in _concept_words(k)}
    if own and not own & got_words:
        tags.append("NO_FIELD_KEYWORDS")  # a hint of field-semantics mismatch
    if covered:
        return Comparison(status="PARTIAL_MATCH", coverage=round(coverage, 3),
                          matched=covered, missing=missing, tags=tags,
                          detail=f"{len(covered)}/{len(concepts)} reference concepts present")
    return Comparison(status="NEEDS_REVIEW", coverage=0.0, missing=missing, tags=tags,
                      detail="no reference concept found in the prediction")

def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str):
        return [p.strip() for p in value.split(",") if p.strip()]
    return [] if value is None else [str(value)]

def compare_sets(expected: list[str], got: list[str], policy: ComparePolicy) -> Comparison:
    """Several legitimate values: matched / missing / extra entries (never a contradiction)."""
    remaining = list(got)
    matched, missing = [], []
    for e in expected:
        keys = entity_keys(e, policy)
        hit = next((g for g in remaining if entity_keys(g, policy) & keys), None)
        if hit is None:
            missing.append(e)
        else:
            matched.append(e)
            remaining.remove(hit)
    status: Status = "MATCH" if not missing and not remaining else (
        "PARTIAL_MATCH" if matched else "MISMATCH")
    return Comparison(status=status, matched=matched, missing=missing, extra=remaining,
                      coverage=round(len(matched) / len(expected), 3) if expected else None,
                      detail=f"{len(matched)}/{len(expected)} matched, {len(remaining)} extra")

# --- coordinates ------------------------------------------------------------------------------

def _coordinate(d: FieldDefinition, expected: float, pred: Prediction, policy: ComparePolicy,
                other_lat: float | None) -> Comparison:
    try:
        got = float(pred.value)
    except (TypeError, ValueError):
        return Comparison(status="MISMATCH", tags=["NOT_A_NUMBER"])
    limit = 90 if d.key.endswith("latitude") else 180
    if not -limit <= got <= limit:
        return Comparison(status="MISMATCH", tags=["OUT_OF_RANGE"])
    km_per_degree = 111.32
    if d.key.endswith("longitude"):
        km_per_degree *= math.cos(math.radians(other_lat if other_lat is not None else 0.0))
    distance = abs(got - expected) * km_per_degree
    threshold = policy.point_km(d.key)
    status: Status = "MATCH" if distance <= threshold else "MISMATCH"
    tags = ["SIGN"] if got * expected < 0 and abs(abs(got) - abs(expected)) < 0.01 else []
    return Comparison(status=status, distance_km=round(distance, 3), tags=tags,
                      detail=f"{distance:.2f} km along this axis (threshold {threshold:g} km)")

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0088 * 2 * math.asin(math.sqrt(a))
