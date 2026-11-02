"""Builders for normalization/resolution tests: evidence chunks, cited claims, and a state."""

from itertools import count
from water_source_research.schemas.common import ValueOrigin, ValueStatus
from water_source_research.schemas.research import (
    CityResearchState, EvidenceRef, FieldClaim,
)
from water_source_research.services.field_state import (
    initialize_city_state, load_field_definitions,
)
from water_source_research.services.normalization import EvidenceIndex, normalize_research
from water_source_research.services.resolution import resolve_research

FIELDS = load_field_definitions()
YEAR = 2026
_ids = count(1)

class World:
    """Acquired evidence of one run: chunks (with source types) and document records."""

    def __init__(self) -> None:
        self.chunks: list[dict] = []
        self.documents: list[dict] = []

    def doc(self, url: str, text: str, source_type: str = "UTILITY", page: int | None = None,
            source_date: str | None = None, status: str = "OK") -> str:
        self.chunks.append({"chunk_id": f"c{next(_ids)}", "url": url, "text": text,
                            "page_number": page, "source_type": source_type})
        self.documents.append({"requested_url": url, "final_url": url, "status": status,
                               "source_type": source_type, "source_date": source_date})
        return url

    def serving_utility(self, url: str, name: str = "Toronto Water", city: str = "Toronto",
                        country: str = "Canada") -> str:
        """An explicit statement on the utility's own site that it serves the city (with the
        country, so it holds without an approved city identity): since normalize-6 a UTILITY
        page is an official source only for a utility verified this way."""
        page = url.rstrip("/") + "/about-us"
        return self.doc(page, f"{name} supplies drinking water to {city}, {country}.")

    @property
    def index(self) -> EvidenceIndex:
        return EvidenceIndex(self.chunks, self.documents)

def claim(field_key: str, value, *cites: tuple[str, str], confidence: float = 0.9,
          status: ValueStatus = ValueStatus.KNOWN, page: int | None = None,
          origin: ValueOrigin = ValueOrigin.REPORTED) -> FieldClaim:
    """A claim citing (url, quote) pairs."""
    return FieldClaim(field_key=field_key, value=value, value_status=status,
                      value_origin=origin, confidence=confidence,
                      evidence=[EvidenceRef(url=u, passage=q, page_number=page)
                                for u, q in cites])

def state_with(*claims: FieldClaim) -> CityResearchState:
    research = initialize_city_state("Toronto", "Canada")
    for c in claims:
        research.fields[c.field_key].candidate_claims.append(c)
    return research

def normalize(world: World, *claims: FieldClaim) -> CityResearchState:
    return normalize_research(state_with(*claims), FIELDS, world.index, current_year=YEAR)

def resolve(world: World, *claims: FieldClaim):
    """(resolved state, log) after normalization and resolution."""
    return resolve_research(normalize(world, *claims), FIELDS, world.index)

def normalized_claim(world: World, c: FieldClaim) -> FieldClaim:
    return normalize(world, c).fields[c.field_key].candidate_claims[0]

def checks(c: FieldClaim) -> dict[str, str]:
    return {k.code: k.result.value for k in c.validation.checks}
