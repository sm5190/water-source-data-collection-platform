"""The run's water-supply relationship graph (schemas/relationships.py): deterministic, built
from stored evidence only, for provenance and explanation (no acceptance rule reads it).

Two inputs, no duplicated logic:
- Utility relationships (UTILITY_SERVES_CITY, SUPPLIES_WHOLESALE_TO, PREDECESSOR_OF) are the
  utility registry's own sentence evidence (services/utility_registry.py), with its role rules
  unchanged: a contractor, a sanitation-only service or a wholesaler never becomes a retail
  drinking-water operator. Utilities named only by a research claim are CANDIDATEs.
- Source relationships (SOURCE_SUPPLIES_UTILITY / _CITY, SOURCE_PART_OF_SYSTEM, ALIAS_OF) come
  from sentences that name both entities joined by an explicit predicate ("takes its water
  from", "se abastece del", "bezieht ... aus", "is one of the springs of", ...), and from the
  extraction's grounded ``part_of_system`` relations. Two names in one sentence without a
  predicate are never a relationship.

Geography uses the passage (services/geo_match.py: the sentence, else its section heading):
a homonym's or another place's sentence is rejected (kept in ``rejected``), an uncertain one is
at most CANDIDATE. A source's own location (upstream, another region or country) is never
checked: a phrase locating the source ("springs in the X Alps") is removed before the city
check. Statuses and transitions: schemas/relationships.py.
"""

import re
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlsplit
from water_source_research.schemas.relationships import (
    Entity, RejectedStatement, RelationshipEvidence, RelationshipGraph, SupplyRelationship,
)
from water_source_research.schemas.research import CityResearchState, EvidenceChunk
from water_source_research.schemas.utility import UtilityRegistry
from water_source_research.services.city_identity import catalog_only, fold
from water_source_research.services.geo_match import (
    UNKNOWN_SCOPE, GeoMatcher, document_scopes, geo_basis, identity_signature, matcher_for,
)
from water_source_research.services.places import HYDRONYM
from water_source_research.services.resolution import name_key
from water_source_research.services.utility_registry import (
    GOVERNMENT_TYPES, WHOLESALE, _name_re, build_registry,
)

RELATIONSHIP_POLICY_VERSION = "relationship-1"
SENTENCE = re.compile(r"(?<=[.!?;])\s+|\n+")
GAP = 90  # characters allowed between an entity mention and the predicate

# --- what the evidence states ------------------------------------------------------------

# "<consumer> <predicate> <source>": the consumer takes water from the source.
CONSUMER_FIRST = re.compile(
    r"\b(?:(?:takes?|took|draws?|drew|obtains?|obtained|gets?|got|receives?|received|"
    r"sources?|sourced|abstracts?|withdraws?|pumps?|pumped)"
    r"\s+(?:(?:its|their|most|all|much|part|some)\s+(?:of\s+)?(?:its|their)?\s*)?"
    r"(?:raw\s+|drinking\s+|potable\s+|surface\s+|ground\s*)?water\s+(?:supply\s+)?from"
    r"|(?:is|are|was|were)\s+(?:mainly\s+|mostly\s+|primarily\s+|largely\s+|partly\s+)?"
    r"(?:supplied|fed|served|provided)\s+(?:with\s+(?:\w+\s+)?water\s+)?(?:by|from)"
    r"|relies\s+(?:mainly\s+|mostly\s+)?on"
    r"|(?:'s|’s)\s+(?:drinking\s+|tap\s+|raw\s+)?water\s+(?:supply\s+)?comes\s+from"
    r"|(?:supplies|provides|delivers)\s+[^.;]{0,60}?\bwith\s+(?:\w+\s+)?water\s+from"
    r"|(?:capta|captan|toma|toman|obtiene|obtienen|extrae|extraen)\s+(?:el\s+|su\s+)?"
    r"(?:agua|recurso)?\s*(?:potable\s+|cruda\s+)?(?:del|desde|de)\b"
    r"|se\s+(?:abastece|abastecen|surte|surten|alimenta|alimentan)\s+(?:principalmente\s+)?"
    r"(?:del|desde|de|con)\b"
    r"|(?:es|son)\s+abastecid[oa]s?\s+(?:principalmente\s+)?(?:por|desde|con|del|de)"
    r"|capta\s+[aá]gua\s+(?:do|da|de)|[ée]\s+abastecid[oa]\s+(?:pelo|pela|por)"
    r"|bezieht\s+(?:sein|ihr)?\s*(?:Trink)?[Ww]asser\s+(?:\w+\s+){0,3}aus"
    r"|wird\s+(?:\w+\s+){0,3}(?:aus|von)\s+(?=\S)"
    r"|s['’]approvisionne\s+(?:en\s+eau\s+)?(?:dans|à|au|aux|de|du|des)"
    r"|puise\s+(?:son\s+|leur\s+)?eau\s+(?:dans|de|du|des))",
    re.IGNORECASE)
