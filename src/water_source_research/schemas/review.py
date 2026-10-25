"""Human review: what a reviewer is asked (review items) and what they decide (decisions).

A review item is one (field, entity) the rules could not settle in one review round. Decisions
are append-only; the latest decision per item in a round is the one applied when the run
resumes. Validation of decisions against their item lives in services/review.py.
"""

from enum import StrEnum
from typing import Any
from pydantic import BaseModel, Field, field_validator, model_validator
from .common import ValueOrigin

class DecisionType(StrEnum):
    APPROVE = "APPROVE"  # accept candidate claim(s) (SELECT among competing ones is the same)
    REJECT = "REJECT"  # these claims are wrong; the rules re-resolve without them
    CORRECT = "CORRECT"  # a reviewer-entered value with its own evidence
    ASSIGN_SOURCE = "ASSIGN_SOURCE"  # pin a claim to a water source
    MARK_UNKNOWN = "MARK_UNKNOWN"  # applicable, but no reliable value exists -> "Unknown"
    MARK_NA = "MARK_NA"  # does not apply -> "N/A"
    DEFER = "DEFER"  # finalize without a value for now -> "Unknown", reason documented

# Decisions that settle their item (the others send it back through the rules).
SETTLING = frozenset({DecisionType.APPROVE, DecisionType.CORRECT, DecisionType.MARK_UNKNOWN,
                      DecisionType.MARK_NA, DecisionType.DEFER})

class ReviewEvidence(BaseModel):
    """Support for a corrected value: a quote from one of the run's evidence chunks, or a
    source the reviewer supplies (kept as REVIEWER_SUPPLIED, never machine-verified)."""

    chunk_id: str | None = None
    quote: str | None = None
    url: str | None = None
    title: str | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _one_kind(self) -> "ReviewEvidence":
        if self.chunk_id:
            if not (self.quote and self.quote.strip()):
                raise ValueError("a chunk citation needs the quoted text")
        elif not (self.url and self.url.startswith(("http://", "https://"))):
            raise ValueError("evidence needs a chunk_id + quote, or an http(s) url")
        return self

class ReviewDecisionIn(BaseModel):
    """One decision as submitted. ``decision_key`` is the client's idempotency key."""

    decision_key: str = Field(min_length=8, max_length=64)
    item_id: str
    round: int = Field(ge=1)
    decision: DecisionType
    claim_uids: list[str] = Field(default_factory=list)
    value: Any = None
    value_origin: ValueOrigin = ValueOrigin.REPORTED
    reason: str | None = Field(default=None, max_length=2000)
    correction_category: str | None = Field(default=None, max_length=64)
    evidence: ReviewEvidence | None = None
    entity_key: str | None = None  # ASSIGN_SOURCE target
    # APPROVE of another candidate: the proposed claim(s) the reviewer rejected in favour of it.
    rejected_claim_uids: list[str] = Field(default_factory=list)
    # APPROVE of a claim that failed validation: the reviewer deliberately confirmed (after
    # checking the source) that the value is right. Recorded in the audit trail.
    override_validation: bool = False

    @field_validator("decision", mode="before")
    @classmethod
    def _select_is_approve(cls, value: Any) -> Any:
        return DecisionType.APPROVE if value == "SELECT" else value

def clean_reviewer(value: str) -> str:
    """Reviewer as typed, with surrounding and repeated whitespace removed."""
    return " ".join(value.split())

def reviewer_key(value: str) -> str:
    """Identity used to group a reviewer's decisions ("Sarina " and "sarina" are one person)."""
    return clean_reviewer(value).casefold()

class ReviewSubmission(BaseModel):
    reviewer: str = Field(min_length=1, max_length=128)

    @field_validator("reviewer")
    @classmethod
    def _clean(cls, value: str) -> str:
        value = clean_reviewer(value)
        if not value:
            raise ValueError("reviewer is empty")
        return value
    decisions: list[ReviewDecisionIn] = Field(min_length=1, max_length=200)

class ResumeRequest(BaseModel):
    reviewer: str = Field(min_length=1, max_length=128)
