from typing import Any
from pydantic import BaseModel, Field
from .common import FieldResolutionStatus, ValueOrigin, ValueStatus

class EvidenceRef(BaseModel):
    url: str
    title: str | None = None
    page_number: int | None = None
    passage: str
    retrieved_at: str | None = None

class EvidenceChunk(BaseModel):
    """A retrievable slice of an acquired document; the unit the context manager selects."""

    chunk_id: str
    url: str
    text: str
    title: str | None = None
    page_number: int | None = None
    source_type: str | None = None  # UTILITY, GOVERNMENT, REGULATORY, TECHNICAL, ACADEMIC, OTHER
    acquisition_method: str | None = None
    retrieved_at: str | None = None

class FieldDefinition(BaseModel):
    key: str
    field_type: str
    subject: str
    type: str
    allowed: list[str] = Field(default_factory=list)
    derived: bool = False
    # Several distinct values can be true at once (e.g. a city drawing on two rivers): each
    # is extracted as its own candidate claim instead of being merged into one string.
    multi_valued: bool = False
    depends_on: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)

class FieldClaim(BaseModel):
    field_key: str
    value: Any = None
    value_status: ValueStatus = ValueStatus.KNOWN
    value_origin: ValueOrigin = ValueOrigin.REPORTED
    confidence: float = Field(ge=0, le=1)
    evidence: list[EvidenceRef] = Field(default_factory=list)

class FieldState(BaseModel):
    field_key: str
    status: FieldResolutionStatus = FieldResolutionStatus.UNRESOLVED
    selected_claim: FieldClaim | None = None
    candidate_claims: list[FieldClaim] = Field(default_factory=list)

class CityResearchState(BaseModel):
    city_name: str
    country: str | None = None
    fields: dict[str, FieldState] = Field(default_factory=dict)