# "<source> <predicate> <consumer>": the source supplies the consumer.
SOURCE_FIRST = re.compile(
    r"\b(?:(?:supplies|supply|supplied|provides|provide|provided|delivers|feeds|feed|serves)"
    r"\s+(?:\w+\s+){0,3}?(?:water\s+)?(?:to|for)?"
    r"|(?:is|are|was|were|remains?)\s+(?:the|a|an|one\s+of\s+the)\s+(?:main|primary|principal|"
    r"major|sole|only|largest|second|key|important)?\s*(?:drinking[\s-]water\s+|water\s+)?"
    r"(?:source|sources|supply|supplies)\s+(?:of\s+(?:drinking\s+)?water\s+)?(?:for|of|to)"
    r"|abastece\s+(?:de\s+agua\s+)?(?:a|al)?|abastecen\s+(?:de\s+agua\s+)?(?:a|al)?"
    r"|(?:es|son)\s+(?:la|las|el|los|una|uno)\s+(?:principal(?:es)?\s+)?fuentes?\s+"
    r"(?:de\s+agua\s+)?(?:de|del|para)"
    r"|versorgt|versorgen|liefert\s+(?:\w+\s+){0,3}(?:an|für)"
    r"|alimente|alimentent|approvisionne)\b",
    re.IGNORECASE)
PART_OF = re.compile(
    r"\b(?:(?:is|are|forms?|was|were)\s+(?:a|an|one)?\s*(?:part|component|section|element|"
    r"branch)s?\s+of"
    r"|,?\s*one\s+of\s+the\s+(?:\w+\s+){0,2}(?:springs|sources|wells|wellfields|reservoirs|"
    r"intakes|catchments)\s+(?:of|feeding|supplying|for)"
    r"|forman?\s+parte\s+(?:del?|de\s+la|de\s+los|de\s+las)"
    r"|(?:ist|sind)\s+(?:ein\s+|eine\s+)?Teil\s+(?:der|des|von)"
    r"|fait\s+partie\s+(?:du|de\s+la|des))",
    re.IGNORECASE)
CONTAINS = re.compile(
    r"\b(?:comprises|consists\s+of|is\s+made\s+up\s+of|is\s+fed\s+by\s+the\s+springs|"
    r"umfasst|besteht\s+aus|comprende|est[aá]\s+formad[oa]\s+por)\b", re.IGNORECASE)
ALIAS = re.compile(
    r"\b(?:also\s+known\s+as|a\.k\.a\.|formerly\s+known\s+as|tambi[eé]n\s+conocid[oa]\s+como|"
    r"auch\s+bekannt\s+als|anteriormente\s+(?:llamad[oa]|conocid[oa]\s+como))\b", re.IGNORECASE)
# Where a "comprises ..." list ends: the next clause is about something else.
CLAUSE_END = re.compile(r";|,?\s+(?:while|whereas|but|whilst|mientras|während|alors\s+que)\b"
                        r"|\.\s", re.IGNORECASE)
NEGATION = re.compile(
    r"\b(?:not|never|no\s+longer|no\s+plans?\s+to|ceased\s+to|stopped|ya\s+no|no\s+(?:se\s+)?(?:abastece|capta|"
    r"toma)|nicht(?:\s+mehr)?|n['’]est\s+plus|ne\s+\w+\s+plus)\b", re.IGNORECASE)
DISCONTINUED = re.compile(
    r"\b(?:no\s+longer|ceased|stopped|decommissioned|abandoned|replaced\s+by|ya\s+no|"
    r"nicht\s+mehr|stillgelegt|n['’]est\s+plus|fuera\s+de\s+servicio)\b", re.IGNORECASE)
HISTORICAL = re.compile(
    r"\b(?:until|till|up\s+to|hasta|bis)\s+(?:19|20)\d{2}\b|\b(?:19|20)\d{2}\s*[-–]\s*"
    r"(?:19|20)\d{2}\b|\b(?:formerly|previously|former|in\s+the\s+past|historically|"
    r"anteriormente|antiguamente|antes\s+de\s+(?:19|20)\d{2}|fr[üu]her|ehemals|ehemalig\w*|"
    r"autrefois)\b", re.IGNORECASE)
CURRENT = re.compile(
    r"\b(?:currently|today|now|at\s+present|presently|actualmente|hoy|hoy\s+en\s+d[ií]a|"
    r"derzeit|heute|aktuell|actuellement|aujourd['’]hui)\b", re.IGNORECASE)
YEAR = re.compile(r"\b(?:19|20)\d{2}\b")

# Source and system names the evidence uses, beyond the run's claims.
SOURCE_LOWER = re.compile(
    r"\b(?:r[ií]o|lago|laguna|embalse|acu[ií]fero|fluss|see|lac|rivi[eè]re|fleuve)\s+"
    r"(?:de\s+(?:la\s+|los\s+)?|del\s+|do\s+|da\s+)?[A-ZÀ-ÖØ-Þ][\w'’\-]+", re.IGNORECASE)
