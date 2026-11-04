"""Geographic identity of a target city: City + Region + Country, with verified alternative
names, context names (metropolitan areas) and homonyms (other places with the same name).

Read from ``config/city_identity.yaml``, which ``scripts/build_city_identity.py`` drafts from
independent geographic sources (GeoNames, Wikidata) and a person approves. It never comes from
the reference workbook, and it is never a research answer: it is used to recognise which place
a text is about and to phrase searches, never to fill a field value.

Rules kept by the model (and tested):
- every name carries its provenance;
- a name in ``names`` (used for matching) is confirmed by two independent sources, or is an
  official / native name stated by Wikidata (P1448 / P1705); single-source names stay in
  ``unverified_names`` and are never used;
- metropolitan-area and region names are context, never the city itself;
- only APPROVED entries are used by research unless a caller explicitly accepts drafts; without
  an approved entry the identity is the catalog's city and country alone (``CATALOG_ONLY``).
"""

import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Literal
import yaml
from pydantic import BaseModel, Field, model_validator
from water_source_research.core.paths import CONFIG_DIR

IDENTITY_PATH = CONFIG_DIR / "city_identity.yaml"

NameKind = Literal["OFFICIAL_LOCAL", "ALTERNATE", "HISTORICAL_SPELLING", "ABBREVIATION"]
Status = Literal["DRAFT", "APPROVED", "AMBIGUOUS", "NOT_FOUND", "CATALOG_ONLY"]
# Wikidata properties that state an official or native name on their own.
OFFICIAL_PROPERTIES = frozenset({"P1448", "P1705"})

def fold(text: str) -> str:
    """Accent-, case- and spacing-insensitive form ("Bogotá" -> "bogota")."""
    t = unicodedata.normalize("NFKD", text)
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return " ".join(t.casefold().replace("-", " ").replace("’", "'").split())

class NameSource(BaseModel):
    source: Literal["geonames", "wikidata", "iso3166", "person"]
    id: str
    property: str | None = None  # e.g. "alternatenames", "P1448", "label:es", "alias:en"
    detail: str | None = None

class IdentityName(BaseModel):
    name: str
    kind: NameKind = "ALTERNATE"
    lang: str | None = None
    sources: list[NameSource] = Field(min_length=1)
    # Other entities this exact string also names (e.g. "New York" = the state too): a match on
    # it alone is never CONFIRMED.
    ambiguous_with: list[str] = Field(default_factory=list)

    @property
    def independently_confirmed(self) -> bool:
        kinds = {s.source for s in self.sources}
        official = any(s.source == "wikidata" and (s.property or "").split(":")[0]
                       in OFFICIAL_PROPERTIES for s in self.sources)
        return len(kinds) >= 2 or official or "person" in kinds

class Region(BaseModel):
    """The country's first-level jurisdiction (state, province, region, ...)."""
    name: str
    iso_3166_2: str | None = None
    level_term: str | None = None  # the first-level term from config/admin_levels.yaml
    names: list[IdentityName] = Field(default_factory=list)
    sources: list[NameSource] = Field(default_factory=list)
    verified: bool = False  # both sources name the same first-level unit

    def all_names(self) -> list[str]:
        return list(dict.fromkeys([self.name] + [n.name for n in self.names
                                                 if n.independently_confirmed]))

class CountryInfo(BaseModel):
    name: str
    iso2: str | None = None
    iso3: str | None = None
    cctld: str | None = None  # without the dot, e.g. "cl"
    names: list[str] = Field(default_factory=list)  # other spellings ("USA", "Chili")
    languages: list[str] = Field(default_factory=list)  # ISO 639-1 codes, main first

    def all_names(self) -> list[str]:
        return list(dict.fromkeys([self.name] + self.names))

class ContextName(BaseModel):
    """A metropolitan area, urban agglomeration or similar: helps searches and supports a match,
    never identical to the city."""
    name: str
    kind: Literal["METRO_AREA", "URBAN_AREA", "OTHER_CONTEXT"] = "METRO_AREA"
    lang: str | None = None
    sources: list[NameSource] = Field(min_length=1)

class Homonym(BaseModel):
    """Another populated place with the same (or a qualified form of the) name."""
    name: str
    country: str
    iso2: str | None = None
    region: str | None = None
    population: int | None = None
    sources: list[NameSource] = Field(min_length=1)

