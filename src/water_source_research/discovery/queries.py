"""Deterministic discovery query planning (no LLM).

Per round, the fields that still need evidence are grouped by ``field_type``. Procedure
``planned_queries`` (advisory, from procedural memory) come first as web queries; otherwise
queries are built from the city/utility and a per-field-type topic. Queries already run in an
earlier round (read back from the trusted TOOL_CALL events) are not repeated.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal
from water_source_research.schemas.research import FieldDefinition

QueryKind = Literal["web", "academic"]

# field_type -> (web topic, academic topic). None: don't search for this field type.
TOPICS: dict[str, tuple[str, str] | None] = {
    "city_identity": None,
    "references": None,
    "population": ("population served drinking water utility", "urban water supply population"),
    "utility": ("drinking water utility", "municipal drinking water supply"),
    "water_source": ("drinking water sources reservoirs", "drinking water supply sources"),
    "source_type": ("drinking water source surface water groundwater",
                    "groundwater surface water supply"),
    "transfer_system": ("water supply aqueduct pipeline transfer", "inter-basin water transfer"),
    "point_of_diversion": ("drinking water intake location", "water intake"),
    "infrastructure": ("water treatment plant intake infrastructure",
                       "water supply infrastructure"),
    "withdrawal": ("water production annual report", "urban water withdrawals"),
    "water_quality": ("drinking water quality report", "drinking water quality"),
}

@dataclass(frozen=True, slots=True)
class PlannedQuery:
    kind: QueryKind
    query: str
    field_type: str
    field_key: str  # representative target field (trusted events are keyed by field)

def previous_queries(events: Sequence[Mapping[str, Any]]) -> set[str]:
    return {str(e["attrs"]["query"]).casefold() for e in events
            if e.get("kind") == "TOOL_CALL" and e.get("node") == "discover"
            and isinstance(e.get("attrs"), dict) and e["attrs"].get("query")}

def build_queries(
    *,
    city: str,
    country: str | None,
    utility: str | None,
    field_keys: Sequence[str],
    fields: Mapping[str, FieldDefinition],
    planned_queries: Mapping[str, Sequence[str]] | None = None,
    already_run: set[str] = frozenset(),  # type: ignore[assignment]
    max_per_kind: int = 6,
) -> list[PlannedQuery]:
    groups: dict[str, str] = {}  # field_type -> first target field
    for key in field_keys:
        d = fields.get(key)
        if d is not None and not d.derived:
            groups.setdefault(d.field_type, key)

    candidates: dict[QueryKind, list[list[PlannedQuery]]] = {"web": [], "academic": []}
    place = " ".join(p for p in (city, country) if p)
    for field_type, field_key in groups.items():
        web: list[PlannedQuery] = [PlannedQuery("web", q, field_type, field_key)
                                   for q in (planned_queries or {}).get(field_type, []) if q]
        academic: list[PlannedQuery] = []
        topic = TOPICS.get(field_type, _fallback_topic(fields[field_key]))
        if topic:
            web_topic, academic_topic = topic
            subject = utility or place
            web.append(PlannedQuery("web", f"{subject} {web_topic}", field_type, field_key))
            academic.append(PlannedQuery("academic", f"{city} {academic_topic}", field_type,
                                         field_key))
        candidates["web"].append(web)
        candidates["academic"].append(academic)

    out: list[PlannedQuery] = []
    seen = {q.casefold() for q in already_run}
    for kind in ("web", "academic"):
        picked = 0
        # Round-robin across field types so one type can't use the whole budget.
        queues = [list(g) for g in candidates[kind]]
        while picked < max_per_kind and any(queues):
            for queue in queues:
                if not queue or picked >= max_per_kind:
                    continue
                q = queue.pop(0)
                text = " ".join(q.query.split())
                if text.casefold() in seen:
                    continue
                seen.add(text.casefold())
                out.append(PlannedQuery(q.kind, text, q.field_type, q.field_key))
                picked += 1
    return out

def _fallback_topic(d: FieldDefinition) -> tuple[str, str] | None:
    words = " ".join(d.keywords[:3])
    return (f"water {words}", f"water supply {words}") if words else None
