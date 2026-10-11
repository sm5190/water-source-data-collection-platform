"""Trajectory -> candidate Recipe.

The deterministic V1 distiller only restates what the trajectory shows the system did
successfully, so it cannot invent history. An LLM distiller may implement the same
``Distiller`` protocol later; its output must pass the same schema, grounding and safety
checks before it can be persisted.

flagging------need LLM distiller layer!

SearXNG
OpenAlex
Crossref
Semantic Scholar
"""

import re
from collections import Counter
from collections.abc import Mapping
from typing import Protocol
from water_source_research.core.capabilities import DISCOVERY_CAPABILITIES, Capability
from water_source_research.core.versions import DISTILLER_VERSION
from water_source_research.schemas.events import EventKind, EventResult
from water_source_research.schemas.procedures import Recipe, RecipeStep, Trajectory
from water_source_research.schemas.research import FieldDefinition
from .config import GENERIC_CONSTRAINTS, ProcedureMemorySettings
from .trajectory import template_query

class DistillationError(Exception):
    pass

class Distiller(Protocol):
    version: str

    def distill(self, trajectory: Trajectory) -> Recipe: ...

# Words a reusable query may contain besides placeholders and the field type's keywords.
# Anything else (proper nouns, plant names, years, domains) marks the query as too specific.
_GENERIC_QUERY_WORDS = """
a an and the of in for to by on per vs total annual yearly daily average report reports
water drinking utility municipal city public official government regulatory filing filings
permit permits licence license data statistics pdf plan master supply supplies production
consumption demand withdrawal withdrawals abstraction capacity volume volumes system systems
source sources treatment plant plants intake reservoir reservoirs dam aquifer groundwater surface
wells infrastructure department board authority works quality census population served service
area map historical trend mgd mld m3 m3/day cubic metres meters litres liters million gallons
"""
GENERIC_QUERY_VOCAB = frozenset(_GENERIC_QUERY_WORDS.split())

STEP_REASONS = {
    Capability.SOURCE_DISCOVERY: "Web discovery found sources that led to accepted evidence.",
    Capability.SCHOLARLY_DISCOVERY: "Scholarly discovery found sources that led to accepted "
                                    "evidence.",
    Capability.STATIC_DOCUMENT_FETCH: "Direct HTTP/PDF retrieval was sufficient; try it before "
                                      "expensive dynamic scraping.",
    Capability.DOCUMENT_PARSING: "Document parsing extracted the needed tables/text.",
    Capability.DYNAMIC_DOCUMENT_ACQUISITION: "Dynamic acquisition was needed for this source "
                                             "class.",
    Capability.INTERACTIVE_PORTAL_ACQUISITION: "An interactive portal held the evidence.",
}

def is_generic_query(templated: str, vocab: frozenset[str]) -> bool:
    if "{" not in templated:
        return False
    for token in templated.split():
        if re.fullmatch(r"\{\w+\}", token):
            continue
        word = token.strip("\"'(),.;:").lower()
        if word and word not in vocab:
            return False
    return True

def query_vocab(field_definitions: Mapping[str, FieldDefinition], field_type: str) -> frozenset[str]:
    words = set(GENERIC_QUERY_VOCAB)
    for d in field_definitions.values():
        if d.field_type == field_type:
            words.update(d.key.lower().split("_"))
            for kw in d.keywords:
                words.update(kw.lower().split())
    return frozenset(words)

def observed_query_templates(t: Trajectory, vocab: frozenset[str]) -> list[str]:
    """Templated queries from successful discovery actions that retained sources."""
    out: list[str] = []
    for s in t.steps:
        if (s.kind != EventKind.TOOL_CALL or s.result != EventResult.SUCCESS
                or s.capability not in {c.value for c in DISCOVERY_CAPABILITIES}):
            continue
        query = s.attrs.get("query")
        retained = s.attrs.get("sources_retained")
        if not isinstance(query, str) or (isinstance(retained, int) and retained <= 0):
            continue
        templated = template_query(query, t.anchors)
        if is_generic_query(templated, vocab) and templated not in out:
            out.append(templated)
    return out

