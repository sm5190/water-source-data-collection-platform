"""Task-scoped context construction.

Given one research task (purpose + target fields) and the current city state, build the
smallest evidence context that lets an LLM do that task, with every passage traceable to its
source. The manager is pure and in-memory and never calls a model: durable reuse of bundles
lives in ``context_manager/store.py`` (keyed by ``ContextManager.cache_key``), and caching of
LLM responses belongs in the LLM layer (keyed by ``ContextBundle.fingerprint``).
"""

import hashlib
import json
import math
import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

from water_source_research.observability.otel import get_tracer
from water_source_research.schemas.common import FieldResolutionStatus
from water_source_research.schemas.research import (
    CityResearchState,
    EvidenceChunk,
    EvidenceRef,
    FieldClaim,
    FieldDefinition,
)
from water_source_research.services.field_state import load_field_definitions

# Bump whenever selection behaviour changes in a way configuration doesn't capture (scoring
# formula, weights/factors below, excerpting, rendering). Stored bundles built under another
# version are never reused.
RETRIEVAL_POLICY_VERSION = "2"

class ContextPurpose(StrEnum):
    PLAN = "PLAN"
    QUERY_GENERATION = "QUERY_GENERATION"
    EXTRACT = "EXTRACT"
    RESOLVE_CONFLICT = "RESOLVE_CONFLICT"
    VALIDATE = "VALIDATE"

class ContextStatus(StrEnum):
    READY = "READY"
    NO_SUFFICIENT_EVIDENCE = "NO_SUFFICIENT_EVIDENCE"
    NOT_NEEDED = "NOT_NEEDED"  # every target was pruned (resolved, derived, nothing to do)

DEFAULT_TOKEN_BUDGETS: dict[ContextPurpose, int] = {
    ContextPurpose.PLAN: 1500,
    ContextPurpose.QUERY_GENERATION: 1500,
    ContextPurpose.EXTRACT: 6000,
    ContextPurpose.RESOLVE_CONFLICT: 4000,
    ContextPurpose.VALIDATE: 3000,
}

SOURCE_TYPE_WEIGHTS = {
    "UTILITY": 1.0,
    "GOVERNMENT": 1.0,
    "REGULATORY": 1.0,
    "TECHNICAL": 0.85,
    "ACADEMIC": 0.85,
    "OTHER": 0.6,
}
UNKNOWN_SOURCE_WEIGHT = 0.75
CITY_NOT_MENTIONED_FACTOR = 0.6
NO_DIGITS_FACTOR = 0.3
EXCERPT_THRESHOLD_TOKENS = 250
MAX_CLAIM_PASSAGE_TOKENS = 150
MIN_CLAIM_PASSAGE_TOKENS = 20

_DONE = {FieldResolutionStatus.RESOLVED, FieldResolutionStatus.NOT_APPLICABLE}
_SKIP_DONE_PURPOSES = {
    ContextPurpose.PLAN, ContextPurpose.QUERY_GENERATION, ContextPurpose.EXTRACT,
}
# Procedure guidance is planning context only; it never enters extraction/validation prompts.
_HINT_PURPOSES = {ContextPurpose.PLAN, ContextPurpose.QUERY_GENERATION}
PROCEDURE_HINT_TOKEN_BUDGET = 300
_ANCHOR_FIELDS = ("city_name", "country", "jurisdiction", "utility_name", "water_source")
_NUMERIC_TYPES = {"number", "integer", "percent", "coordinate"}
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_DIGIT = re.compile(r"\d")
_BM25_K1 = 1.2

def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / 4)

@dataclass(frozen=True, slots=True)
class ProcedureHint:
    """Advisory strategy text from procedural memory. Never evidence; never cited as S#."""

    procedure_id: str
    version: int
    text: str

@dataclass(slots=True)
class ContextTask:
    purpose: ContextPurpose
    target_fields: list[str]
    max_tokens: int | None = None
    procedure_hints: tuple[ProcedureHint, ...] = ()

@dataclass(slots=True)
class ContextItem:
    kind: str  # "fact" | "claim" | "evidence"
    text: str
    source_id: str | None = None
    score: float = 0.0
    field_keys: list[str] = field(default_factory=list)
    chunk_id: str | None = None
    tokens: int = 0

