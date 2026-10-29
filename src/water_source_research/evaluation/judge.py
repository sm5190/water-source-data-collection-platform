"""Semantic judge: does an automated value mean the same as the manual reference value?

Used only where the deterministic comparison can't decide (evaluation/compare.py):
descriptive fields, and names (utility, water source, ...) that are neither identical nor
configured aliases. Numbers, years, categories and coordinates are never judged, and nothing the
hard rules decided is sent here: a different reporting year (TEMPORAL), scope or measurement
flags (SCOPE), and N/A / Unknown / value differences stay as the rules set them.

The model (via ``llm/router.py``, the configured provider, no fallback) returns a strict
structure: a verdict (EQUIVALENT / PARTIAL_OVERLAP / DIFFERENT / INSUFFICIENT_CONTEXT), the
facts that overlap, are missing or differ, three dimension checks (same meaning of the measure,
same geographic scope, same reporting period) and a short justification. Caps are applied
deterministically afterwards (``map_verdict``). Every judgment is stored in ``eval_judgments``
with its inputs, model and prompt version (cache + audit); re-evaluations reuse it. A judgment
is provisional until a person verifies it (adjudication). The judge is not assumed correct: it
is checked against manually labelled Toronto cases (scripts/judge_calibration.py) and its
accuracy must not be claimed until those labels are confirmed and the check passes.
"""

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal
import yaml
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from water_source_research.core.paths import CONFIG_DIR
from water_source_research.db.evaluation_models import EvalJudgmentRecord
from water_source_research.llm.base import LLMError

JUDGE_PROMPT_VERSION = "judge-2"
DESCRIPTIONS = CONFIG_DIR / "field_descriptions.yaml"
# Fields the judge may see: free text, and names of things.
JUDGED_FIELDS = frozenset({
    "seasonal_uses", "infrastructure_notes", "water_quality_notes",
    "past_total_city_water_withdrawals", "city_name", "jurisdiction", "country",
    "utility_name", "water_source", "water_system", "water_transfer_system", "reservoir_names",
})
DESCRIPTIVE = frozenset({"seasonal_uses", "infrastructure_notes", "water_quality_notes",
                         "past_total_city_water_withdrawals"})
# Deterministic statuses the judge may refine. Everything else (MATCH, the hard-rule
# mismatches, missing values, not comparable) is final without it.
JUDGEABLE = {"descriptive": frozenset({"NEEDS_REVIEW", "PARTIAL_MATCH"}),
             "name": frozenset({"PARTIAL_MATCH", "MISMATCH"})}
# Mismatches the rules decided for a reason the judge must not overturn.
HARD_TAGS = frozenset({"VALUE_WHERE_NOT_APPLICABLE", "UNKNOWN_WHERE_NOT_APPLICABLE",
                       "NOT_APPLICABLE_WHERE_VALUE", "VALUE_WHERE_NONE_REPORTED",
                       "INVALID_CATEGORY", "NOT_A_NUMBER", "TEMPORAL", "REFERENCE_INVALID"})

Tri = Literal["yes", "no", "unclear", "not_applicable"]
Verdict = Literal["EQUIVALENT", "PARTIAL_OVERLAP", "DIFFERENT", "INSUFFICIENT_CONTEXT"]

class Judgment(BaseModel):
    """What the model must return (nothing else)."""

    model_config = ConfigDict(extra="forbid")
    verdict: Verdict
    overlapping_facts: list[str] = Field(default_factory=list, max_length=12)
    missing_facts: list[str] = Field(default_factory=list, max_length=12,
                                     description="in the manual value, absent from the automated")
    differing_facts: list[str] = Field(default_factory=list, max_length=12,
                                       description="stated differently or contradicted")
    same_meaning_of_measure: Tri
    same_geographic_scope: Tri
    same_reporting_period: Tri
    justification: str = Field(max_length=300)

VERDICT_STATUS = {"EQUIVALENT": "MATCH", "PARTIAL_OVERLAP": "PARTIAL_MATCH",
                  "DIFFERENT": "MISMATCH", "INSUFFICIENT_CONTEXT": "NEEDS_REVIEW"}
_SEVERITY = {"MATCH": 0, "PARTIAL_MATCH": 1, "NEEDS_REVIEW": 2, "MISMATCH": 3}

