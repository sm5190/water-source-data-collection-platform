"""Trusted workflow events: what the system actually did during a run.

These are the only input to trajectory distillation. They hold enums, counts, statuses and
system-generated values; raw web/PDF text never belongs here. Free-form attributes are
restricted to an allow-list, URLs are reduced to scheme://host/path (no query strings,
fragments or credentials), and secret-looking strings are redacted.
"""

import re
from datetime import UTC, datetime
from enum import StrEnum
from urllib.parse import urlsplit, urlunsplit
from pydantic import BaseModel, ConfigDict, Field, field_validator
from water_source_research.core.capabilities import Capability

class EventKind(StrEnum):
    NODE = "NODE"
    TOOL_CALL = "TOOL_CALL"
    FIELD_TRANSITION = "FIELD_TRANSITION"
    VALIDATION = "VALIDATION"
    HITL = "HITL"
    EVIDENCE_ACCEPTED = "EVIDENCE_ACCEPTED"

class EventResult(StrEnum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    NO_RESULT = "NO_RESULT"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    APPROVED = "APPROVED"
    CORRECTED = "CORRECTED"
    SKIPPED = "SKIPPED"

# Fields whose resolved value may be recorded, solely so distillation can replace them with
# placeholders ({city}, {utility}, ...) when templating observed search queries.
ANCHOR_FIELDS = frozenset({"city_name", "country", "jurisdiction", "utility_name", "water_type"})

ALLOWED_ATTRS = frozenset({
    "query", "sources_found", "sources_retained", "source_type", "url", "duration_ms", "cost",
    "from_status", "to_status", "bundle_status", "claims", "chunks", "anchor_value",
    "correction_category", "procedure_id", "procedure_version", "planned_queries",
})
MAX_ATTR_CHARS = 300

_SECRET = re.compile(
    r"(sk-[A-Za-z0-9]{8,}|AKIA[0-9A-Z]{12,}|bearer\s+\S+|api[_-]?key|access[_-]?token|"
    r"password|secret|authorization|session[_-]?id|[A-Za-z0-9+/_-]{40,})",
    re.IGNORECASE,
)

def contains_secret(text: str) -> bool:
    return bool(_SECRET.search(text))

def strip_url(url: str) -> str:
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path, "", ""))

AttrValue = str | int | float | bool | None

class WorkflowEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seq: int = Field(ge=1)
    kind: EventKind
    node: str
    field_key: str | None = None
    capability: Capability | None = None
    provider: str | None = None
    strategy: str | None = None
    result: EventResult | None = None
    attrs: dict[str, AttrValue] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    @field_validator("attrs")
    @classmethod
    def _sanitize_attrs(cls, attrs: dict[str, AttrValue]) -> dict[str, AttrValue]:
        unknown = set(attrs) - ALLOWED_ATTRS
        if unknown:
            raise ValueError(f"Event attrs not allowed: {sorted(unknown)}")
        clean: dict[str, AttrValue] = {}
        for key, value in attrs.items():
            if isinstance(value, str):
                if key == "url":
                    value = strip_url(value)
                elif contains_secret(value):
                    value = "[redacted]"
                value = value[:MAX_ATTR_CHARS]
            clean[key] = value
        return clean

def append_event(events: list[dict] | None, **fields: object) -> list[dict]:
    """Return a new event list with one validated event appended (seq assigned here)."""
    events = list(events or [])
    event = WorkflowEvent(seq=len(events) + 1, **fields)  # type: ignore[arg-type]
    events.append(event.model_dump(mode="json"))
    return events
