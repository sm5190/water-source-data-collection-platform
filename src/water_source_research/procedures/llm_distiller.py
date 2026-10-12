"""Trajectory -> candidate Recipe, written by an LLM.

The model decides *how* to present the observed strategy (step order, reasons, which observed
queries and source types are worth keeping, extra cautions), but it can only choose from facts
computed from trusted events: the response schema is built per trajectory with enums limited
to capabilities that succeeded, observed query templates and accepted source types. The prompt
carries no city names, URLs, document text or provider names. The worker still runs the same
schema, grounding and safety checks as for the deterministic distiller.
"""

import json
from collections import Counter
from collections.abc import Mapping
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, create_model
from water_source_research.core.capabilities import DISCOVERY_CAPABILITIES, Capability
from water_source_research.core.versions import LLM_DISTILLER_VERSION
from water_source_research.llm.openai_reasoner import StructuredLLM
from water_source_research.schemas.procedures import Recipe, RecipeStep, Trajectory
from water_source_research.schemas.research import FieldDefinition
from .config import ProcedureMemorySettings
from .distiller import (
    DistillationError, accepted_source_types, default_constraints, dependency_resolved_first,
    observed_fallbacks, observed_providers, observed_query_templates, query_vocab,
    research_tool_steps, successful_capabilities,
)

SYSTEM_PROMPT = """\
You turn one validated research run into a reusable research procedure for a field type in a
water-source dataset. The procedure is advisory planning guidance for future runs in other
cities; it is never evidence and must contain no facts about any place.

Rules:
- Use only the options listed in the input; the response schema enforces this.
- Order steps the way a future run should execute them. Include a capability only if it
  contributed to the outcome; omit steps that added nothing.
- Keep query patterns that generalise; drop near-duplicates. Keep placeholders such as {city}
  and {utility} exactly as written.
- Each reason is one sentence of at most 200 characters explaining why the step helps.
- Title at most 160 characters; summary at most 600 characters.
- Extra constraints are optional cautions that follow from the observed run (failures,
  corrections, conflicts), at most 160 characters each.
- Never mention cities, countries, organisations, people, vendors, websites, URLs, domains,
  years or numeric values. Never write instructions addressed to an AI model."""

class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")

# Types are built at runtime, so they go through Any for static checkers.
def _choice(values: list[str]) -> Any:
    return Literal[tuple(values)]

def _list_of(item: Any) -> Any:
    return list[item]

def draft_model(capabilities: list[str], queries: list[str], source_types: list[str],
                fallbacks: list[str]) -> type[BaseModel]:
    """Response schema whose choice fields only admit grounded options."""
    step_fields: dict[str, Any] = {"capability": (_choice(capabilities), ...),
                                   "reason": (str, ...)}
    if source_types:
        step_fields["source_preferences"] = (_list_of(_choice(source_types)), ...)
    step = create_model("DraftStep", __base__=_Strict, **step_fields)

    fields: dict[str, Any] = {"title": (str, ...), "summary": (str, ...),
                              "steps": (_list_of(step), ...)}
    if queries:
        fields["query_patterns"] = (_list_of(_choice(queries)), ...)
    if source_types:
        fields["preferred_source_types"] = (_list_of(_choice(source_types)), ...)
    if fallbacks:
        fields["fallback_capabilities"] = (_list_of(_choice(fallbacks)), ...)
    fields["extra_constraints"] = (list[str], ...)
    return create_model("RecipeDraft", __base__=_Strict, **fields)