class OfficialWebsite(BaseModel):
    """A government website related to the city (Wikidata P856 of the city item, or a
    person's review). Only a VERIFIED MUNICIPAL site can corroborate that an organisation is a
    city department; regional or provincial government sites are recorded, never used for
    that. Kept apart from the geographic identity: a missing or unverified municipal site
    never blocks approving the identity."""
    host: str  # lower case, without "www."
    url: str
    level: Literal["MUNICIPAL", "REGIONAL", "PROVINCIAL", "NATIONAL", "UNKNOWN"] = "MUNICIPAL"
    verified: bool = True  # False: recorded, pending independent confirmation
    note: str | None = None
    sources: list[NameSource] = Field(min_length=1)

    @property
    def municipal_authority(self) -> bool:
        return self.level == "MUNICIPAL" and self.verified

class EntryApproval(BaseModel):
    """Who approved an identity entry, when, and on what basis."""
    approved_by: str
    approved_at: str
    basis: str = ""

class CityIdentity(BaseModel):
    city: str  # the catalog's city name
    country: str  # the catalog's country name
    status: Status = "DRAFT"
    region: Region | None = None
    country_info: CountryInfo | None = None
    official_websites: list[OfficialWebsite] = Field(default_factory=list)
    approval: EntryApproval | None = None
    ids: dict[str, str] = Field(default_factory=dict)
    names: list[IdentityName] = Field(default_factory=list)
    unverified_names: list[IdentityName] = Field(default_factory=list)
    context_names: list[ContextName] = Field(default_factory=list)
    homonyms: list[Homonym] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _names_are_confirmed(self) -> "CityIdentity":
        weak = [n.name for n in self.names if not n.independently_confirmed]
        if weak:
            raise ValueError(f"{self.city}: names without independent confirmation must be in "
                             f"unverified_names: {weak}")
        return self

    @property
    def key(self) -> str:
        return f"{fold(self.city)}|{fold(self.country)}"

    def catalog_names(self) -> list[str]:
        """The catalog's own name forms: "Pune (Poona)" -> ["Pune", "Poona"]."""
        if "(" in self.city and self.city.endswith(")"):
            head, _, tail = self.city[:-1].partition("(")
            return [head.strip(), tail.strip()]
        return [self.city]

    def city_names(self) -> list[str]:
        """The names that identify the city itself: catalog forms first, then verified names."""
        return list(dict.fromkeys(self.catalog_names() + [n.name for n in self.names]))

    def unambiguous_city_names(self) -> list[str]:
        ambiguous = {fold(n.name) for n in self.names if n.ambiguous_with}
        return [n for n in self.city_names() if fold(n) not in ambiguous]

    def country_names(self) -> list[str]:
        return self.country_info.all_names() if self.country_info else [self.country]

    def region_names(self) -> list[str]:
        return self.region.all_names() if self.region else []

    def context_place_names(self) -> list[str]:
        return [c.name for c in self.context_names]

def catalog_only(city: str, country: str) -> CityIdentity:
    """The identity when no approved entry exists: the catalog's city and country only."""
    return CityIdentity(city=city, country=country, status="CATALOG_ONLY",
                        country_info=CountryInfo(name=country))

@lru_cache
def load_city_identities(path: Path = IDENTITY_PATH) -> tuple[CityIdentity, ...]:
    if not path.exists():
        return ()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return tuple(CityIdentity.model_validate(c) for c in data.get("cities", []))

def identity_for(city: str | None, country: str | None, *, allow_draft: bool = False,
                 identities: tuple[CityIdentity, ...] | None = None) -> CityIdentity:
    """The approved identity of a run's city (by catalog city name or a parenthesised part,
    and country), else ``catalog_only``. Drafts are used only with ``allow_draft``."""
    city, country = (city or "").strip(), (country or "").strip()
    want = (fold(city), fold(country))
    usable = {"APPROVED"} | ({"DRAFT"} if allow_draft else set())
    for ident in identities if identities is not None else load_city_identities():
        if ident.status not in usable:
            continue
        names = {fold(n) for n in ident.catalog_names()} | {fold(ident.city)}
        countries = {fold(c) for c in ident.country_names()} | {fold(ident.country)}
        if want[0] in names and want[1] in countries:
            return ident
    return catalog_only(city, country)