@dataclass(slots=True)
class SourceRef:
    source_id: str
    url: str
    title: str | None = None
    page_number: int | None = None
    retrieved_at: str | None = None
    passages: list[str] = field(default_factory=list)

    def label(self) -> str:
        page = f" (p. {self.page_number})" if self.page_number is not None else ""
        return f"[{self.source_id}] {self.title or self.url}\n{self.url}{page}"

@dataclass(slots=True)
class ContextBundle:
    purpose: str
    target_fields: list[str]
    items: list[ContextItem] = field(default_factory=list)
    header: str = ""
    sources: dict[str, SourceRef] = field(default_factory=dict)
    skipped_fields: dict[str, str] = field(default_factory=dict)
    stats: dict[str, int] = field(default_factory=dict)
    # Set by ContextBundleStore; not part of the serialized payload or the fingerprint.
    bundle_id: str | None = None
    cache_key: str | None = None
    cache_hit: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "purpose": self.purpose,
            "target_fields": self.target_fields,
            "items": [asdict(i) for i in self.items],
            "header": self.header,
            "sources": {sid: asdict(s) for sid, s in self.sources.items()},
            "skipped_fields": self.skipped_fields,
            "stats": self.stats,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], **ids: Any) -> "ContextBundle":
        return cls(
            purpose=data["purpose"],
            target_fields=list(data["target_fields"]),
            items=[ContextItem(**i) for i in data["items"]],
            header=data["header"],
            sources={sid: SourceRef(**s) for sid, s in data["sources"].items()},
            skipped_fields=dict(data["skipped_fields"]),
            stats=dict(data["stats"]),
            **ids,
        )

    @property
    def is_empty(self) -> bool:
        """True when no target needs work; the caller should skip the LLM call."""
        return not self.target_fields

    @property
    def uncovered_fields(self) -> list[str]:
        """EXTRACT targets that received no evidence chunk."""
        if self.purpose != ContextPurpose.EXTRACT:
            return []
        covered = {k for i in self.items if i.kind == "evidence" for k in i.field_keys}
        return [k for k in self.target_fields if k not in covered]

    @property
    def status(self) -> ContextStatus:
        if self.is_empty:
            return ContextStatus.NOT_NEEDED
        if self.purpose == ContextPurpose.EXTRACT and self.uncovered_fields == self.target_fields:
            return ContextStatus.NO_SUFFICIENT_EVIDENCE
        return ContextStatus.READY

    @property
    def consumed(self) -> set[tuple[str, str]]:
        """(chunk_id, field_key) pairs sent in this bundle; add them to ``seen`` after the call."""
        return {
            (item.chunk_id, key)
            for item in self.items
            if item.kind == "evidence" and item.chunk_id
            for key in item.field_keys
        }

    @property
    def fingerprint(self) -> str:
        """Stable hash of the rendered prompt context, usable as an LLM result cache key."""
        return hashlib.sha256(self.render().encode("utf-8")).hexdigest()

    def evidence_ref(self, source_id: str, passage: str | None = None) -> EvidenceRef:
        """Map a cited ``[S#]`` back to an EvidenceRef for a FieldClaim."""
        if source_id not in self.sources:
            raise KeyError(f"Unknown source id {source_id!r}")
        src = self.sources[source_id]
        return EvidenceRef(
            url=src.url,
            title=src.title,
            page_number=src.page_number,
            passage=passage if passage is not None else "\n".join(src.passages),
            retrieved_at=src.retrieved_at,
        )

    def render(self) -> str:
        if self.is_empty:
            return ""
        sections = [self.header]
        hints = [i.text for i in self.items if i.kind == "procedure_hint"]
        if hints:
            sections.append(
                "## Procedure guidance (advisory — prior successful strategy, NOT evidence)\n"
                + "\n".join(hints)
            )
        facts = [i.text for i in self.items if i.kind == "fact"]
        if facts:
            sections.append("## Known facts\n" + "\n".join(facts))
        claims = [i.text for i in self.items if i.kind == "claim"]
        if claims:
            sections.append("## Candidate claims\n" + "\n".join(claims))
        grouped: dict[str, list[str]] = {}
        for item in self.items:
            if item.kind == "evidence" and item.source_id:
                grouped.setdefault(item.source_id, []).append(item.text)
        if grouped:
            blocks = [
                self.sources[sid].label() + "\n" + "\n\n".join(texts)
                for sid, texts in grouped.items()
            ]
            sections.append("## Evidence\n" + "\n\n".join(blocks))
        return "\n\n".join(sections)

