"""Procedural memory schemas: reusable research strategy, never factual data.

A Recipe describes *how* to research a field type (capabilities, source categories, query
templates, constraints). It must not contain facts about any city: query patterns must be
templated with placeholders, and grounding/safety checks reject URLs, domains and secrets.
"""

import hashlib
import json
import re
from enum import StrEnum
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from water_source_research.core.capabilities import Capability

PLACEHOLDERS = ("{city}", "{country}", "{utility}", "{jurisdiction}")
_PLACEHOLDER = re.compile(r"\{(\w+)\}")

class ProcedureStatus(StrEnum):
    CANDIDATE = "CANDIDATE"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    ACTIVE = "ACTIVE"
    DEPRECATED = "DEPRECATED"
    RETIRED = "RETIRED"
    REJECTED = "REJECTED"

def task_type_for(field_type: str) -> str:
    return f"{field_type}_research"

class RecipeStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order: int = Field(ge=1)
    capability: Capability
    reason: str = Field(max_length=200)
    source_preferences: list[str] = Field(default_factory=list, max_length=6)

class Recipe(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_type: str
    field_type: str
    water_type: str | None = None
    title: str = Field(max_length=160)
    summary: str = Field(max_length=600)
    recommended_steps: list[RecipeStep] = Field(min_length=1, max_length=10)
    query_patterns: list[str] = Field(default_factory=list, max_length=10)
    preferred_source_types: list[str] = Field(default_factory=list, max_length=6)
    fallback_capabilities: list[Capability] = Field(default_factory=list, max_length=5)
    constraints: list[str] = Field(default_factory=list, max_length=6)
    # Observed providers per capability; informational only, never required for execution.
    provider_metadata: dict[str, list[str]] = Field(default_factory=dict)

    @field_validator("query_patterns")
    @classmethod
    def _templated(cls, patterns: list[str]) -> list[str]:
        for p in patterns:
            if len(p) > 120:
                raise ValueError(f"Query pattern too long: {p[:40]}…")
            names = _PLACEHOLDER.findall(p)
            if not names:
                raise ValueError(f"Query pattern must use a placeholder: {p!r}")
            bad = [n for n in names if f"{{{n}}}" not in PLACEHOLDERS]
            if bad:
                raise ValueError(f"Unknown placeholder(s) {bad} in {p!r}")
        return patterns

    @field_validator("constraints")
    @classmethod
    def _short_constraints(cls, constraints: list[str]) -> list[str]:
        for c in constraints:
            if len(c) > 160:
                raise ValueError("Constraint too long")
        return constraints

    @model_validator(mode="after")
    def _ordered_steps(self) -> "Recipe":
        if [s.order for s in self.recommended_steps] != list(
            range(1, len(self.recommended_steps) + 1)
        ):
            raise ValueError("recommended_steps must be ordered 1..n")
        return self

    def capability_plan(self) -> tuple[str, ...]:
        return tuple(s.capability.value for s in self.recommended_steps)

    def content_hash(self) -> str:
        canonical = json.dumps(self.model_dump(mode="json"), sort_keys=True)
        return hashlib.sha256(canonical.encode()).hexdigest()

    def recipe_text(self) -> str:
        """Compact planner-facing rendering."""
        lines = [f"{self.title}: {self.summary}"]
        for s in self.recommended_steps:
            prefs = f" (prefer: {', '.join(s.source_preferences)})" if s.source_preferences else ""
            lines.append(f"{s.order}. {s.capability.value}{prefs} — {s.reason}")
        if self.query_patterns:
            lines.append("Queries: " + "; ".join(self.query_patterns))
        if self.fallback_capabilities:
            lines.append("Fallbacks: " + ", ".join(c.value for c in self.fallback_capabilities))
        if self.constraints:
            lines.append("Constraints: " + "; ".join(self.constraints))
        return "\n".join(lines)

    def embedding_text(self) -> str:
        """What the embedding represents: the reusable procedure, not an execution log."""
        steps = " ".join(
            f"{s.capability.value}[{','.join(s.source_preferences)}]"
            for s in self.recommended_steps
        )
        return (
            f"task: {self.task_type}; field: {self.field_type}; "
            f"water type: {self.water_type or 'any'}; summary: {self.summary}; steps: {steps}; "
            f"sources: {', '.join(self.preferred_source_types)}; "
            f"queries: {'; '.join(self.query_patterns)}"
        )

class ProcedureMatch(BaseModel):
    """Compact, typed guidance handed to the planner. Advisory only."""

    procedure_id: str
    procedure_version_id: str
    version: int
    task_type: str
    field_type: str
    rank: int
    score: float
    similarity_score: float | None
    reliability_score: float
    usage_count: int
    success_count: int
    recent_success_rate: float | None
    recipe: Recipe
    reasons: list[str] = Field(default_factory=list)

class RetrievalRequest(BaseModel):
    task_type: str
    field_type: str
    workflow_version: int
    run_id: str | None = None
    field_keys: list[str] = Field(default_factory=list)  # fields this guidance would serve
    water_type: str | None = None
    jurisdiction_type: str | None = None
    language: str | None = None
    operation: str = "source_discovery"

    def query_text(self) -> str:
        # No city facts: retrieval is about the kind of task, not the place.
        return (
            f"task: {self.task_type}; field: {self.field_type}; "
            f"water type: {self.water_type or 'any'}; operation: {self.operation}"
        )

class RetrievalResult(BaseModel):
    matches: list[ProcedureMatch] = Field(default_factory=list)
    candidate_count: int = 0
    degraded: bool = False  # retrieval failed entirely; caller uses its default strategy
    vector_degraded: bool = False  # ranked without semantic similarity
    degraded_reason: str | None = None

class TrajectoryStep(BaseModel):
    seq: int
    kind: str
    node: str
    field_key: str | None = None
    capability: str | None = None
    provider: str | None = None
    strategy: str | None = None
    result: str | None = None
    attrs: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    created_at: str | None = None

class QualitySignals(BaseModel):
    human_review_required: bool = False
    human_correction_count: int = 0
    rejected_claim_count: int = 0
    accepted_without_edit: int = 0
    conflict_count: int = 0

class Trajectory(BaseModel):
    """Structured execution trajectory for one (run, field type), built from trusted events."""

    trajectory_id: str
    run_id: str
    city_name: str
    country: str | None
    workflow_version: int
    task_type: str
    field_type: str
    field_keys: list[str]
    outcome: str
    water_type: str | None = None
    field_statuses: dict[str, str]
    accepted_evidence: dict[str, list[dict[str, str | None]]]  # field -> [{url, source_type}]
    anchors: dict[str, str]  # used only to template queries; never copied into recipes
    steps: list[TrajectoryStep]
    quality: QualitySignals

    def payload_hash(self) -> str:
        canonical = json.dumps(self.model_dump(mode="json"), sort_keys=True)
        return hashlib.sha256(canonical.encode()).hexdigest()
