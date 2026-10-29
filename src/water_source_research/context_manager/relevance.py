"""Is an evidence passage about the target city or its water system? (the extraction gate)

A field is sent to the model only when at least one passage selected for it is relevant. The
rules are OR-ed and deliberately generous, so an uncertain case is sent; relevance scores are
never used (a weak passage that names another legitimate source must still reach the model):

- CITY_NEARBY        the passage's matching sentences (+/-1) name the city or its country more
                     than other places, or as often (TARGET / MIXED locality).
- DOC_CONTEXT        its document names the city: any chunk of the same web page, or for a PDF
                     the same or an adjacent page (a 150-page national report mentioning a
                     same-named town somewhere doesn't make every page local).
- CITY_DOMAIN        it comes from the city's own website (toronto.ca, cityofhouston.gov):
                     pages there often don't repeat the city's name.
- VERIFIED_UTILITY   it comes from the website of a utility whose service of the city is
                     stated in the run's evidence (the Milestone 1 UTILITY_SERVES_CITY test)
                     and whose name the host spells. A UTILITY classification alone is not
                     enough.
- LOCAL_WATER_FEATURE  for fields about the water system: it names one of the city's known
                     water features, i.e. a water body named in a sentence about the city, a
                     water_source / reservoir_names candidate or a target of their stated
                     relations, or the city's jurisdiction. Admits basin and upstream evidence
                     ("the Maipo basin ...", "Ndakaini Dam on the Thika River") that never names
                     the city.

A passage whose matching sentences name only other places (OTHER) is never relevant.
"""

import hashlib
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit
from water_source_research.discovery.normalize import host_names_org, is_city_domain
from water_source_research.schemas.research import CityResearchState, EvidenceChunk, FieldDefinition
from water_source_research.services.places import HYDRONYM, city_pattern, without_water_bodies

CITY_NEARBY = "CITY_NEARBY"
DOC_CONTEXT = "DOC_CONTEXT"
CITY_DOMAIN = "CITY_DOMAIN"
VERIFIED_UTILITY = "VERIFIED_UTILITY"
LOCAL_WATER_FEATURE = "LOCAL_WATER_FEATURE"
RULES = (CITY_NEARBY, DOC_CONTEXT, CITY_DOMAIN, VERIFIED_UTILITY, LOCAL_WATER_FEATURE)

# State fields the rules read (beyond the target): they are part of the bundle cache key.
GATE_FIELDS = ("utility_name", "water_source", "reservoir_names", "jurisdiction")
_SOURCE_FIELDS = ("water_source", "reservoir_names")
# Words that don't name a particular feature: a candidate made only of these ("groundwater",
# "mountain spring water") would make every passage on the topic look local.
_GENERIC = frozenset(["the", "a", "an", "and", "of", "de", "del", "la", "el", "los", "las", "da", "do", "dos", "das", "di", "du", "des", "le", "groundwater", "ground", "surface", "water", "waters", "river", "rivers", "lake", "lakes", "spring", "springs", "aquifer", "aquifers", "well", "wells", "wellfield", "wellfields", "field", "fields", "reservoir", "reservoirs", "dam", "dams", "creek", "creeks", "stream", "streams", "basin", "basins", "catchment", "watershed", "source", "sources", "supply", "supplies", "mountain", "mountains", "local", "municipal", "public", "drinking", "raw", "fresh", "sea", "ocean", "desalination", "reclaimed", "plant", "plants", "system", "systems", "region", "regional", "area", "areas", "province", "state", "county", "district", "city", "metropolitan", "national", "main", "other", "various", "several"])
_SENTENCE = re.compile(r"(?<=[.!?;])\s+|\n+")

def water_system_field(field: FieldDefinition) -> bool:
    """Fields about the city's water system, for which upstream/regional evidence counts."""
    return field.subject == "supply_record" or field.key.startswith("total_city_water_withdrawals")

def name_key(name: str) -> str:
    # imported here: services.resolution imports the context manager (via normalization)
    from water_source_research.services.resolution import name_key as key
    return key(name)

def _names_city(text: str, city: str) -> bool:
    """The city is named outside water-body names ("Lake Houston" isn't Houston): the TARGET
    case of services/places.py::names_city, without building the other-places pattern."""
    pattern = city_pattern(city)
    return bool(pattern.search(text)) and bool(pattern.search(without_water_bodies(text)))

def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").removeprefix("www.")

