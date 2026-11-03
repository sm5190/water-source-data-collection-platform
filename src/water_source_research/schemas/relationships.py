"""Evidence-supported water-supply relationships (services/supply_relationships.py).

Provenance and explanation only: no relationship accepts or rejects a field value. Entities
keep their identity apart from their role: an organisation is ``utility:<key>`` whatever it
does; what it does for the city is the relationship (serves it, supplies it wholesale, ...).

Statuses (recomputed from all evidence on every build; a rebuild never promotes by itself):
- VERIFIED: an explicit, placed statement in a government/regulatory document, or explicit
  placed statements from two independent hosts (neither the subject's own site), one of them
  in a document whose country is the target's.
- SUPPORTED: an explicit placed statement, but only the subject's own site (a
  self-description) or a single independent host.
- CANDIDATE: no placed explicit statement: a research claim only, or statements whose place
  is uncertain (AMBIGUOUS) or taken only from the document as a whole.
- HISTORICAL: every statement carries explicit historical scope (a closed year range, "until
  1998", "formerly", "replaced by", "no longer", a predecessor statement). Past tense alone is
  never enough (temporal stays UNKNOWN).
- CONTRADICTED: current statements together with an explicit negation or discontinuation that
  no later dated statement overrides. Every statement is kept.
Transitions: CANDIDATE -> SUPPORTED -> VERIFIED as placed evidence is added; any -> CONTRADICTED
when a negation/discontinuation appears beside current statements; -> HISTORICAL only with
temporal evidence.
"""

from typing import Literal
from pydantic import BaseModel, Field

RelationshipKind = Literal[
    "UTILITY_SERVES_CITY",      # a drinking-water operator serves (part of) the city
    "SUPPLIES_WHOLESALE_TO",    # bulk / raw / treated water to the city or another utility
    "SOURCE_SUPPLIES_UTILITY",  # a source (or infrastructure) supplies a utility
    "SOURCE_SUPPLIES_CITY",     # a source supplies the city
    "SOURCE_PART_OF_SYSTEM",    # a source is a stated component of a larger system
    "PREDECESSOR_OF",           # an operator preceded another
    "ALIAS_OF",                 # explicit equivalence ("also known as", "formerly")
    "DEPARTMENT_OF",            # an organisation is a department of the city (affiliation)
]
RelationshipStatus = Literal["VERIFIED", "SUPPORTED", "CANDIDATE", "HISTORICAL", "CONTRADICTED"]
Temporal = Literal["CURRENT", "HISTORICAL", "UNKNOWN"]
EntityType = Literal["city", "utility", "source", "system"]
# Who says it: the subject about itself (own site), a government/regulator, another document,
# or only a research claim (extraction output).
# MUNICIPAL_DEPARTMENT: a page of an organisation verified as a city department
# (services/affiliation.py); weighed like a government document in the graph only.
Basis = Literal["SELF_DESCRIPTION", "GOVERNMENT_OR_REGULATOR", "MUNICIPAL_DEPARTMENT",
                "INDEPENDENT", "CLAIM"]

class Entity(BaseModel):
    entity_id: str  # "<type>:<key>" (sources: "source:<kind>:<key>")
    type: EntityType
    name: str
    kind: str | None = None  # sources: SOURCE_KINDS value (river, spring, reservoir, ...)

class RelationshipEvidence(BaseModel):
    url: str
    document_id: int | None = None
    chunk_id: str | None = None
    page: int | None = None
    quote: str
    span: tuple[int, int] | None = None  # characters of the quote in the chunk
    predicate: str  # the phrase stating the relationship ("takes its water from", ...)
    geo_status: str  # services/geo_match.py GeoStatus of the passage for the city
    geo_reason: str = ""
    placed: bool = False  # the passage itself (or its section) places the city
    strength: Literal["AUTHORITATIVE", "SUPPORTING", "NONE"] = "NONE"
    basis: Basis = "INDEPENDENT"
    host: str = ""
    source_type: str | None = None
    document_scope: str = "UNKNOWN"
    temporal: Temporal = "UNKNOWN"
    temporal_marker: str | None = None
    years: list[int] = Field(default_factory=list)
    negated: bool = False  # "does not supply", "no longer supplies", "ya no abastece"
    # DEPARTMENT_OF evidence (services/affiliation.py): what kind of signal, the link target,
    # the heading the link sits under, and when the page was observed.
    affiliation_basis: str | None = None
    target_url: str | None = None
    heading: str | None = None
    observed_at: str | None = None

class SupplyRelationship(BaseModel):
    key: str  # kind|subject_id|object_id
    kind: RelationshipKind
    subject: Entity
    object: Entity
    status: RelationshipStatus
    status_reason: str = ""
    temporal: Temporal = "UNKNOWN"
    basis: list[Basis] = Field(default_factory=list)
    evidence: list[RelationshipEvidence] = Field(default_factory=list)

class RejectedStatement(BaseModel):
    """A statement that names both entities with a predicate but is not about the city (or
    whose subject plays a non-serving role): kept so exclusions are auditable."""
    kind: RelationshipKind
    subject: str
    object: str
    reason: str
    url: str
    quote: str

class RelationshipGraph(BaseModel):
    policy_version: str
    city: str
    country: str | None = None
    geo_basis: str = "NAME_ONLY"
    identity: str = "catalog-only"
    entities: list[Entity] = Field(default_factory=list)
    relationships: list[SupplyRelationship] = Field(default_factory=list)
    rejected: list[RejectedStatement] = Field(default_factory=list)

    def get(self, key: str) -> SupplyRelationship | None:
        return next((r for r in self.relationships if r.key == key), None)

    def of_kind(self, kind: str) -> list[SupplyRelationship]:
        return [r for r in self.relationships if r.kind == kind]

    def involving(self, entity_id: str) -> list[SupplyRelationship]:
        return [r for r in self.relationships
                if entity_id in (r.subject.entity_id, r.object.entity_id)]