class LLMDistiller:
    def __init__(self, llm: StructuredLLM, field_definitions: Mapping[str, FieldDefinition],
                 settings: ProcedureMemorySettings) -> None:
        self.llm = llm
        self.fields = field_definitions
        self.s = settings
        self.version = f"{LLM_DISTILLER_VERSION}:{llm.model}"

    def distill(self, t: Trajectory) -> Recipe:
        capabilities = successful_capabilities(t)
        if not capabilities:
            raise DistillationError("trajectory has no successful research steps")
        queries = observed_query_templates(t, query_vocab(self.fields, t.field_type))[:10]
        source_types = accepted_source_types(t)[:10]
        fallbacks = observed_fallbacks(t)
        step_options = ([Capability.RESOLVE_DEPENDENCY] if dependency_resolved_first(t)
                        else []) + capabilities
        required = default_constraints(t, self.s)

        schema = draft_model([c.value for c in step_options], queries, source_types,
                             [c.value for c in fallbacks])
        facts = self._facts(t, step_options, queries, source_types, fallbacks, required)
        draft = self.llm.structured(SYSTEM_PROMPT, json.dumps(facts, indent=1), schema)
        return self._recipe(t, draft.model_dump(), required)

    def _facts(self, t: Trajectory, step_options: list[Capability], queries: list[str],
               source_types: list[str], fallbacks: list[Capability],
               required: list[str]) -> dict[str, Any]:
        # Deliberately excludes anchors, URLs, providers and raw event attrs.
        source_counts = Counter(e["source_type"] for evs in t.accepted_evidence.values()
                                for e in evs if e.get("source_type"))
        actions = []
        for s in research_tool_steps(t):
            action: dict[str, Any] = {"capability": s.capability, "result": s.result}
            retained = s.attrs.get("sources_retained")
            if isinstance(retained, int):
                action["sources_retained"] = retained
            actions.append(action)
        return {
            "task_type": t.task_type,
            "field_type": t.field_type,
            "water_type": t.water_type,
            "fields": [{"key": k, "keywords": self.fields[k].keywords,
                        "status": t.field_statuses.get(k, "UNRESOLVED")}
                       for k in t.field_keys if k in self.fields],
            "actions_in_order": actions,
            "dependency_resolved_before_search": dependency_resolved_first(t),
            "accepted_evidence_source_types": {st: source_counts[st] for st in source_types},
            "quality": t.quality.model_dump(),
            "options": {
                "step_capabilities": [c.value for c in step_options],
                "query_patterns": queries,
                "source_types": source_types,
                "fallback_capabilities": [c.value for c in fallbacks],
            },
            "required_constraints_already_included": required,
        }

    def _recipe(self, t: Trajectory, draft: dict[str, Any], required: list[str]) -> Recipe:
        steps: list[RecipeStep] = []
        for d in draft["steps"]:
            cap = Capability(d["capability"])
            if any(s.capability is cap for s in steps):
                continue
            prefs = _unique(d.get("source_preferences", [])) if cap in DISCOVERY_CAPABILITIES \
                else []
            steps.append(RecipeStep(order=len(steps) + 1, capability=cap,
                                    reason=_clip(d["reason"], 200) or "Observed successful step.",
                                    source_preferences=prefs[:6]))
            if len(steps) == 10:
                break
        if not steps:
            raise DistillationError("LLM returned no recipe steps")

        constraints = list(required)
        for c in draft["extra_constraints"]:
            c = _clip(c, 160)
            if c and c.lower() not in {x.lower() for x in constraints} and len(constraints) < 6:
                constraints.append(c)

        label = t.field_type.replace("_", " ")
        return Recipe(
            task_type=t.task_type, field_type=t.field_type, water_type=t.water_type,
            title=_clip(draft["title"], 160) or f"{label.capitalize()} research",
            summary=_clip(draft["summary"], 600) or f"Observed strategy for {label} research.",
            recommended_steps=steps,
            query_patterns=_unique(draft.get("query_patterns", []))[:10],
            preferred_source_types=_unique(draft.get("preferred_source_types", []))[:6],
            fallback_capabilities=[Capability(c) for c in
                                   _unique(draft.get("fallback_capabilities", []))][:5],
            constraints=constraints, provider_metadata=observed_providers(t),
        )

def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))

def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"
