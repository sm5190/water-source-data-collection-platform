"""Deterministic checks that a candidate recipe is grounded in what actually happened.

Applies to any distiller (deterministic or LLM): a recipe may only recommend capabilities
that succeeded, query patterns that were observed and retained sources, source types that
backed accepted evidence, and providers that were actually used.
"""

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from water_source_research.core.capabilities import Capability
from water_source_research.schemas.events import EventKind, EventResult
from water_source_research.schemas.procedures import Recipe, Trajectory
from water_source_research.schemas.research import FieldDefinition
from .distiller import dependency_resolved_first, observed_query_templates, query_vocab

@dataclass(frozen=True, slots=True)
class Violation:
    code: str
    detail: str

def check_grounding(recipe: Recipe, t: Trajectory,
                    field_definitions: Mapping[str, FieldDefinition]) -> list[Violation]:
    out: list[Violation] = []
    if recipe.task_type != t.task_type or recipe.field_type != t.field_type:
        out.append(Violation("FIELD_MISMATCH",
                             f"recipe {recipe.task_type}/{recipe.field_type} vs trajectory "
                             f"{t.task_type}/{t.field_type}"))
    if recipe.water_type is not None and recipe.water_type != t.water_type:
        out.append(Violation("WATER_TYPE_MISMATCH", f"{recipe.water_type} vs {t.water_type}"))

    tool = [s for s in t.steps if s.kind == EventKind.TOOL_CALL and s.field_key in t.field_keys]
    succeeded = {s.capability for s in tool if s.result == EventResult.SUCCESS}
    observed = {s.capability for s in tool}
    providers: dict[str, set[str]] = defaultdict(set)
    for s in tool:
        if s.result == EventResult.SUCCESS and s.provider and s.capability:
            providers[s.capability].add(s.provider)

    for step in recipe.recommended_steps:
        cap = step.capability
        if cap is Capability.RESOLVE_DEPENDENCY:
            if not dependency_resolved_first(t):
                out.append(Violation("UNGROUNDED_DEPENDENCY",
                                     "dependency resolution not observed before searching"))
        elif cap.value not in observed:
            out.append(Violation("INVENTED_CAPABILITY", f"{cap.value} was never used"))
        elif cap.value not in succeeded:
            out.append(Violation("UNSUCCESSFUL_CAPABILITY", f"{cap.value} never succeeded"))
    for cap in recipe.fallback_capabilities:
        if cap.value not in succeeded:
            out.append(Violation("INVENTED_FALLBACK", f"{cap.value} never succeeded"))

    observed_queries = set(observed_query_templates(t, query_vocab(field_definitions,
                                                                   t.field_type)))
    for q in recipe.query_patterns:
        if q not in observed_queries:
            out.append(Violation("UNOBSERVED_QUERY", q))

    accepted_types = {e["source_type"] for evs in t.accepted_evidence.values() for e in evs
                      if e.get("source_type")}
    claimed_types = set(recipe.preferred_source_types)
    for step in recipe.recommended_steps:
        claimed_types.update(step.source_preferences)
    for st in sorted(claimed_types - accepted_types):
        out.append(Violation("UNGROUNDED_SOURCE_TYPE",
                             f"{st} did not contribute accepted evidence"))

    for cap, names in recipe.provider_metadata.items():
        for name in names:
            if name not in providers.get(cap, set()):
                out.append(Violation("INVENTED_PROVIDER", f"{name} did not succeed for {cap}"))
    return out
