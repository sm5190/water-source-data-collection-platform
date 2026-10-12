"""Conservative procedure deduplication.

Two recipes can only be the same procedure if their compatibility metadata *and* capability
plan match (that is the ``procedure_key``): groundwater vs surface water, or withdrawal vs
water-quality research, never collapse. Within one key, a candidate merges into an existing
version only when its content is structurally equivalent; embedding similarity is an extra
guard, never sufficient on its own.
"""

import hashlib
import json
import math
from dataclasses import dataclass
from enum import StrEnum
from water_source_research.schemas.procedures import ProcedureStatus, Recipe
from .config import ProcedureMemorySettings
from .repository import VersionSnapshot

_MERGEABLE = {ProcedureStatus.CANDIDATE, ProcedureStatus.AWAITING_APPROVAL,
              ProcedureStatus.ACTIVE}

class MergeDecision(StrEnum):
    MERGE = "MERGE"
    NEW_VERSION = "NEW_VERSION"
    NEW_PROCEDURE = "NEW_PROCEDURE"

@dataclass(frozen=True, slots=True)
class MergeEvaluation:
    decision: MergeDecision
    target_version_id: str | None = None
    jaccard: float | None = None
    cosine: float | None = None

def procedure_key(recipe: Recipe, jurisdiction_type: str | None, language: str | None) -> str:
    identity = {
        "task_type": recipe.task_type,
        "field_type": recipe.field_type,
        "water_type": recipe.water_type,
        "jurisdiction_type": jurisdiction_type,
        "language": language,
        "capability_plan": list(recipe.capability_plan()),
    }
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()

class MergeEvaluator:
    def __init__(self, settings: ProcedureMemorySettings) -> None:
        self.s = settings

    def evaluate(
        self,
        candidate: Recipe,
        candidate_embedding: list[float] | None,
        existing: list[VersionSnapshot],
        embedding_model: str | None = None,
    ) -> MergeEvaluation:
        live = [v for v in existing if v.status in _MERGEABLE]
        if not existing:
            return MergeEvaluation(MergeDecision.NEW_PROCEDURE)
        # Prefer merging into the active version, then the newest.
        live.sort(key=lambda v: (v.status != ProcedureStatus.ACTIVE, -v.version))
        cand_hash = candidate.content_hash()
        cand_set = _content_set(candidate)
        for v in live:
            if v.recipe_hash == cand_hash:
                return MergeEvaluation(MergeDecision.MERGE, v.id, 1.0, None)
            existing_recipe = Recipe.model_validate(v.recipe_json)
            if existing_recipe.capability_plan() != candidate.capability_plan():
                continue  # defensive: same key implies same plan
            jaccard = _jaccard(cand_set, _content_set(existing_recipe))
            comparable = (candidate_embedding is not None and v.embedding is not None
                          and (embedding_model is None or v.embedding_model is None
                               or v.embedding_model == embedding_model))
            cosine = _cosine(candidate_embedding, v.embedding) if comparable else None
            if jaccard >= self.s.merge_min_jaccard and (
                cosine is None or cosine >= self.s.merge_min_cosine
            ):
                return MergeEvaluation(MergeDecision.MERGE, v.id, jaccard, cosine)
        return MergeEvaluation(MergeDecision.NEW_VERSION)

def _content_set(r: Recipe) -> set[str]:
    return ({f"src:{s}" for s in r.preferred_source_types}
            | {f"q:{q.lower()}" for q in r.query_patterns}
            | {f"fb:{c.value}" for c in r.fallback_capabilities})

def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)

def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0