def dependency_resolved_first(t: Trajectory) -> bool:
    """True if the utility was resolved before the first successful search for this field."""
    first_search = min((s.seq for s in t.steps if s.kind == EventKind.TOOL_CALL
                        and s.result == EventResult.SUCCESS
                        and s.capability in {c.value for c in DISCOVERY_CAPABILITIES}),
                       default=None)
    resolved = [s.seq for s in t.steps if s.kind == EventKind.FIELD_TRANSITION
                and s.field_key == "utility_name" and s.attrs.get("to_status") == "RESOLVED"]
    return first_search is not None and any(seq < first_search for seq in resolved)

class DeterministicDistiller:
    version = DISTILLER_VERSION

    def __init__(self, field_definitions: Mapping[str, FieldDefinition],
                 settings: ProcedureMemorySettings) -> None:
        self.fields = field_definitions
        self.s = settings

    def distill(self, t: Trajectory) -> Recipe:
        tool = [s for s in t.steps if s.kind == EventKind.TOOL_CALL and s.capability
                and s.field_key in t.field_keys]
        success = [s for s in tool if s.result == EventResult.SUCCESS]
        if not success:
            raise DistillationError("trajectory has no successful research steps")

        capabilities: list[Capability] = []
        for s in success:
            cap = Capability(s.capability)
            if cap not in capabilities:
                capabilities.append(cap)

        source_counts = Counter(e["source_type"] for evs in t.accepted_evidence.values()
                                for e in evs if e.get("source_type"))
        source_types = [st for st, _ in sorted(source_counts.items(),
                                               key=lambda kv: (-kv[1], kv[0]))][:6]
        queries = observed_query_templates(t, query_vocab(self.fields, t.field_type))[:8]

        steps: list[RecipeStep] = []
        if any("{utility}" in q for q in queries) and dependency_resolved_first(t):
            steps.append(RecipeStep(order=1, capability=Capability.RESOLVE_DEPENDENCY,
                                    reason="Resolve the responsible utility first; successful "
                                           "searches were utility-specific."))
        for cap in capabilities:
            steps.append(RecipeStep(
                order=len(steps) + 1, capability=cap,
                reason=STEP_REASONS.get(cap, "Observed successful step."),
                source_preferences=source_types[:4] if cap in DISCOVERY_CAPABILITIES else [],
            ))

        fallbacks: list[Capability] = []
        for s in success:
            failed_before = any(f.result == EventResult.FAILURE and f.seq < s.seq
                                and f.capability != s.capability and f.field_key == s.field_key
                                for f in tool)
            cap = Capability(s.capability)
            if failed_before and cap not in fallbacks:
                fallbacks.append(cap)

        providers: dict[str, list[str]] = {}
        for s in success:
            if s.provider:
                providers.setdefault(s.capability, [])
                if s.provider not in providers[s.capability]:
                    providers[s.capability].append(s.provider)

        label = t.field_type.replace("_", " ")
        plan = " → ".join(c.value for c in capabilities)
        prefer = f", preferring {', '.join(source_types[:3])}" if source_types else ""
        constraints = (self.s.constraints_by_field_type.get(t.field_type, [])
                       + GENERIC_CONSTRAINTS)[:6]
        return Recipe(
            task_type=t.task_type, field_type=t.field_type, water_type=t.water_type,
            title=f"{label.capitalize()} research via {plan}"[:160],
            summary=(f"For {label} research, use {plan}{prefer}; "
                     f"search with field-specific terminology.")[:600],
            recommended_steps=steps, query_patterns=queries,
            preferred_source_types=source_types, fallback_capabilities=fallbacks[:5],
            constraints=constraints, provider_metadata={k: sorted(v) for k, v in providers.items()},
        )