SYSTEM = re.compile(
    r"\b(?:(?:[IVX]+\.|First|Second|Third|[A-ZÀ-ÖØ-Þ][\w'’\-]+)\s+){1,5}"
    r"(?:Pipeline|Aqueduct|Water\s+Supply\s+System|Water\s+System|Supply\s+Scheme|"
    r"Spring\s+(?:Water\s+)?(?:Pipeline|Main)|Acueducto|Aqueduc)\b"
    r"|\b(?:[IVX]+\.\s+)?(?:[A-ZÀ-ÖØ-Þ][\w'’\-]+\s+){0,3}[A-ZÀ-ÖØ-Þ]\w*leitung\b")
# A phrase locating a source ("in the X Alps", "en la cordillera de Y"): removed before the
# city check, so the source's location never decides the passage's place.
FEATURE_LOCATION = re.compile(
    r"\b(?:in|im|en|dans|near|above|upstream\s+(?:of|from))\s+(?:the\s+|la\s+|el\s+|den\s+|"
    r"der\s+|le\s+|les\s+)?(?:[A-ZÀ-ÖØ-Þ][\w'’\-]+\s*){1,3}")
_KIND_WORDS = [("spring", r"spring|quelle|manantial|vertiente|source\b"),
               ("aquifer", r"aquifer|acu[ií]fero|grundwasser"),
               ("wellfield", r"wellfield|well\s+field|wells\b"),
               ("reservoir", r"reservoir|embalse|dam\b|talsperre|stausee|barrage|represa"),
               ("lake", r"\blake|\blago|\blac\b|laguna|\bsee\b"),
               ("river", r"river|\br[ií]o\b|fluss|rivi[eè]re|fleuve|creek")]

def _infer_kind(name: str) -> str | None:
    for kind, pattern in _KIND_WORDS:
        if re.search(pattern, name, re.IGNORECASE):
            return kind
    return None

def _host(url: str) -> str:
    return (urlsplit(url or "").hostname or "").casefold().removeprefix("www.")

def _temporal(text: str) -> tuple[str, str | None, list[int], bool]:
    """(temporal, marker, years, negated) of a statement. HISTORICAL only from explicit
    historical scope or discontinuation; CURRENT only from an explicit current marker; past
    tense alone stays UNKNOWN."""
    years = sorted({int(y) for y in YEAR.findall(text)})
    negated = bool(NEGATION.search(text))
    if m := DISCONTINUED.search(text):
        return "HISTORICAL", m.group(0), years, True
    if m := HISTORICAL.search(text):
        return "HISTORICAL", m.group(0), years, negated
    if m := CURRENT.search(text):
        return "CURRENT", m.group(0), years, negated
    return "UNKNOWN", None, years, negated

# --- the builder ---------------------------------------------------------------------------

