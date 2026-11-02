"""Utilities related to the target city and the evidence for each relationship
(services/utility_registry.py). Discovery uncertainty only: a registry status never accepts a
claim by itself."""

from typing import Literal
from pydantic import BaseModel, Field

Relationship = Literal["SERVES", "SERVES_PART", "SUPPLIES_WHOLESALE", "SERVES_OTHER",
                       "NON_SERVING_ROLE", "MENTION_ONLY", "PREDECESSOR_OF", "SUCCESSOR_OF"]
Role = Literal["DRINKING_WATER_OPERATOR", "WHOLESALE_SUPPLIER", "SANITATION_ONLY",
               "CONTRACTOR", "HISTORICAL_OPERATOR"]
UtilityStatus = Literal["VERIFIED", "SANITATION_ONLY", "CANDIDATE", "AMBIGUOUS_GEOGRAPHY",
                        "OTHER_CITY", "NON_SERVING", "HISTORICAL"]
POTABLE_ROLES = ("DRINKING_WATER_OPERATOR", "WHOLESALE_SUPPLIER")

class UtilityEvidence(BaseModel):
    """One sentence about a candidate and the city."""
    relationship: Relationship
    role: Role | None = None
    geo_status: str  # services/geo_match.py GeoStatus
    geo_reason: str = ""
    url: str
    host: str
    chunk_id: str | None = None
    quote: str
    source_type: str | None = None
    document_scope: str = "UNKNOWN"
    # Strength (see the registry's verification rule): AUTHORITATIVE verifies alone,
    # SUPPORTING needs an independent second statement, NONE never verifies.
    strength: Literal["AUTHORITATIVE", "SUPPORTING", "NONE"] = "NONE"
    other: str | None = None  # the other candidate of a predecessor/successor statement

class UtilityCandidate(BaseModel):
    key: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    origins: list[str] = Field(default_factory=list)  # "claim" | "text" | "title"
    status: UtilityStatus = "CANDIDATE"
    verified_roles: list[Role] = Field(default_factory=list)
    status_reason: str = ""
    bound_hosts: list[str] = Field(default_factory=list)  # hosts that are this utility's own
    predecessor_of: list[str] = Field(default_factory=list)  # candidate keys
    successor_of: list[str] = Field(default_factory=list)
    evidence: list[UtilityEvidence] = Field(default_factory=list)

    def all_names(self) -> list[str]:
        return [self.name, *self.aliases]

class UtilityRegistry(BaseModel):
    policy_version: str
    city: str
    country: str | None = None
    identity: str = "catalog-only"  # geo_match.identity_signature
    candidates: list[UtilityCandidate] = Field(default_factory=list)

    def get(self, key: str) -> UtilityCandidate | None:
        return next((c for c in self.candidates if c.key == key), None)

    def find(self, name: str) -> UtilityCandidate | None:
        from water_source_research.services.utility_registry import utility_key
        k = utility_key(name)
        return next((c for c in self.candidates
                     if c.key == k or k in {utility_key(a) for a in c.aliases}), None)

    def verified(self, roles: tuple[str, ...] = POTABLE_ROLES) -> list[UtilityCandidate]:
        return [c for c in self.candidates
                if c.status == "VERIFIED" and set(c.verified_roles) & set(roles)]

    def verified_hosts(self, roles: tuple[str, ...] = POTABLE_ROLES) -> list[str]:
        return list(dict.fromkeys(h for c in self.verified(roles) for h in c.bound_hosts))

    def host_roles(self, host: str) -> list[str]:
        """Verified roles of the utility whose own host this is (empty: not a verified
        utility's host)."""
        host = host.casefold().removeprefix("www.")
        return [r for c in self.candidates if c.status in ("VERIFIED", "SANITATION_ONLY")
                and host in c.bound_hosts for r in c.verified_roles]