class _SourceRegistry:
    def __init__(self, bundle: ContextBundle) -> None:
        self.bundle = bundle
        self.by_key: dict[tuple[str, int | None], str] = {}

    def has(self, url: str, page_number: int | None) -> bool:
        return (url, page_number) in self.by_key

    def add(
        self,
        url: str,
        title: str | None,
        page_number: int | None,
        retrieved_at: str | None,
        passage: str,
    ) -> str:
        key = (url, page_number)
        sid = self.by_key.get(key)
        if sid is None:
            sid = f"S{len(self.by_key) + 1}"
            self.by_key[key] = sid
            self.bundle.sources[sid] = SourceRef(sid, url, title, page_number, retrieved_at)
        self.bundle.sources[sid].passages.append(passage)
        return sid

    def add_ref(self, ref: EvidenceRef) -> str:
        return self.add(ref.url, ref.title, ref.page_number, ref.retrieved_at, ref.passage)

class ContextManager:
    def __init__(
        self,
        field_definitions: Mapping[str, FieldDefinition] | None = None,
        token_estimator: Callable[[str], int] = estimate_tokens,
        max_chunks_per_source: int = 3,
        dedupe_threshold: float = 0.85,
        policy_version: str = RETRIEVAL_POLICY_VERSION,
        procedure_hint_budget: int = PROCEDURE_HINT_TOKEN_BUDGET,
    ) -> None:
        self.fields = dict(field_definitions or load_field_definitions())
        self.estimate = token_estimator
        self.max_chunks_per_source = max_chunks_per_source
        self.dedupe_threshold = dedupe_threshold
        self.policy_version = policy_version
        self.procedure_hint_budget = procedure_hint_budget
        self._patterns: dict[str, re.Pattern[str]] = {}
        # Everything about the manager that can change a bundle for identical inputs.
        self.policy_signature = _digest({
            "version": policy_version,
            "max_chunks_per_source": max_chunks_per_source,
            "dedupe_threshold": dedupe_threshold,
            "procedure_hint_budget": procedure_hint_budget,
            "token_estimator": f"{token_estimator.__module__}.{token_estimator.__qualname__}",
            "fields": [self.fields[k].model_dump(mode="json") for k in sorted(self.fields)],
        })

    def cache_key(
        self,
        task: ContextTask,
        state: CityResearchState,
        chunks: Sequence[EvidenceChunk | Mapping[str, Any]] = (),
        seen: Iterable[tuple[str, str]] | None = None,
    ) -> str:
        """Deterministic key over exactly the inputs ``build`` reads for this task.

        State outside the targets, anchors and their dependencies is excluded, so resolving an
        unrelated field doesn't invalidate the bundle; evidence and ``seen`` count only for
        EXTRACT, the one purpose that reads them.
        """
        purpose = ContextPurpose(task.purpose)
        self._check_fields(task.target_fields)
        targets = list(dict.fromkeys(task.target_fields))
        read = set(targets) | set(_ANCHOR_FIELDS)
        read.update(d for k in targets for d in self.fields[k].depends_on)
        payload: dict[str, Any] = {
            "policy": self.policy_signature,
            "purpose": purpose.value,
            "targets": targets,
            "budget": task.max_tokens or DEFAULT_TOKEN_BUDGETS[purpose],
            "city": state.city_name,
            "country": state.country,
            "fields": {
                k: state.fields[k].model_dump(mode="json")
                for k in sorted(read) if k in state.fields
            },
        }
        if purpose in _HINT_PURPOSES and task.procedure_hints:
            payload["procedure_hints"] = [
                [h.procedure_id, h.version, _digest(h.text)] for h in task.procedure_hints
            ]
        if purpose is ContextPurpose.EXTRACT:
            payload["evidence"] = sorted(
                _digest(_as_chunk(c).model_dump(mode="json")) for c in chunks
            )
            payload["seen"] = sorted([c, k] for c, k in set(seen or ()) if k in targets)
        return _digest(payload)

    def _check_fields(self, keys: Iterable[str]) -> None:
        unknown = [k for k in keys if k not in self.fields]
        if unknown:
            raise ValueError(f"Unknown field keys: {unknown}")

    def build(
        self,
        task: ContextTask,
        state: CityResearchState,
        chunks: Sequence[EvidenceChunk | Mapping[str, Any]] = (),
        seen: Iterable[tuple[str, str]] | None = None,
    ) -> ContextBundle:
        with get_tracer().start_as_current_span("context_manager.build") as span:
            bundle = self._build(task, state, chunks, set(seen or ()))
            span.set_attribute("context.purpose", bundle.purpose)
            span.set_attribute("context.city", state.city_name)
            span.set_attribute("context.target_fields", bundle.target_fields)
            for key, value in bundle.stats.items():
                span.set_attribute(f"context.{key}", value)
            return bundle

    def _build(
        self,
        task: ContextTask,
        state: CityResearchState,
        chunks: Sequence[EvidenceChunk | Mapping[str, Any]],
        seen: set[tuple[str, str]],
    ) -> ContextBundle:
        purpose = ContextPurpose(task.purpose)
        self._check_fields(task.target_fields)
        budget = task.max_tokens or DEFAULT_TOKEN_BUDGETS[purpose]
        targets, skipped = self._prune(purpose, task.target_fields, state)
        bundle = ContextBundle(purpose=purpose.value, target_fields=targets, skipped_fields=skipped)
        stats: Counter[str] = Counter(budget_tokens=budget)
        if not targets:
            bundle.stats = dict(stats)
            return bundle

        sources = _SourceRegistry(bundle)
        bundle.header = self._header(purpose, targets, state)
        used = self.estimate(bundle.header)

        if task.procedure_hints:
            if purpose in _HINT_PURPOSES:
                used = self._add_procedure_hints(bundle, task.procedure_hints, budget, used,
                                                 stats)
            else:
                stats["procedure_hints_ignored"] = len(task.procedure_hints)

        for item in self._facts(targets, state, sources):
            if used + item.tokens > budget:
                stats["facts_dropped_budget"] += 1
                continue
            bundle.items.append(item)
            used += item.tokens
            stats["facts"] += 1

        if purpose is ContextPurpose.EXTRACT:
            used = self._add_evidence(bundle, targets, state, chunks, seen, budget, used,
                                      sources, stats)
        elif purpose is ContextPurpose.RESOLVE_CONFLICT:
            pairs = [
                (key, claim, f"candidate {i}")
                for key in targets
                for i, claim in enumerate(_by_confidence(state.fields[key].candidate_claims), 1)
            ]
            used = self._add_claims(bundle, pairs, budget, used, sources, stats)
        elif purpose is ContextPurpose.VALIDATE:
            pairs = []
            for key in targets:
                fs = state.fields[key]
                if fs.selected_claim is not None:
                    pairs.append((key, fs.selected_claim, "selected"))
                else:
                    pairs.append((key, _by_confidence(fs.candidate_claims)[0], "top candidate"))
            used = self._add_claims(bundle, pairs, budget, used, sources, stats)

        stats["tokens_used"] = used
        bundle.stats = dict(stats)
        return bundle

    # --- target selection and framing -------------------------------------------------

    def _prune(
        self, purpose: ContextPurpose, target_fields: list[str], state: CityResearchState
    ) -> tuple[list[str], dict[str, str]]:
        targets: list[str] = []
        skipped: dict[str, str] = {}
        for key in dict.fromkeys(target_fields):
            fs = state.fields.get(key)
            status = fs.status if fs else FieldResolutionStatus.UNRESOLVED
            candidates = fs.candidate_claims if fs else []
            if purpose in _SKIP_DONE_PURPOSES and status in _DONE:
                skipped[key] = f"already {status.value}"
            elif purpose is ContextPurpose.EXTRACT and self.fields[key].derived:
                skipped[key] = "derived: computed by the rules engine"
            elif purpose is ContextPurpose.RESOLVE_CONFLICT and len(candidates) < 2:
                skipped[key] = "fewer than two candidate claims"
            elif purpose is ContextPurpose.VALIDATE and not (
                (fs and fs.selected_claim) or candidates
            ):
                skipped[key] = "no claim to validate"
            else:
                targets.append(key)
        return targets, skipped

    def _header(self, purpose: ContextPurpose, targets: list[str], state: CityResearchState) -> str:
        place = ", ".join(x for x in (state.city_name, _country(state)) if x)
        lines = [f"# Task: {purpose.value}", f"City: {place}", "## Target fields"]
        for key in targets:
            d = self.fields[key]
            kind = f"enum: {' | '.join(d.allowed)}" if d.allowed else d.type
            fs = state.fields.get(key)
            status = fs.status.value if fs else FieldResolutionStatus.UNRESOLVED.value
            n = len(fs.candidate_claims) if fs else 0
            extra = f", {n} candidate claim(s)" if n else ""
            lines.append(f"- {key} ({kind}) — status {status}{extra}")
        return "\n".join(lines)

    def _facts(
        self, targets: list[str], state: CityResearchState, sources: _SourceRegistry
    ) -> list[ContextItem]:
        wanted = list(_ANCHOR_FIELDS)
        for key in targets:
            wanted.extend(self.fields[key].depends_on)
        items = []
        for key in dict.fromkeys(wanted):
            if key in targets:
                continue
            value: Any = None
            sid = None
            fs = state.fields.get(key)
            if fs and fs.status is FieldResolutionStatus.RESOLVED and fs.selected_claim:
                value = fs.selected_claim.value
                if fs.selected_claim.evidence:
                    sid = sources.add_ref(fs.selected_claim.evidence[0])
            elif fs and fs.status is FieldResolutionStatus.NOT_APPLICABLE:
                value = "N/A"
            elif key == "city_name":
                value = state.city_name
            elif key == "country":
                value = state.country
            if value is None:
                continue
            text = f"- {key} = {value}" + (f" [{sid}]" if sid else "")
            items.append(ContextItem("fact", text, sid, 1.0, [key], tokens=self.estimate(text)))
        return items

    def _add_procedure_hints(
        self,
        bundle: ContextBundle,
        hints: tuple[ProcedureHint, ...],
        budget: int,
        used: int,
        stats: Counter[str],
    ) -> int:
        # Own sub-budget, labelled by procedure id/version, never registered as a source.
        allowance = min(self.procedure_hint_budget, max(0, budget - used))
        spent = 0
        for hint in hints:
            text = f"- [{hint.procedure_id} v{hint.version}] " + hint.text.replace("\n", " | ")
            tokens = self.estimate(text)
            if spent + tokens > allowance:
                stats["procedure_hints_dropped_budget"] += 1
                continue
            bundle.items.append(ContextItem("procedure_hint", text, None, 0.0, [], None, tokens))
            spent += tokens
            stats["procedure_hints"] += 1
        return used + spent

    # --- evidence selection (EXTRACT) -------------------------------------------------

    def _add_evidence(
        self,
        bundle: ContextBundle,
        targets: list[str],
        state: CityResearchState,
        raw_chunks: Sequence[EvidenceChunk | Mapping[str, Any]],
        seen: set[tuple[str, str]],
        budget: int,
        used: int,
        sources: _SourceRegistry,
        stats: Counter[str],
    ) -> int:
        tracer = get_tracer()
        with tracer.start_as_current_span("context.retrieval") as span:
            chunks = [_as_chunk(c) for c in raw_chunks]
            terms = {key: self._terms(key) for key in targets}
            tf = self._term_frequencies(chunks, terms)
            stats["chunks_considered"] = len(chunks)
            span.set_attribute("context.chunks_considered", len(chunks))
            span.set_attribute("context.chunks_matched", sum(1 for row in tf if row))
        if not chunks:
            return used

        with tracer.start_as_current_span("context.ranking") as span:
            scores = self._score(chunks, targets, terms, tf, state, seen)
            ranked = sorted(
                ((max(pf.values()), i) for i, pf in enumerate(scores) if pf),
                key=lambda x: (-x[0], x[1]),
            )
            # Seed pass: each target's best chunk goes first so no field is starved.
            seeds: list[int] = []
            for key in targets:
                best = max(
                    (i for _, i in ranked if key in scores[i] and i not in seeds),
                    key=lambda i: scores[i][key],
                    default=None,
                )
                if best is not None:
                    seeds.append(best)
            order = seeds + [i for _, i in ranked if i not in seeds]
            stats["chunks_irrelevant"] = len(chunks) - len(ranked)
            span.set_attribute("context.chunks_ranked", len(ranked))
            span.set_attribute("context.chunks_irrelevant", stats["chunks_irrelevant"])

        with tracer.start_as_current_span("context.dedupe") as span:
            # Ranked order, so a near-duplicate always collapses onto its best-ranked copy.
            candidates: list[tuple[int, list[str], str]] = []
            kept_hashes: set[str] = set()
            kept_shingles: list[set[str]] = []
            for i in order:
                keys = sorted(scores[i], key=lambda k: -scores[i][k])
                passage = self._passage(chunks[i].text, [t for k in keys for t in terms[k]])
                norm = " ".join(re.findall(r"\w+", passage.lower()))
                digest = hashlib.sha1(norm.encode()).hexdigest()
                shingles = _shingles(norm)
                if digest in kept_hashes or any(
                    _jaccard(shingles, s) >= self.dedupe_threshold for s in kept_shingles
                ):
                    stats["dropped_duplicate"] += 1
                    continue
                kept_hashes.add(digest)
                kept_shingles.append(shingles)
                candidates.append((i, keys, passage))
            span.set_attribute("context.dropped_duplicate", stats["dropped_duplicate"])

        with tracer.start_as_current_span("context.budgeting") as span:
            per_source: Counter[str] = Counter()
            for i, keys, passage in candidates:
                chunk = chunks[i]
                if per_source[chunk.url] >= self.max_chunks_per_source:
                    stats["dropped_source_cap"] += 1
                    continue
                cost = self.estimate(passage)
                if not sources.has(chunk.url, chunk.page_number):
                    page = f" (p. {chunk.page_number})" if chunk.page_number is not None else ""
                    cost += self.estimate(f"[S00] {chunk.title or chunk.url}\n{chunk.url}{page}")
                if used + cost > budget:
                    stats["dropped_budget"] += 1
                    continue
                sid = sources.add(chunk.url, chunk.title, chunk.page_number, chunk.retrieved_at,
                                  passage)
                bundle.items.append(ContextItem(
                    "evidence", passage, sid, round(scores[i][keys[0]], 4), keys,
                    chunk.chunk_id, cost,
                ))
                used += cost
                per_source[chunk.url] += 1
                stats["chunks_selected"] += 1
            span.set_attribute("context.budget_tokens", budget)
            span.set_attribute("context.tokens_used", used)
            span.set_attribute("context.chunks_selected", stats["chunks_selected"])
            span.set_attribute("context.dropped_budget", stats["dropped_budget"])
            span.set_attribute("context.dropped_source_cap", stats["dropped_source_cap"])
        return used

    def _term_frequencies(
        self, chunks: list[EvidenceChunk], terms: dict[str, list[str]]
    ) -> list[dict[str, int]]:
        vocab = {t for ts in terms.values() for t in ts}
        return [
            {t: n for t in vocab if (n := len(self._pattern(t).findall(c.text.lower())))}
            for c in chunks
        ]

    def _score(
        self,
        chunks: list[EvidenceChunk],
        targets: list[str],
        terms: dict[str, list[str]],
        tf: list[dict[str, int]],
        state: CityResearchState,
        seen: set[tuple[str, str]],
    ) -> list[dict[str, float]]:
        """Per-chunk, per-field relevance: BM25-style term saturation x IDF x priors."""
        df = Counter(t for row in tf for t in row)
        n_docs = len(chunks)
        idf = {t: math.log(1 + (n_docs - df[t] + 0.5) / (df[t] + 0.5)) for t in df}
        city = self._pattern(state.city_name.lower())

        out = []
        for i, chunk in enumerate(chunks):
            prior = SOURCE_TYPE_WEIGHTS.get((chunk.source_type or "").upper(),
                                            UNKNOWN_SOURCE_WEIGHT)
            if not city.search(chunk.text.lower()):
                prior *= CITY_NOT_MENTIONED_FACTOR
            has_digits = bool(_DIGIT.search(chunk.text))
            per_field: dict[str, float] = {}
            for key in targets:
                if (chunk.chunk_id, key) in seen:
                    continue
                score = sum(
                    idf[t] * n / (n + _BM25_K1)
                    for t in terms[key] if (n := tf[i].get(t))
                )
                if not score:
                    continue
                if self.fields[key].type in _NUMERIC_TYPES and not has_digits:
                    score *= NO_DIGITS_FACTOR
                per_field[key] = score * prior
            out.append(per_field)
        return out

    def _terms(self, key: str) -> list[str]:
        d = self.fields[key]
        return [k.lower() for k in d.keywords] or key.split("_")

    def _pattern(self, term: str) -> re.Pattern[str]:
        if term not in self._patterns:
            self._patterns[term] = re.compile(rf"(?<!\w){re.escape(term)}(?!\w)")
        return self._patterns[term]

    def _passage(self, text: str, terms: list[str]) -> str:
        """Long chunks are reduced to verbatim matching sentences (+/-1 neighbour)."""
        if self.estimate(text) <= EXCERPT_THRESHOLD_TOKENS:
            return text
        sentences = [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]
        patterns = [self._pattern(t) for t in terms]
        hits = {
            i for i, s in enumerate(sentences)
            if any(p.search(s.lower()) for p in patterns)
        }
        if not hits:
            return text
        keep = sorted({j for i in hits for j in (i - 1, i, i + 1) if 0 <= j < len(sentences)})
        parts = ["…"] if keep[0] > 0 else []
        for prev, j in zip([None, *keep], keep):
            if prev is not None and j != prev + 1:
                parts.append("…")
            parts.append(sentences[j])
        if keep[-1] < len(sentences) - 1:
            parts.append("…")
        return " ".join(parts)

    # --- claims (RESOLVE_CONFLICT / VALIDATE) -----------------------------------------

    def _add_claims(
        self,
        bundle: ContextBundle,
        pairs: list[tuple[str, FieldClaim, str]],
        budget: int,
        used: int,
        sources: _SourceRegistry,
        stats: Counter[str],
    ) -> int:
        """Every claim is always included; passages shrink to share the remaining budget."""
        overhead = [self.estimate(_claim_line(k, c, label)) for k, c, label in pairs]
        n_refs = sum(len(c.evidence) for _, c, _ in pairs)
        remaining = budget - used - sum(overhead)
        cap = MAX_CLAIM_PASSAGE_TOKENS
        if n_refs:
            cap = max(MIN_CLAIM_PASSAGE_TOKENS, min(cap, remaining // n_refs - 4))
        for (key, claim, label), base in zip(pairs, overhead):
            lines = [_claim_line(key, claim, label)]
            for ref in claim.evidence:
                passage = _truncate(ref.passage, cap * 4)
                sid = sources.add(ref.url, ref.title, ref.page_number, ref.retrieved_at, passage)
                lines.append(f'    [{sid}] "{passage}"')
            text = "\n".join(lines)
            tokens = self.estimate(text)
            bundle.items.append(ContextItem("claim", text, None, claim.confidence, [key],
                                            tokens=tokens))
            used += tokens
            stats["claims"] += 1
        if used > budget:
            stats["over_budget"] = 1
        return used

def _as_chunk(c: EvidenceChunk | Mapping[str, Any]) -> EvidenceChunk:
    return c if isinstance(c, EvidenceChunk) else EvidenceChunk.model_validate(c)

def _digest(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

def _country(state: CityResearchState) -> str | None:
    fs = state.fields.get("country")
    if fs and fs.status is FieldResolutionStatus.RESOLVED and fs.selected_claim:
        return str(fs.selected_claim.value)
    return state.country

def _by_confidence(claims: list[FieldClaim]) -> list[FieldClaim]:
    return sorted(claims, key=lambda c: -c.confidence)

def _claim_line(key: str, claim: FieldClaim, label: str) -> str:
    return (
        f"- {key} {label}: {claim.value!r} (origin {claim.value_origin.value}, "
        f"status {claim.value_status.value}, confidence {claim.confidence:.2f})"
    )

def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars].rsplit(" ", 1)[0]
    return cut + " …"

def _shingles(norm: str, size: int = 5) -> set[str]:
    words = norm.split()
    if len(words) <= size:
        return {norm}
    return {" ".join(words[i:i + size]) for i in range(len(words) - size + 1)}

def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)