class _Builder:
    def __init__(self, research: CityResearchState, chunks: Sequence[EvidenceChunk],
                 documents: Mapping[str, Mapping[str, Any]], registry: UtilityRegistry,
                 document_ids: Mapping[str, int],
                 links: Mapping[str, Sequence[Mapping[str, Any]]] | None = None):
        self.research, self.chunks, self.registry = research, list(chunks), registry
        self.documents, self.document_ids = documents, document_ids
        city, country = research.city_name, research.country
        self.matcher = matcher_for(city, country) or GeoMatcher(catalog_only(city, country or ""))
        self.scopes = document_scopes(self.matcher, self.chunks)
        self.by_chunk = {c.chunk_id: c for c in self.chunks}
        self.city = Entity(entity_id=f"city:{fold(city)}|{fold(country or '')}", type="city",
                           name=city)
        self.entities: dict[str, Entity] = {self.city.entity_id: self.city}
        self.found: dict[str, SupplyRelationship] = {}
        self.rejected: list[RejectedStatement] = []
        self.utilities = {c.key: c for c in registry.candidates
                          if c.status not in ("NON_SERVING", "OTHER_CITY", "SANITATION_ONLY")}
        self.utility_res = {k: _name_re(c.all_names()) for k, c in self.utilities.items()}
        self.sources = self._known_sources()
        from water_source_research.services.affiliation import assess
        self.affiliations = assess(self.matcher, registry.candidates, self.chunks, links or {},
                                   city, documents)
        self.department_hosts = {h for a in self.affiliations if a.verified
                                 for c in [registry.get(a.candidate_key)] if c
                                 for h in c.bound_hosts}

    # entities ---------------------------------------------------------------------------

    def _known_sources(self) -> dict[str, tuple[str, str | None]]:
        """name_key -> (name, kind) of the run's water_source / reservoir_names claims."""
        out: dict[str, tuple[str, str | None]] = {}
        for field in ("water_source", "reservoir_names"):
            fs = self.research.fields.get(field)
            for c in (fs.candidate_claims if fs else []):
                if c.value is None or c.review_status == "REJECTED":
                    continue
                kind = (c.qualifiers.source_kind if c.qualifiers else None) or _infer_kind(
                    str(c.value)) or ("reservoir" if field == "reservoir_names" else None)
                out.setdefault(name_key(str(c.value)), (str(c.value), kind))
        return out

    def source_entity(self, name: str, kind: str | None = None) -> Entity:
        name = re.sub(r"^(?:the|el|la|los|las|der|die|das|le|les)\s+", "", name.strip(),
                      flags=re.IGNORECASE)
        key = name_key(name)
        known = self.sources.get(key)
        system = bool(SYSTEM.fullmatch(name))
        kind = kind or (known[1] if known else None) or ("supply_system" if system
                                                         else _infer_kind(name))
        etype = "system" if kind == "supply_system" else "source"
        eid = f"{etype}:{kind or 'unspecified'}:{key}"
        return self.entities.setdefault(eid, Entity(entity_id=eid, type=etype, name=known[0]
                                                    if known else name, kind=kind))

    def utility_entity(self, key: str) -> Entity:
        cand = self.registry.get(key)
        eid = f"utility:{key}"
        return self.entities.setdefault(eid, Entity(entity_id=eid, type="utility",
                                                    name=cand.name if cand else key))

    # evidence ---------------------------------------------------------------------------

    def _doc(self, url: str) -> Mapping[str, Any]:
        return self.documents.get(url) or {}

    def evidence(self, chunk: EvidenceChunk | None, url: str, quote: str, predicate: str,
                 geo_status: str, geo_reason: str, placed: bool, strength: str,
                 self_hosts: Sequence[str]) -> RelationshipEvidence:
        host = _host(url)
        stype = (self._doc(url).get("source_type") or (chunk.source_type if chunk else None)
                 or "").upper() or None
        official = self.matcher.official_host(host)  # the city government's own site
        basis = ("GOVERNMENT_OR_REGULATOR" if official else
                 "MUNICIPAL_DEPARTMENT" if host in self.department_hosts else
                 "SELF_DESCRIPTION" if host in self_hosts else "GOVERNMENT_OR_REGULATOR"
                 if stype in GOVERNMENT_TYPES else "INDEPENDENT")
        span = None
        if chunk is not None:
            i = chunk.text.find(quote[:80])
            if i >= 0:
                span = (i, i + len(quote))
        temporal, marker, years, negated = _temporal(quote)
        scope = self.scopes.get(url, UNKNOWN_SCOPE)
        return RelationshipEvidence(
            url=url, document_id=self.document_ids.get(url), chunk_id=chunk.chunk_id if chunk
            else None, page=chunk.page_number if chunk else None, quote=quote[:600], span=span,
            predicate=predicate[:120], geo_status=geo_status, geo_reason=geo_reason[:300],
            placed=placed, strength=strength, basis=basis, host=host, source_type=stype,
            document_scope=scope.label(), temporal=temporal, temporal_marker=marker,
            years=years, negated=negated)

    def add(self, kind: str, subject: Entity, obj: Entity, ev: RelationshipEvidence) -> None:
        key = f"{kind}|{subject.entity_id}|{obj.entity_id}"
        rel = self.found.get(key)
        if rel is None:
            rel = self.found[key] = SupplyRelationship(key=key, kind=kind, subject=subject,
                                                       object=obj, status="CANDIDATE")
        if not any(e.url == ev.url and e.quote == ev.quote for e in rel.evidence):
            rel.evidence.append(ev)

    def reject(self, kind: str, subject: str, obj: str, reason: str, url: str,
               quote: str) -> None:
        if not any(r.url == url and r.quote == quote[:400] for r in self.rejected):
            self.rejected.append(RejectedStatement(kind=kind, subject=subject, object=obj,
                                                   reason=reason[:300], url=url,
                                                   quote=quote[:400]))

    # utility relationships (the registry's evidence) --------------------------------------

    def from_registry(self) -> None:
        for cand in self.registry.candidates:
            if cand.status in ("NON_SERVING", "OTHER_CITY"):
                for e in cand.evidence:
                    if e.relationship in ("NON_SERVING_ROLE", "SERVES_OTHER"):
                        self.reject("UTILITY_SERVES_CITY", cand.name, self.city.name,
                                    f"{cand.status.lower()}: {e.relationship.lower()}"
                                    f"{' (' + e.role.lower() + ')' if e.role else ''}",
                                    e.url, e.quote)
                continue
            subject = self.utility_entity(cand.key)
            serving = False
            for e in cand.evidence:
                if e.relationship == "SERVES_OTHER" or e.relationship == "NON_SERVING_ROLE":
                    self.reject("UTILITY_SERVES_CITY", cand.name, self.city.name,
                                e.relationship.lower() + (f" ({e.role.lower()})" if e.role
                                                          else ""), e.url, e.quote)
                    continue
                if e.relationship not in ("SERVES", "SERVES_PART", "SUPPLIES_WHOLESALE"):
                    continue
                if e.role == "SANITATION_ONLY":
                    continue
                if e.geo_status in ("HOMONYM", "OTHER_PLACE"):
                    self.reject("UTILITY_SERVES_CITY", cand.name, self.city.name,
                                f"passage about another place ({e.geo_reason})", e.url,
                                e.quote)
                    continue
                wholesale = e.relationship == "SUPPLIES_WHOLESALE" or (
                    e.role == "HISTORICAL_OPERATOR" and WHOLESALE.search(e.quote))
                kind = "SUPPLIES_WHOLESALE_TO" if wholesale else "UTILITY_SERVES_CITY"
                obj = self._wholesale_object(cand.key, e.quote) if wholesale else self.city
                chunk = self.by_chunk.get(e.chunk_id or "")
                placed, why = self._statement_placed(cand, e, chunk)
                temporal, *_ = _temporal(e.quote)
                if e.role == "HISTORICAL_OPERATOR" and temporal != "HISTORICAL":
                    placed, why = False, why + "; past tense only"
                self.add(kind, subject, obj, self.evidence(
                    chunk, e.url, e.quote, e.relationship.lower().replace("_", " "),
                    e.geo_status, why, placed, e.strength, cand.bound_hosts))
                serving = True
            if not serving and "claim" in cand.origins:
                for ref, quote in self._claim_refs(cand.all_names()):
                    chunk = self._chunk_for(ref, quote)
                    self.add("UTILITY_SERVES_CITY", subject, self.city, self.evidence(
                        chunk, ref, quote, "utility_name claim", "NONE",
                        "a research claim, no service statement", False, "NONE",
                        cand.bound_hosts).model_copy(update={"basis": "CLAIM"}))
            for other_key in cand.predecessor_of:
                if other_key in {c.key for c in self.registry.candidates}:
                    statements = [e for e in cand.evidence if e.relationship in (
                        "PREDECESSOR_OF", "SUCCESSOR_OF")]
                    for e in statements:
                        chunk = self.by_chunk.get(e.chunk_id or "")
                        ev = self.evidence(chunk, e.url, e.quote, "predecessor statement",
                                           e.geo_status, e.geo_reason, e.geo_status in (
                                               "CONFIRMED", "PROBABLE"), e.strength,
                                           cand.bound_hosts)
                        self.add("PREDECESSOR_OF", subject, self.utility_entity(other_key),
                                 ev.model_copy(update={"temporal": "HISTORICAL",
                                                       "temporal_marker": "predecessor"}))

    def _statement_placed(self, cand, e, chunk: EvidenceChunk | None) -> tuple[bool, str]:
        """Does the statement itself place the service at the city? The sentence (the
        utility's own name removed) or its section heading must name the city; the page title
        or the document as a whole counts only for a government/regulatory document (whose
        title then states the city). Stricter than the registry, which also reads the title:
        the registry and acceptance are unchanged."""
        if e.geo_status not in ("CONFIRMED", "PROBABLE"):
            return False, e.geo_reason
        pattern = _name_re(cand.all_names())
        text = pattern.sub(" ", e.quote) if pattern is not None else e.quote
        scope = self.scopes.get(e.url, UNKNOWN_SCOPE)
        own = self.matcher.match_place(text, scope)
        if own.is_target:
            return True, own.reason
        if chunk is not None:
            local = self.matcher.section_place(chunk.text, max(0, chunk.text.find(
                e.quote[:60])), scope)
            if local is not None:
                return True, local.reason
        if ((e.source_type or "").upper() in GOVERNMENT_TYPES
                or self.matcher.official_host(e.url)) and not scope.elsewhere:
            return True, f"government document about the city ({e.geo_reason})"
        if _host(e.url) in self.department_hosts and not scope.elsewhere:
            return True, f"page of a verified city department ({e.geo_reason})"
        return False, f"only the page title or document places the city ({e.geo_reason})"

    def _wholesale_object(self, subject_key: str, quote: str) -> Entity:
        """Another utility the wholesale sentence names (bulk water to a retailer), else the
        city."""
        for key, pattern in self.utility_res.items():
            if key != subject_key and pattern is not None and pattern.search(quote):
                return self.utility_entity(key)
        return self.city

    def _claim_refs(self, names: Sequence[str]) -> list[tuple[str, str]]:
        fs = self.research.fields.get("utility_name")
        keys = {name_key(n) for n in names}
        out = []
        for c in (fs.candidate_claims if fs else []):
            if c.value is not None and c.review_status != "REJECTED" and name_key(
                    str(c.value)) in keys:
                out += [(r.url, r.passage or str(c.value)) for r in c.evidence]
        return out

    def _chunk_for(self, url: str, quote: str) -> EvidenceChunk | None:
        core = (quote or "").strip("… ")[:60]
        return next((c for c in self.chunks if c.url == url and core and core in c.text), None)

    # source relationships (sentences with an explicit predicate) ----------------------------

    def _mentions(self, sentence: str) -> tuple[list, list, list]:
        """(sources, utilities, city) mentions as (start, end, entity) in the sentence."""
        sources, seen = [], []
        names = [v[0] for v in self.sources.values()]
        known_re = _name_re(names) if names else None
        patterns = [p for p in (known_re, SYSTEM, HYDRONYM, SOURCE_LOWER) if p is not None]
        for p in patterns:
            for m in p.finditer(sentence):
                a, b = m.span()
                if any(a < y and x < b for x, y in seen):
                    continue
                seen.append((a, b))
                sources.append((a, b, self.source_entity(m.group(0).strip(" ,."))))
        utilities = []
        for key, p in self.utility_res.items():
            if p is None:
                continue
            for m in p.finditer(sentence):
                if not any(m.start() < y and x < m.end() for x, y in seen):
                    utilities.append((m.start(), m.end(), self.utility_entity(key)))
        city = []
        work = HYDRONYM.sub(lambda m: " " * len(m.group(0)), sentence)
        for m in self.matcher.city_re.finditer(work):
            if not any(m.start() < y and x < m.end() for x, y in seen + [
                    (u[0], u[1]) for u in utilities]):
                city.append((m.start(), m.end(), self.city))
        return sorted(sources, key=lambda t: t[0]), utilities, city

    def _place(self, sentence: str, chunk: EvidenceChunk, start: int, spans) -> tuple:
        """The city's place in this sentence with the entity names and the source-locating
        phrases removed; else its section heading."""
        text = sentence
        for a, b in sorted(spans, reverse=True):
            text = text[:a] + " " * (b - a) + text[b:]
        text = FEATURE_LOCATION.sub(lambda m: " " * len(m.group(0)), text)
        scope = self.scopes.get(chunk.url, UNKNOWN_SCOPE)
        place = self.matcher.match_place(text, scope)
        if place.status in ("NONE", "AMBIGUOUS"):
            local = self.matcher.section_place(chunk.text, start, scope)
            if local is not None:
                place = local
        return place, scope

    def from_sentences(self) -> None:
        for chunk in self.chunks:
            if not (HYDRONYM.search(chunk.text) or SOURCE_LOWER.search(chunk.text)
                    or SYSTEM.search(chunk.text) or self.sources):
                continue
            offset = 0
            for sentence in SENTENCE.split(chunk.text):
                start = chunk.text.find(sentence, offset)
                offset = max(offset, start)
                if len(sentence) < 12:
                    continue
                if not (CONSUMER_FIRST.search(sentence) or SOURCE_FIRST.search(sentence)
                        or PART_OF.search(sentence) or CONTAINS.search(sentence)
                        or ALIAS.search(sentence)):
                    continue
                sources, utilities, city = self._mentions(sentence)
                if not sources and not utilities:
                    continue
                self._statements(chunk, sentence, start, sources, utilities, city)

    def _statements(self, chunk, sentence, start, sources, utilities, city) -> None:
        consumers = utilities + city
        spans = [(a, b) for a, b, _ in sources + utilities]
        place = scope = None

        def located():
            nonlocal place, scope
            if place is None:
                place, scope = self._place(sentence, chunk, start, spans)
            return place, scope

        quote = " ".join(sentence.split())
        found = []
        for pm in CONSUMER_FIRST.finditer(sentence):
            c = max((m for m in consumers if m[1] <= pm.start() and pm.start() - m[1] <= GAP),
                    key=lambda m: m[1], default=None)
            s = min((m for m in sources if m[0] >= pm.end() and m[0] - pm.end() <= GAP),
                    key=lambda m: m[0], default=None)
            if c and s:
                found.append((s[2], c[2], pm.group(0), (c[0], s[1])))
        for pm in SOURCE_FIRST.finditer(sentence):
            s = max((m for m in sources if m[1] <= pm.start() and pm.start() - m[1] <= GAP),
                    key=lambda m: m[1], default=None)
            c = min((m for m in consumers if m[0] >= pm.end() and m[0] - pm.end() <= GAP),
                    key=lambda m: m[0], default=None)
            if c and s:
                found.append((s[2], c[2], pm.group(0), (s[0], c[1])))
        for src, consumer, predicate, (a, b) in found:
            kind = "SOURCE_SUPPLIES_CITY" if consumer.type == "city" else "SOURCE_SUPPLIES_UTILITY"
            p, sc = located()
            local = sentence[max(0, a - 40):b + 40]
            if sc.elsewhere or p.status in ("HOMONYM", "OTHER_PLACE"):
                self.reject(kind, src.name, consumer.name, f"not about the city: {p.reason}"
                            if not sc.elsewhere else f"document about {sc.label().lower()}",
                            chunk.url, quote)
                continue
            if NEGATION.search(local) and not DISCONTINUED.search(local) and \
                    not HISTORICAL.search(local):
                negated_only = True
            else:
                negated_only = False
            placed = p.status in ("CONFIRMED", "PROBABLE") if consumer.type == "city" else \
                p.status not in ("AMBIGUOUS",)
            self_hosts = []
            if consumer.type == "utility":
                cand = self.registry.get(consumer.entity_id.split(":", 1)[1])
                self_hosts = cand.bound_hosts if cand else []
            ev = self.evidence(chunk, chunk.url, quote, predicate, p.status, p.reason, placed,
                               "SUPPORTING" if placed else "NONE", self_hosts)
            if negated_only:
                ev = ev.model_copy(update={"negated": True})
            self.add(kind, src, consumer, ev)
        # composition and equivalence between sources/systems
        ents = sources
        for pm in PART_OF.finditer(sentence):
            a = max((m for m in ents if m[1] <= pm.start() and pm.start() - m[1] <= GAP),
                    key=lambda m: m[1], default=None)
            b = min((m for m in ents if m[0] >= pm.end() and m[0] - pm.end() <= GAP),
                    key=lambda m: m[0], default=None)
            if a and b and a[2].entity_id != b[2].entity_id:
                self._composition(chunk, quote, pm.group(0), a[2], b[2])
        for pm in CONTAINS.finditer(sentence):
            whole = max((m for m in ents if m[1] <= pm.start() and pm.start() - m[1] <= GAP),
                        key=lambda m: m[1], default=None)
            if whole is None:
                continue
            stop = CLAUSE_END.search(sentence, pm.end())
            end = stop.start() if stop else len(sentence)
            for part in (m for m in ents if pm.end() <= m[0] < end):
                if part[2].entity_id != whole[2].entity_id and part[2].type == "source":
                    self._composition(chunk, quote, pm.group(0), part[2], whole[2])
        for pm in ALIAS.finditer(sentence):
            pool = sources + utilities
            a = max((m for m in pool if m[1] <= pm.start() and pm.start() - m[1] <= 40),
                    key=lambda m: m[1], default=None)
            b = min((m for m in pool if m[0] >= pm.end() and m[0] - pm.end() <= 10),
                    key=lambda m: m[0], default=None)
            if a and b and a[2].type == b[2].type and a[2].entity_id != b[2].entity_id:
                ev = self.evidence(chunk, chunk.url, quote, pm.group(0), "NONE",
                                   "equivalence statement", True, "SUPPORTING", [])
                self.add("ALIAS_OF", a[2], b[2], ev)

    def _composition(self, chunk, quote, predicate, part: Entity, whole: Entity) -> None:
        scope = self.scopes.get(chunk.url, UNKNOWN_SCOPE)
        if scope.elsewhere:  # a same-named system elsewhere is another entity
            self.reject("SOURCE_PART_OF_SYSTEM", part.name, whole.name,
                        f"document about {scope.label().lower()}", chunk.url, quote)
            return
        ev = self.evidence(chunk, chunk.url, quote, predicate, "NONE",
                           "composition statement (place not required)", True, "SUPPORTING",
                           [])
        self.add("SOURCE_PART_OF_SYSTEM", part, whole, ev)

    def from_affiliations(self) -> None:
        """DEPARTMENT_OF: VERIFIED only from the city's official host (statement or
        departments list); every other signal leaves it CANDIDATE."""
        for a in self.affiliations:
            subject = self.utility_entity(a.candidate_key)
            for e in a.evidence:
                chunk = self.by_chunk.get(e.chunk_id or "")
                official = e.basis in ("OFFICIAL_STATEMENT", "OFFICIAL_DEPARTMENTS_LINK",
                                       "OFFICIAL_LINK_NO_CONTEXT")
                ev = self.evidence(chunk, e.url, e.quote, e.basis.lower().replace("_", " "),
                                   "NONE", e.official_reason or "", e.verifies,
                                   "AUTHORITATIVE" if e.verifies else "NONE", [])
                ev = ev.model_copy(update={
                    "affiliation_basis": e.basis, "target_url": e.target_url,
                    "heading": e.heading or None, "observed_at": e.observed_at,
                    "basis": "GOVERNMENT_OR_REGULATOR" if official else
                    "SELF_DESCRIPTION" if e.basis in ("SELF_DECLARATION", "COPYRIGHT_FOOTER",
                                                       "HOSTNAME_HINT") else "INDEPENDENT"})
                self.add("DEPARTMENT_OF", subject, self.city, ev)

    def from_claim_relations(self) -> None:
        """The extraction's grounded part_of_system relations (quote verified in the run's
        evidence when the claim was made)."""
        fs = self.research.fields.get("water_source")
        for c in (fs.candidate_claims if fs else []):
            if c.value is None or c.review_status == "REJECTED" or not c.qualifiers:
                continue
            for r in c.qualifiers.relations:
                if r.get("relation") != "part_of_system" or not r.get("target"):
                    continue
                url, quote = r.get("url") or "", r.get("quote") or ""
                chunk = self._chunk_for(url, quote)
                ev = self.evidence(chunk, url, quote, "part_of_system (claim relation)",
                                   "NONE", "composition stated in the claim's quote", True,
                                   "SUPPORTING", [])
                self.add("SOURCE_PART_OF_SYSTEM",
                         self.source_entity(str(c.value), c.qualifiers.source_kind),
                         self.source_entity(r["target"], "supply_system"), ev)

    # statuses ---------------------------------------------------------------------------

    def decide(self) -> None:
        for rel in list(self.found.values()):
            status, reason, temporal = _status(rel)
            if status is None:
                for e in rel.evidence:
                    self.reject(rel.kind, rel.subject.name, rel.object.name, reason, e.url,
                                e.quote)
                del self.found[rel.key]
                continue
            rel.status, rel.status_reason, rel.temporal = status, reason, temporal
            rel.basis = sorted({e.basis for e in rel.evidence})
        # a predecessor's own service is historical (succession is temporal evidence)
        historical = {c.key for c in self.registry.candidates if c.status == "HISTORICAL"
                      and c.predecessor_of}
        for rel in self.found.values():
            if rel.kind == "UTILITY_SERVES_CITY" and rel.subject.entity_id.split(":", 1)[1] \
                    in historical:
                rel.status, rel.temporal = "HISTORICAL", "HISTORICAL"
                rel.status_reason = "the operator is a stated predecessor"