def _feature_key(value: object) -> str | None:
    key = name_key(str(value))
    words = key.split()
    if len(key) < 4 or not words or all(w in _GENERIC for w in words):
        return None
    return key

@dataclass(frozen=True, slots=True)
class RelevanceContext:
    city_pages: Mapping[str, frozenset[int | None]]  # url -> pages (None = web page) naming the city
    city_hosts: frozenset[str]  # the city's own website(s)
    verified_hosts: frozenset[str]
    features: frozenset[str]  # name_keys of the city's known water features

    def doc_names_city(self, chunk: EvidenceChunk) -> bool:
        pages = self.city_pages.get(chunk.url)
        if not pages:
            return False
        if chunk.page_number is None:
            return True
        p = chunk.page_number
        return bool({p - 1, p, p + 1} & pages)

    def names_feature(self, text: str) -> bool:
        if not self.features:
            return False
        padded = f" {name_key(text)} "
        return any(f" {f} " in padded for f in self.features)

    def rules(self, chunk: EvidenceChunk, locality: str | None, passage: str,
              field: FieldDefinition) -> list[str]:
        """The rules a passage meets for ``field`` (empty = not relevant)."""
        if locality == "OTHER":
            return []
        out = []
        if locality in ("TARGET", "MIXED"):
            out.append(CITY_NEARBY)
        if self.doc_names_city(chunk):
            out.append(DOC_CONTEXT)
        host = _host(chunk.url)
        if host in self.city_hosts:
            out.append(CITY_DOMAIN)
        if host in self.verified_hosts:
            out.append(VERIFIED_UTILITY)
        if water_system_field(field) and self.names_feature(passage):
            out.append(LOCAL_WATER_FEATURE)
        return out

def _claim_values(state: CityResearchState, key: str) -> list[str]:
    fs = state.fields.get(key)
    if fs is None:
        return []
    claims = [c for c in fs.candidate_claims if c.review_status != "REJECTED"]
    if fs.selected_claim is not None:
        claims.append(fs.selected_claim)
    return [str(c.value) for c in claims if c.value is not None]

def _relation_targets(state: CityResearchState) -> list[str]:
    out = []
    for key in _SOURCE_FIELDS:
        fs = state.fields.get(key)
        for c in (fs.candidate_claims if fs else []):
            for r in (c.qualifiers.relations if c.qualifiers else None) or []:
                if r.get("target"):
                    out.append(str(r["target"]))
    return out

def build_relevance_context(state: CityResearchState,
                            chunks: Sequence[EvidenceChunk]) -> RelevanceContext:
    city, country = state.city_name, state.country
    pages: dict[str, set[int | None]] = {}
    hydronyms: set[str] = set()
    for c in chunks:
        if not _names_city(c.text, city):
            continue
        pages.setdefault(c.url, set()).add(c.page_number)
        for s in _SENTENCE.split(c.text):
            if s.strip() and _names_city(s, city):
                hydronyms.update(m.group(0) for m in HYDRONYM.finditer(s))
    excluded = {name_key(x) for x in (city, country) if x}
    features = {k for v in (*hydronyms, *(v for f in _SOURCE_FIELDS
                                          for v in _claim_values(state, f)),
                            *_relation_targets(state), *_claim_values(state, "jurisdiction"))
                if (k := _feature_key(v)) and k not in excluded}
    from water_source_research.services.semantics import service_statement_url
    hosts = {h for c in chunks if (h := _host(c.url))}
    verified: set[str] = set()
    for name in dict.fromkeys(_claim_values(state, "utility_name")):
        corroborating = {h for h in hosts if host_names_org(name, h)}
        if corroborating and service_statement_url(((c.text, c.url) for c in chunks),
                                                   name, city):
            verified |= corroborating
    return RelevanceContext({u: frozenset(p) for u, p in pages.items()},
                            frozenset(h for h in hosts if is_city_domain(h, city)),
                            frozenset(verified), frozenset(features))

def context_signature(state: CityResearchState, chunks: Iterable[EvidenceChunk]) -> str:
    """Memo key for ``build_relevance_context``: everything it reads."""
    h = hashlib.sha256()
    h.update(f"{state.city_name}|{state.country}".encode())
    for key in GATE_FIELDS:
        h.update(f"|{key}:".encode())
        for v in sorted(_claim_values(state, key)):
            h.update(v.encode() + b"\x00")
    for v in sorted(_relation_targets(state)):
        h.update(v.encode() + b"\x01")
    for c in chunks:
        h.update(c.chunk_id.encode())
    return h.hexdigest()
