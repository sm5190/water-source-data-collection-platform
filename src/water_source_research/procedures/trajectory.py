"""Structured execution trajectories, built only from trusted workflow events.

Never from prompts, chat logs or document text. A trajectory covers one (run, field type).
"""

import hashlib
import re
from collections.abc import Mapping
from typing import Any
from water_source_research.schemas.events import EventKind, EventResult, WorkflowEvent
from water_source_research.schemas.procedures import (
    QualitySignals, Trajectory, TrajectoryStep, task_type_for,
)
from water_source_research.schemas.research import FieldDefinition

# Anchor field -> query placeholder.
PLACEHOLDER_FOR = {"city_name": "city", "country": "country", "utility_name": "utility",
                   "jurisdiction": "jurisdiction"}

def trajectory_id(run_id: str, task_type: str, field_type: str, workflow_version: int) -> str:
    return _hash(run_id, task_type, field_type, str(workflow_version))

def distillation_key(trajectory_id_: str, distiller_version: str) -> str:
    return _hash(trajectory_id_, distiller_version)

def template_query(query: str, anchors: Mapping[str, str]) -> str:
    """Replace run-specific values (city, utility, ...) with placeholders."""
    templated = query
    for name, value in sorted(anchors.items(), key=lambda kv: -len(kv[1] or "")):
        if value:
            templated = re.sub(re.escape(value), "{" + name + "}", templated, flags=re.IGNORECASE)
    return " ".join(templated.split())

def build_trajectory(
    run: Mapping[str, Any],
    events: list[WorkflowEvent],
    field_definitions: Mapping[str, FieldDefinition],
    field_type: str,
) -> Trajectory:
    keys = [k for k, d in field_definitions.items() if d.field_type == field_type]
    keyset = set(keys)
    task_type = task_type_for(field_type)

    anchors: dict[str, str] = {"city": run["city_name"]}
    if run.get("country"):
        anchors["country"] = run["country"]
    water_type: str | None = None
    statuses: dict[str, str] = {}
    accepted: dict[str, list[dict[str, str | None]]] = {}
    steps: list[TrajectoryStep] = []
    corrections = rejected = 0
    reviewed = False
    corrected_fields: set[str] = set()

    for e in events:
        to_status = e.attrs.get("to_status")
        if e.kind is EventKind.FIELD_TRANSITION and to_status:
            if e.field_key in keyset:
                statuses[e.field_key] = str(to_status)
            anchor_value = e.attrs.get("anchor_value")
            if to_status == "RESOLVED" and isinstance(anchor_value, str) and anchor_value:
                if e.field_key in PLACEHOLDER_FOR:
                    anchors[PLACEHOLDER_FOR[e.field_key]] = anchor_value
                elif e.field_key == "water_type":
                    water_type = anchor_value
        relevant = e.field_key in keyset or (
            e.kind is EventKind.FIELD_TRANSITION and e.field_key in PLACEHOLDER_FOR
            and to_status == "RESOLVED"
        )
        if not relevant:
            continue
        if e.kind is EventKind.EVIDENCE_ACCEPTED and e.field_key:
            url = e.attrs.get("url")
            source_type = e.attrs.get("source_type")
            accepted.setdefault(e.field_key, []).append({
                "url": url if isinstance(url, str) else None,
                "source_type": source_type if isinstance(source_type, str) else None,
            })
        if e.kind is EventKind.HITL and e.field_key in keyset:
            reviewed = True
            if e.result is EventResult.CORRECTED:
                corrections += 1
                corrected_fields.add(e.field_key)
        if e.kind is EventKind.VALIDATION and e.result is EventResult.REJECTED:
            rejected += 1
        steps.append(TrajectoryStep(
            seq=e.seq, kind=e.kind.value, node=e.node, field_key=e.field_key,
            capability=e.capability.value if e.capability else None, provider=e.provider,
            strategy=e.strategy, result=e.result.value if e.result else None,
            attrs=dict(e.attrs), created_at=e.created_at,
        ))

    resolved = [k for k in keys if statuses.get(k) == "RESOLVED"]
    quality = QualitySignals(
        human_review_required=reviewed or bool(run.get("human_review_required")),
        human_correction_count=corrections,
        rejected_claim_count=rejected,
        accepted_without_edit=len([k for k in resolved if k not in corrected_fields]),
        conflict_count=len([k for k in keys if statuses.get(k) == "CONFLICT"]),
    )
    return Trajectory(
        trajectory_id=trajectory_id(run["run_id"], task_type, field_type,
                                    run["workflow_version"]),
        run_id=run["run_id"], city_name=run["city_name"], country=run.get("country"),
        workflow_version=run["workflow_version"], task_type=task_type, field_type=field_type,
        field_keys=keys, outcome=run["outcome"], water_type=water_type,
        field_statuses=statuses, accepted_evidence=accepted, anchors=anchors, steps=steps,
        quality=quality,
    )

def _hash(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()