def _status(rel: SupplyRelationship) -> tuple[str | None, str, str]:
    """(status, reason, temporal) from the relationship's evidence (schemas/relationships.py);
    status None = only negated statements (no relationship)."""
    evs = rel.evidence
    negations = [e for e in evs if e.negated and e.temporal != "HISTORICAL"]
    historical = [e for e in evs if e.temporal == "HISTORICAL"]
    current = [e for e in evs if not e.negated and e.temporal != "HISTORICAL"]
    if rel.kind == "DEPARTMENT_OF":
        verifying = [e for e in evs if e.affiliation_basis in ("OFFICIAL_STATEMENT",
                                                               "OFFICIAL_DEPARTMENTS_LINK")]
        if verifying:
            where = verifying[0].geo_reason or verifying[0].host
            return ("VERIFIED", f"corroborated on the city's official host ({where})",
                    "UNKNOWN")
        return ("CANDIDATE", "MUNICIPAL_CANDIDATE: " + ", ".join(sorted(
            {(e.affiliation_basis or "").lower() for e in evs})), "UNKNOWN")
    if rel.kind == "PREDECESSOR_OF":
        placed = [e for e in evs if e.placed]
        return ("SUPPORTED" if placed else "CANDIDATE",
                "stated succession" if placed else "succession statement, place uncertain",
                "HISTORICAL")
    if not current and not historical:
        return None, "only negated statements", "UNKNOWN"
    if current and (negations or any(e.negated for e in historical)):
        newest_current = max((y for e in current for y in e.years), default=None)
        newest_negation = max((y for e in negations + historical for y in e.years),
                              default=None)
        if not (newest_current and newest_negation and newest_current > newest_negation):
            return "CONTRADICTED", "current statements and a negation/discontinuation", \
                "UNKNOWN"
    if not current:
        return "HISTORICAL", "every statement has historical scope (" + ", ".join(
            sorted({e.temporal_marker or "" for e in historical}))[:120] + ")", "HISTORICAL"
    placed = [e for e in current if e.placed]
    temporal = "CURRENT" if any(e.temporal == "CURRENT" for e in current) else "UNKNOWN"
    gov = [e for e in placed if e.basis in ("GOVERNMENT_OR_REGULATOR",
                                            "MUNICIPAL_DEPARTMENT")]
    independent = {e.host for e in placed if e.basis in ("INDEPENDENT",
                                                          "GOVERNMENT_OR_REGULATOR")}
    if gov:
        what = ("a verified city department's page" if gov[0].basis == "MUNICIPAL_DEPARTMENT"
                else "a government/regulatory document")
        return "VERIFIED", f"stated by {what} ({gov[0].host})", temporal
    if len(independent) >= 2 and any(e.document_scope == "TARGET_COUNTRY" for e in placed):
        return "VERIFIED", f"stated independently by {', '.join(sorted(independent)[:3])}", \
            temporal
    if placed:
        if all(e.basis == "SELF_DESCRIPTION" for e in placed):
            return "SUPPORTED", "only the subject's own description", temporal
        return "SUPPORTED", "one independent statement", temporal
    if all(e.basis == "CLAIM" for e in current):
        return "CANDIDATE", "a research claim only", temporal
    return "CANDIDATE", "no statement that itself places the city", temporal