def map_verdict(j: Judgment) -> str:
    """Comparison status from a judgment, with deterministic caps: any dimension answered
    "no" -> at best MISMATCH; "unclear" -> at best NEEDS_REVIEW; EQUIVALENT with differing
    facts -> PARTIAL_MATCH. The worst applicable status wins."""
    status = VERDICT_STATUS[j.verdict]
    dims = (j.same_meaning_of_measure, j.same_geographic_scope, j.same_reporting_period)
    caps = []
    if "no" in dims:
        caps.append("MISMATCH")
    if "unclear" in dims:
        caps.append("NEEDS_REVIEW")
    if j.verdict == "EQUIVALENT" and j.differing_facts:
        caps.append("PARTIAL_MATCH")
    return max([status, *caps], key=_SEVERITY.__getitem__)

class JudgeInput(BaseModel):
    field_key: str
    definition: str
    manual_value: str
    manual_year: int | None = None
    automated_value: str
    automated_year: int | None = None
    automated_quotes: list[str] = Field(default_factory=list)

    def cache_key(self, provider: str, model: str) -> str:
        raw = json.dumps({"v": JUDGE_PROMPT_VERSION, "provider": provider, "model": model,
                          **self.model_dump()}, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

SYSTEM = """You check a water-supply dataset. For one field of one city you get the value a person \
researched by hand (MANUAL) and the value software extracted (AUTOMATED), with the quote the \
software relied on. Decide whether AUTOMATED means the same as MANUAL for this field.

Rules:
- Use the field definition strictly. A value that describes something the definition excludes \
is DIFFERENT, however similar the words.
- A city population is not a metropolitan or service-area population. A treated-water quality \
is not a raw-water quality. Wastewater or sewer infrastructure is not drinking-water \
infrastructure. A historical estimate is not a current reported value.
- Different reporting years are different periods. "Unknown" and "N/A" are different answers.
- Names: the same organization or water body written differently (word order, official vs common \
name, a division of the same city utility) is EQUIVALENT only if that is evident from the values \
or the quote. Do not guess an identity you cannot see.
- EQUIVALENT: same meaning, nothing missing or different. PARTIAL_OVERLAP: some facts agree, \
others are missing or different. DIFFERENT: they do not describe the same thing. \
INSUFFICIENT_CONTEXT: you cannot tell from what you were given.
- List the facts that overlap, the MANUAL facts missing from AUTOMATED, and facts stated \
differently. Keep each fact short. Justification: one or two plain sentences.
- Dimensions (yes / no / unclear / not_applicable):
  same_meaning_of_measure: do both values describe the kind of thing the field asks for, in the \
same sense (e.g. both drinking-water infrastructure, both raw-water quality, both a city \
population)? Extra, missing or more detailed facts do NOT make this "no"; describing a \
different kind of thing does.
  same_geographic_scope: do both refer to the same place (the city, or this water source)? \
not_applicable if neither value has a geographic scope of its own.
  same_reporting_period: do both refer to the same year or period? not_applicable if neither \
value states or implies a period."""

def user_prompt(x: JudgeInput) -> str:
    quotes = "\n".join(f"- \"{q[:600]}\"" for q in x.automated_quotes[:3]) or "- (none)"
    year = lambda y: f" (year: {y})" if y else ""
    return (f"FIELD: {x.field_key}\nDEFINITION: {x.definition}\n\n"
            f"MANUAL: {x.manual_value}{year(x.manual_year)}\n"
            f"AUTOMATED: {x.automated_value}{year(x.automated_year)}\n"
            f"AUTOMATED QUOTE(S):\n{quotes}")

def load_descriptions(path: Path = DESCRIPTIONS) -> dict[str, str]:
    return dict(yaml.safe_load(path.read_text(encoding="utf-8"))["fields"])

class JudgmentStore:
    """eval_judgments: one row per distinct (prompt version, provider, model, inputs)."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def get(self, key: str) -> dict[str, Any] | None:
        with Session(self.engine) as session:
            row = session.scalar(select(EvalJudgmentRecord)
                                 .where(EvalJudgmentRecord.cache_key == key))
            return _judgment_dict(row) if row else None

    def put(self, key: str, x: JudgeInput, j: Judgment, status: str, provider: str,
            model: str) -> dict[str, Any]:
        try:
            with Session(self.engine) as session, session.begin():
                row = EvalJudgmentRecord(
                    cache_key=key, field_key=x.field_key, prompt_version=JUDGE_PROMPT_VERSION,
                    provider=provider, model=model, inputs=x.model_dump(mode="json"),
                    output=j.model_dump(mode="json"), verdict=j.verdict, status=status)
                session.add(row)
                session.flush()
                return _judgment_dict(row)
        except IntegrityError:  # another evaluation stored the same judgment first
            stored = self.get(key)
            assert stored is not None
            return stored

class SemanticJudge:
    """Judge with a persistent cache. ``llm`` is a structured LLM from the router (or a test
    double with ``structured(system, user, schema)``, ``provider`` and ``model``); ``None``
    means unavailable (every request returns None, reason in ``unavailable``)."""

    def __init__(self, llm: Any, store: JudgmentStore,
                 descriptions: Mapping[str, str] | None = None,
                 unavailable: str | None = None, cache_only: bool = False) -> None:
        self.llm = llm
        # cache_only: stored verdicts only, never a model call (reproducible comparisons);
        # a cell without a stored verdict needs a person, like an unavailable judge.
        self.cache_only = cache_only
        self.cache_misses = 0
        self.store = store
        self.descriptions = dict(descriptions or load_descriptions())
        self.unavailable = unavailable if llm is None else None
        self.calls = 0
        self.errors: list[str] = []

    @property
    def provider(self) -> str:
        return getattr(self.llm, "provider", "none")

    @property
    def model(self) -> str:
        return getattr(self.llm, "model", "none")

    def make_input(self, field_key: str, manual: str, manual_year: int | None, automated: str,
                   automated_year: int | None, quotes: list[str]) -> JudgeInput:
        return JudgeInput(field_key=field_key, definition=self.descriptions.get(field_key, ""),
                          manual_value=manual, manual_year=manual_year,
                          automated_value=automated, automated_year=automated_year,
                          automated_quotes=quotes)

    def judge(self, x: JudgeInput) -> dict[str, Any] | None:
        """The stored judgment for ``x`` (from the cache, or asked now); None if the model is
        unavailable or failed (the cell then needs a person; failures are not cached)."""
        if self.llm is None:
            return None
        from water_source_research.observability.otel import get_tracer
        with get_tracer().start_as_current_span("evaluation.judge") as span:
            span.set_attribute("research.field_key", x.field_key)
            key = x.cache_key(self.provider, self.model)
            cached = self.store.get(key)
            span.set_attribute("evaluation.judge_cached", cached is not None)
            if cached is not None:
                return {**cached, "cached": True}
            if self.cache_only:
                self.cache_misses += 1
                span.set_attribute("evaluation.judge_cache_miss", True)
                return None
            from water_source_research.observability.llm_telemetry import llm_call_context
            try:
                self.calls += 1
                with llm_call_context(field_key=x.field_key, task_kind="eval_judge"):
                    j = self.llm.structured(SYSTEM, user_prompt(x), Judgment)
            except LLMError as exc:
                self.errors.append(f"{exc.kind.value}: {exc}"[:300])
                span.set_attribute("evaluation.judge_error", exc.kind.value)
                return None
            out = self.store.put(key, x, j, map_verdict(j), self.provider, self.model)
            span.set_attribute("evaluation.verdict", str(out.get("verdict") or ""))
            return {**out, "cached": False}

def build_judge(engine: Engine, settings: Any = None, cache_only: bool = False
                ) -> SemanticJudge:
    """The judge on the configured LLM provider; unavailable (not an error) without
    credentials, so evaluation still completes with those cells marked for verification.
    ``cache_only``: only stored verdicts for this provider/model are used; the model is never
    called (building the client makes no request)."""
    from water_source_research.core.config import get_settings
    from water_source_research.llm.router import build_structured_llm
    settings = settings or get_settings()
    try:
        llm = build_structured_llm(settings, task="eval_judge",
                                   prompt_version=JUDGE_PROMPT_VERSION)
    except LLMError as exc:
        return SemanticJudge(None, JudgmentStore(engine), unavailable=str(exc)[:200])
    return SemanticJudge(llm, JudgmentStore(engine), cache_only=cache_only)

def _judgment_dict(r: EvalJudgmentRecord) -> dict[str, Any]:
    return {"judgment_id": r.id, "cache_key": r.cache_key, "field_key": r.field_key,
            "prompt_version": r.prompt_version, "provider": r.provider, "model": r.model,
            "inputs": r.inputs, "output": r.output, "verdict": r.verdict, "status": r.status,
            "created_at": r.created_at.isoformat() if r.created_at else None}

# --- applying the judge to an evaluation's results ------------------------------------------

def judge_kind(field_key: str) -> str | None:
    if field_key not in JUDGED_FIELDS:
        return None
    return "descriptive" if field_key in DESCRIPTIVE else "name"

def eligible(r: Mapping[str, Any]) -> bool:
    """A result the judge may refine (see the module docstring)."""
    kind = judge_kind(r["field_key"])
    cmp = r.get("comparison") or {}
    if kind is None or r["status"] not in JUDGEABLE[kind]:
        return False
    tags = set(cmp.get("tags") or [])
    return not (tags & HARD_TAGS or any(t.startswith("SCOPE:") for t in tags))
