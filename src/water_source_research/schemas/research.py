from typing import Any, Literal
from pydantic import BaseModel, Field, model_validator
from .common import (
    CheckResult, FieldResolutionStatus, ValidationOutcome, ValueOrigin, ValueStatus,
)

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

# How a field is resolved (services/resolution.py):
#   city        one value for the city
#   entity      its accepted values define the SupplyRecords (water_source)
#   record      one value per SupplyRecord (attributed to a water source)
#   time_series built from dated values of another field
#   derived     computed by the rules engine, never extracted
FieldScope = Literal["city", "entity", "record", "time_series", "derived"]

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
    scope: FieldScope | None = None  # defaults from subject/derived
    # Canonical unit of numeric values (unit conversion and value-in-quote checks).
    unit: str | None = None
    # Never accepted automatically (e.g. needs GIS work or an external database).
    review_always: bool = False
    # Free text where differing descriptions are not contradictions (review, not CONFLICT).
    descriptive: bool = False
    # Source types authoritative for this field (empty = the resolution policy's default).
    official_sources: list[str] = Field(default_factory=list)
    # Built at export from the run's citations (References): never extracted or reviewed.
    generated: bool = False
    # When nothing was found, the city catalog or the methodology's derivations fill the gap,
    # so "nothing found" is not a review item (conflicting/uncertain claims still are).
    gap_filled_by: Literal["catalog", "rules"] | None = None
    # Plain definition from the standardization methodology (config/field_descriptions.yaml):
    # shown to the extraction model and the evaluation judge.
    definition: str | None = None

    @model_validator(mode="after")
    def _default_scope(self) -> "FieldDefinition":
        if self.scope is None:
            if self.derived:
                self.scope = "derived"
            else:
                self.scope = "city" if self.subject == "city" else "record"
        return self

class ValidationCheck(BaseModel):
    code: str
    result: CheckResult
    detail: str | None = None

class ClaimValidation(BaseModel):
    outcome: ValidationOutcome
    checks: list[ValidationCheck] = Field(default_factory=list)
    version: str

    def codes(self, *results: CheckResult) -> list[str]:
        return [c.code for c in self.checks if c.result in results]

    def result_of(self, code: str) -> CheckResult | None:
        return next((c.result for c in self.checks if c.code == code), None)

# What a water-source value names (standardization rules: Water Source = a natural source,
# Reclaimed, or desalinated sea water; reservoirs, systems and regions are other fields).
SOURCE_KINDS = ("river", "lake", "spring", "aquifer", "wellfield", "sea", "reclaimed",
                "desalination", "general_groundwater", "reservoir", "supply_system", "region",
                "category")

class ClaimQualifiers(BaseModel):
    """Context that decides whether two claims describe the same measurement."""

    reference_year: int | None = None
    year_basis: Literal["quote", "passage", "document"] | None = None
    reported_value: float | None = None  # the number as the source states it
    reported_unit: str | None = None
    measurement_basis: str | None = None  # average | maximum | capacity | annual
    source_types: list[str] = Field(default_factory=list)
    source_dates: list[str] = Field(default_factory=list)
    # Discovery round whose extraction produced the claim (1 = first pass; later rounds are
    # re-extractions on new evidence). Provenance only: never part of the claim's identity.
    extraction_round: int | None = None
    # water_source / reservoir_names: what kind of thing the value names (SOURCE_KINDS), and
    # relationships the evidence states, each with its verbatim quote and source URL, e.g.
    # {"relation": "on_river", "target": "San Jacinto River", "quote": "...", "url": "..."}.
    # Never inferred from names or geography.
    source_kind: str | None = None
    relations: list[dict[str, Any]] = Field(default_factory=list)

class FieldClaim(BaseModel):
    field_key: str
    value: Any = None
    value_status: ValueStatus = ValueStatus.KNOWN
    value_origin: ValueOrigin = ValueOrigin.REPORTED
    confidence: float = Field(ge=0, le=1)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    # Set by normalization (services/normalization.py); stable across retries and reprocessing.
    claim_uid: str | None = None
    original_value: Any = None  # the value as extracted
    normalized_value: Any = None  # standardized form used for comparison and export
    qualifiers: ClaimQualifiers | None = None
    validation: ClaimValidation | None = None
    # Water source the claim describes (record-scope fields), set by resolution.
    entity_key: str | None = None
    # Human review: REJECTED claims are kept but never selected; CORRECTED claims were
    # entered by a reviewer (``corrected_from`` = the claim they replace); a reviewer may pin
    # a claim to a water source (``assigned_entity_key``) that attribution then respects.
    review_status: Literal["REJECTED", "CORRECTED"] | None = None
    corrected_from: str | None = None
    assigned_entity_key: str | None = None

    @property
    def comparable_value(self) -> Any:
        return self.normalized_value if self.normalized_value is not None else self.value

class EntityResolution(BaseModel):
    """Resolution of one field for one entity (the city, a water source, or all records)."""

    entity_key: str
    label: str | None = None
    status: FieldResolutionStatus
    rule: str
    reason_codes: list[str] = Field(default_factory=list)
    selected_claim_uids: list[str] = Field(default_factory=list)
    candidate_claim_uids: list[str] = Field(default_factory=list)
    # Valid claims for earlier reference years (kept, not conflicts).
    historical_claim_uids: list[str] = Field(default_factory=list)
    # Reviewer decisions are never recomputed by the rules.
    decided_by: Literal["RULES", "REVIEWER"] = "RULES"
    review_decision_id: int | None = None
    # water_source entities: relationships to other source entities stated in the evidence
    # ({"relation", "target_entity_key", "quote", "url"}); both entities are kept.
    relations: list[dict[str, Any]] = Field(default_factory=list)
    note: str | None = None  # reviewer's reason (e.g. why Unknown / N/A)

class FieldResolution(BaseModel):
    scope: str
    decided_by: Literal["RULES", "REVIEWER"] = "RULES"
    policy_version: str
    entities: list[EntityResolution] = Field(default_factory=list)

class FieldState(BaseModel):
    field_key: str
    status: FieldResolutionStatus = FieldResolutionStatus.UNRESOLVED
    selected_claim: FieldClaim | None = None
    candidate_claims: list[FieldClaim] = Field(default_factory=list)
    resolution: FieldResolution | None = None

    def claim(self, claim_uid: str) -> FieldClaim | None:
        return next((c for c in self.candidate_claims if c.claim_uid == claim_uid), None)

class CityResearchState(BaseModel):
    city_name: str
    country: str | None = None
    fields: dict[str, FieldState] = Field(default_factory=dict)