def build_relationships(research: CityResearchState, chunks: Sequence[EvidenceChunk],
                        documents: Mapping[str, Mapping[str, Any]] | None = None,
                        registry: UtilityRegistry | None = None,
                        document_ids: Mapping[str, int] | None = None,
                        links: Mapping[str, Sequence[Mapping[str, Any]]] | None = None
                        ) -> RelationshipGraph:
    """The relationship graph for the run's stored evidence (deterministic; rebuilding on the
    same evidence gives the same graph)."""
    documents = documents or {}
    if registry is None:
        registry = build_registry(research, list(chunks), documents)
    b = _Builder(research, chunks, documents, registry, document_ids or {}, links)
    b.from_affiliations()
    b.from_registry()
    b.from_sentences()
    b.from_claim_relations()
    b.decide()
    city, country = research.city_name, research.country
    rels = sorted(b.found.values(), key=lambda r: r.key)
    used = {r.subject.entity_id for r in rels} | {r.object.entity_id for r in rels}
    return RelationshipGraph(
        policy_version=RELATIONSHIP_POLICY_VERSION, city=city, country=country,
        geo_basis=geo_basis(city, country)["basis"],
        identity=identity_signature(city, country),
        entities=sorted((e for e in b.entities.values() if e.entity_id in used),
                        key=lambda e: e.entity_id),
        relationships=rels, rejected=b.rejected)
