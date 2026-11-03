"""Which organisations supply the target city's drinking water, by role, with the evidence.

Deterministic and rebuilt from the run's evidence (chunks with their documents) and its
``utility_name`` claims, so a revalidation or replay reproduces it. Discovery uncertainty
only: a status here never accepts a claim; validation uses it as one input (Milestone 3 P5).

Candidates: utility_name claims, organisation names in the evidence (English "<X> Water",
"<X> Public Works", Spanish/Portuguese "Aguas ...", "Empresa ... de Agua(s)", "Companhia de
Saneamento ...", French "... des Eaux ...", German "... Wasser", "Stadtwerke ..."), with
"Full Name (ABBR)" aliases, and acronyms named as a predecessor ("X, formerly ABC").

Each sentence naming a candidate is classified (the candidate's name removed first, so
"<City> Public Works" does not name the city by itself):
- relationship: SERVES / SERVES_PART (communes, districts, parts of the city) /
  SUPPLIES_WHOLESALE (bulk or raw water) / SERVES_OTHER (another place) / NON_SERVING_ROLE
  (built, designed, contracted, financed, acquired ...) / MENTION_ONLY / PREDECESSOR_OF /
  SUCCESSOR_OF;
- role: DRINKING_WATER_OPERATOR (a service verb with a drinking-water object, or "the water
  utility of <city>"), WHOLESALE_SUPPLIER, SANITATION_ONLY (wastewater/sewerage only; never a
  potable-water utility), CONTRACTOR, HISTORICAL_OPERATOR (past tense / "until 1999");
- place: services/geo_match.py on the City + Region + Country identity (catalog city and
  country alone when no identity is approved).

Verification strength:
- AUTHORITATIVE: published by the utility itself (a host bound to it) or a government/
  regulatory body, and the place is CONFIRMED (city + region/country, or a verified name) or
  PROBABLE in a document established as about the target country (its host or its text).
  Verifies alone.
- SUPPORTING: CONFIRMED or PROBABLE place in a document not about another country/region.
  Verifies only with an independent second supporting statement from another host, one of
  them in a document about the target country.
- NONE: AMBIGUOUS or HOMONYM places, or no service relationship. Never verifies.

A UTILITY classification is never evidence: a host is *bound* to a candidate when it spells
the name (discovery.normalize.host_names_org) and its pages name the candidate; whether the
candidate serves the city comes only from the statements. A predecessor is HISTORICAL and is
never merged with its successor, and one's verification never verifies the other.
"""

import re
from itertools import pairwise
from collections.abc import Iterable, Mapping, Sequence
from typing import Any
from dataclasses import dataclass
from urllib.parse import urlsplit
from water_source_research.discovery.normalize import host_names_org, is_city_domain
from water_source_research.schemas.research import CityResearchState, EvidenceChunk
from water_source_research.schemas.utility import (
    POTABLE_ROLES, UtilityCandidate, UtilityEvidence, UtilityRegistry,
)
from water_source_research.services.city_identity import catalog_only, fold
from water_source_research.services.geo_match import (
    UNKNOWN_SCOPE, DocumentScope, GeoMatcher, document_scopes, geo_basis, identity_signature,
    matcher_for,
)

UTILITY_POLICY_VERSION = "utility-3"  # 2: client vs contractor, untreated water, descriptor names
# 3: publisher apart from subject, section context, a missing homonym is not evidence
GOVERNMENT_TYPES = frozenset({"GOVERNMENT", "REGULATORY"})
SERVING = frozenset({"SERVES", "SERVES_PART", "SUPPLIES_WHOLESALE"})

_CAP = r"[A-ZÀ-ÖØ-Þ][\w.&'’\-]*"
_SUFFIX = (r"(?:Utility|Utilities|Authority|Board|Department|Services|Company|Corporation|"
           r"Commission|District|Works|Supply\s+Company|Solutions|Technologies|Group|"
           r"International|Holdings|Systems|Infrastructure)")
_ORG_PATTERNS = [
    re.compile(rf"\b(?:{_CAP}\s+){{1,4}}Water(?:\s+(?:and|&)\s+Sewerage)?(?:\s+{_SUFFIX})?\b"),
    re.compile(rf"\b(?:{_CAP}\s+){{1,4}}(?:Public\s+(?:Works|Utilities)|Waterworks|"
               rf"Utility\s+District|Water\s+Authority)\b"),
    re.compile(rf"\b(?:Aguas|Agua|Empresa|Compañía|Compania|Companhia|Sociedad|Corporación|"
               rf"Servicio|Serviço|Acueducto|Obras\s+Sanitarias)(?:\s+(?:de|del|de\s+la|"
               rf"de\s+los|das|dos|da|do|(?:y|e)(?=\s+(?:Alcantarillado|Saneamiento|Sanitari|"
               rf"Aseo)))?\s*{_CAP}){{1,6}}(?:\s+S\.?\s?A\.?)?"),
    re.compile(rf"\b(?:Société|Régie|Compagnie|Office)\s+(?:{_CAP}\s+|des\s+|de\s+)*Eaux"
               rf"(?:\s+(?:de|du|d['’])\s*{_CAP})?"),
    re.compile(rf"\b{_CAP}\s+Wasser\b|\bWasserwerke?\s+{_CAP}|\b{_CAP}\s+Wasserwerke?\b|"
               rf"\bStadtwerke\s+{_CAP}"),
]
_WATER_WORD = re.compile(r"water|agua|água|acueducto|sanitari|saneamento|saneamiento|eaux?\b|"
                         r"wasser|works|utilit", re.IGNORECASE)
_ABBR = re.compile(rf"((?:{_CAP})(?:\s+(?:{_CAP}|de|del|de\s+la|of|and|y|da|do|das|dos|des|du|&))"
                   rf"{{1,8}})\s*\(\s*([A-Z][A-Z&.\-]{{1,9}})\s*\)")
_ACRONYM = re.compile(r"\b[A-Z][A-Z&]{2,7}\b")
_LEGAL = re.compile(r"\b(s\.?\s?a\.?(\s?a\.?)?|e\.?\s?s\.?\s?p\.?|inc\.?|llc|ltd\.?|plc|gmbh|ag|"
                    r"s\.?\s?l\.?|co\.?)$", re.IGNORECASE)
_GENERIC = frozenset({"the", "water", "public", "works", "utility", "utilities", "authority",
    "board", "department", "services", "service", "company", "corporation", "commission",
    "district", "and", "sewerage", "supply", "aguas", "agua", "empresa", "compania",
    "companhia", "sociedad", "corporacion", "servicio", "servico", "acueducto", "obras",
    "sanitarias", "de", "del", "la", "los", "das", "dos", "da", "do", "y", "e", "of",
    "solutions", "technologies", "group", "international", "holdings", "systems",
    "infrastructure", "societe", "regie", "compagnie", "office", "des", "eaux", "du", "wasser",
    "wasserwerk", "wasserwerke", "stadtwerke", "municipal", "metropolitana", "national",
    "city"})

# Words that describe water rather than name an organisation ("Surface Water", "Bottled
# Water", "Agua Potable y Saneamiento"): a name made only of these and generic words isn't one.
_DESCRIPTORS = frozenset({
    "surface", "waste", "ground", "rain", "storm", "bottled", "clean", "drinking", "potable",
    "raw", "treated", "fresh", "sea", "tap", "annual", "additional", "safe", "quality", "urban",
    "rural", "global", "world", "regional", "private", "new", "total", "basic", "sanitation",
    "sewer", "wastewater", "groundwater", "alcantarillado", "saneamiento", "aseo", "basico",
    "sanitario", "sanitaria", "sanitarios", "servicios", "abastecimiento", "urbano", "urbana",
    "reclaimed", "recycled", "hot", "cold", "grey", "gray", "emergency", "integrated",
    "sustainable", "smart", "future", "virtual", "blue", "green", "mineral", "spring", "river",
    "lake", "bulk", "production", "internal", "distribution", "non", "revenue", "nrw",
    "supply", "untreated", "transmission", "resources", "resource", "management", "security", "scarcity",
    "directive", "framework", "partnership", "program", "programme", "project", "fund", "day",
    "week", "report", "plan", "monitoring", "research", "institute", "association",
    "network", "forum", "council", "agency", "development", "commission", "standards",
    "american", "african", "european", "asian", "africa", "europe", "asia", "america", "eu",
    "un", "us", "uk"})
# Sentence fragments the patterns can catch before a name ("About <City> Water", "Although
# ...", "At ..."): stripped from the start; a name that is only these is dropped.
_LEADING_STOP = frozenset({
    "about", "contact", "although", "at", "the", "la", "el", "los", "las", "our", "your", "a",
    "an", "and", "in", "on", "from", "for", "by", "with", "as", "if", "when", "while", "us",
    "this", "that", "these", "those", "all", "each", "every", "more", "most", "some", "other",
    "such", "into", "over", "under", "through", "between", "per", "via", "since", "review",
    "figure", "table", "chapter", "section", "see", "note", "source", "sources", "en", "de",
    "del", "y", "o", "ou", "et", "und", "der", "die", "das"})
# "<X> Water Tower", "<X> Water Treatment Plant", "<X> Water Report": not organisations.
_AFTER_FRAGMENT = frozenset({
    "tower", "towers", "tank", "tanks", "treatment", "plant", "plants", "main", "mains",
    "pipeline", "pipelines", "reservoir", "reservoirs", "quality", "supply", "resources",
    "management", "report", "reports", "directive", "framework", "fund", "act", "law", "day",
    "week", "museum", "park", "crisis", "conflict", "sector", "sources", "source", "use",
    "users", "rights", "bill", "bills", "tariff", "tariffs", "meter", "meters", "sampling",
    "policy", "strategy", "project", "projects", "program", "programme", "system", "systems",
    "network", "scheme", "station", "stations", "intake", "intakes", "dam", "basin",
    "security", "demand", "levels", "level", "footprint", "cycle", "infrastructure"})
# Government, research and finance bodies: named in the evidence, but not utilities.
_NON_UTILITY_HEADS = frozenset({
    "direccion", "dirección", "ministerio", "ministry", "superintendencia", "secretaria",
    "secretaría", "instituto", "institute", "directorate", "agencia", "agency", "comision",
    "comisión", "consejo", "bank", "banco", "fondo", "fund", "programa", "program",
    "university", "universidad", "universidade",
    "bureau", "association", "asociación", "federation", "network", "forum"})
# Service nouns: "el suministro de agua potable" names a service, not who provides it.
_NOMINAL = re.compile(r"^(suministro|suministros|distribuci[oó]n|abastecimiento|prestaci[oó]n|"
                      r"gesti[oó]n|operaci[oó]n|distribution|distributor|distribuidora|"
                      r"servicio|servicios|management|operation|operations)$", re.IGNORECASE)
_SENTENCE = re.compile(r"(?<=[.!?;])\s+|\n+")
_FIRST_PERSON = re.compile(r"\b(we|our|us|nosotros|nuestr[oa]s?|n[oó]s|noss[oa]s?|nous|"
                           r"notre|nos|wir|unser\w*)\b", re.IGNORECASE)
SERVICE_VERBS = re.compile(
    r"\b(suppl(?:y|ies|ied|ying)|provid(?:e|es|ed|ing)|deliver(?:s|ed|ing)?|serv(?:e|es|ed|"
    r"ing)|distribut\w*|operat(?:es|ed|ing)|manag(?:es|ed|ing)|responsible\s+for|"
    r"abastec\w*|suministr\w*|fornec\w*|distribu\w*|versorg\w*|dessert\w*|aliment\w*|"
    r"atiende\w*|presta\w*|opera\b|gestiona\w*|treat(?:s|ed|ing)?|collect(?:s|ed|ing)?|"
    r"trata\w*|recolect\w*|traite\w*|klär\w*)\b", re.IGNORECASE)
UTILITY_IDENTITY = re.compile(
    r"\b(water\s+(?:utility|company|supplier|provider|board|authority|department|services?)|"
    r"(?:empresa|compañía|companhia|operador\w*|concesionari\w*)\s+(?:sanitari\w+|de\s+agua"
    r"\w*|de\s+saneamento)|sanitaria|wasserversorger)\s+(?:of|for|de|del|da|do|in|en|von|"
    r"für)\b", re.IGNORECASE)
POTABLE = re.compile(
    r"\b(drinking[\s-]water|potable\s+water|water\s+suppl(?:y|ies)|water\s+services?|"
    r"tap\s+water|water\s+(?:and|&)\s+(?:sewer\w*|sanitation|wastewater)|"
    r"agua\s+potable|abastecimiento\s+de\s+agua|servicios?\s+(?:de\s+)?(?:agua|sanitari\w*)|"
    r"distribuci[oó]n\s+de\s+agua|[aá]gua\s+pot[aá]vel|abastecimento\s+de\s+[aá]gua|"
    r"fornecimento\s+de\s+[aá]gua|eau\s+potable|distribution\s+d['’]eau|trinkwasser\w*|"
    r"wasserversorgung)\b"
    r"|\b(?:suppl\w+|provid\w+|deliver\w+|serv(?:es|ed|ing)|abastec\w+|suministr\w+)\s+"
    r"(?:\w+\s+){0,3}(?:water|agua|água|eau|wasser)\b", re.IGNORECASE)
SANITATION = re.compile(
    r"\b(wastewater|waste\s+water|sewer\w*|sewage|sanitation|stormwater|drainage|"
    r"alcantarillado|aguas\s+servidas|aguas\s+residuales|saneamiento|esgoto\w*|"
    r"assainissement|eaux\s+us[ée]es|abwasser\w*|kanalisation)\b", re.IGNORECASE)
WHOLESALE = re.compile(
    r"\b(bulk|wholesale|(?:raw|untreated)\s+(?:surface\s+|ground\s*)?water|"
    r"treated\s+water\s+to\s+(?:the\s+)?(?:utilities|municipalities|cities|member)|"
    r"en\s+bloque|agua\s+cruda|[aá]gua\s+bruta|"
    r"water\s+to\s+(?:the\s+)?(?:utilities|municipalities|member\s+cities|cities\s+of))\b",
    re.IGNORECASE)
PART = re.compile(
    r"\b(communes?\s+of|comunas?|parts?\s+of|portions?\s+of|districts?\s+of|sectors?\s+of|"
    r"areas?\s+of|zona\s+(?:norte|sur|oriente|poniente)|northern|southern|eastern|western|"
    r"some\s+(?:areas|communes|districts|neighbo\w+)|most\s+of|la\s+mayor\s+parte\s+de|"
    r"parte\s+de)\b", re.IGNORECASE)
# Building for someone else is the contractor role. Investing, financing, contracting or
# owning stakes are not: utilities do all of these as clients ("a BOT arrangement between
# <utility> and ..."), so they are neutral mentions.
CONTRACTOR = re.compile(
    r"\b(builds?|built|construct(?:s|ed|ing)?|designs?|designed|designing|epc|"
    r"engineering\s+services|consult(?:s|ed|ing|ancy)|supplies\s+(?:equipment|membranes|"
    r"pipes|technology)|equipment\s+supplier|"
    r"constru(?:ye|yen|y[oó]|ido)|dise[ñn](?:a|an|ó)|constr[oó]i|construiu|"
    r"errichtet|baut)\b", re.IGNORECASE)
# Between the candidate's name and a building verb, these make the candidate the client who
# ordered the work ("<X> encargó a la empresa <Y> el diseño", "<X> contracted <Y> to build"):
# commissioning work is not doing it. ("diseño", the noun, is not a verb: "diseñó" is.)
CLIENT = re.compile(
    r"\b(encarg[oó]\s+a|contrat[oó]\s+a|adjudic[oó]\s+(?:a|el)|licit[oó]|"
    r"contracted|hired|commissioned|awarded|appointed|selected|engaged|retained|"
    r"beauftragt\w*|vergab\w*|confi[eé]\s+[aà])\b", re.IGNORECASE)
# Contract types name an arrangement, not who built what.
_CONTRACT_TYPES = re.compile(r"\b(build[\s-]+operate[\s-]+(?:transfer|own)|design[\s-]+build|"
                             r"BOT|BOO|BOOT|DBO|EPC\s+contract)\b", re.IGNORECASE)
PAST = re.compile(
    r"\b(until\s+(?:19|20)\d{2}|was\s+responsible|were\s+responsible|used\s+to|"
    r"hasta\s+(?:19|20)\d{2}|fue\s+(?:la\s+)?(?:empresa|responsable)|era\s+(?:la\s+)?"
    r"(?:empresa|responsable)|ehemalig\w*)\b", re.IGNORECASE)
# "<A> ... <marker> ... <B>": B is A's predecessor / A is B's predecessor.
B_PREDECESSOR = re.compile(
    r"\b(formerly(?:\s+known\s+as)?|previously(?:\s+known\s+as)?|former(?:ly)?\s+name|ex-|"
    r"antes|anteriormente|antigua(?:mente)?|successor\s+(?:of|to)|sucesora\s+de|"
    r"heredera\s+de|took\s+over\s+from|replaced(?!\s+by)|reemplaz[oó])\b", re.IGNORECASE)
A_PREDECESSOR = re.compile(
    r"\b(became|was\s+renamed|renamed|(?:was\s+)?succeeded\s+by|replaced\s+by|"
    r"transformed\s+into|pas[oó]\s+a\s+(?:llamarse|ser)|se\s+convirti[oó]\s+en|"
    r"dio\s+origen\s+a|privati[sz]ed\s+as|privatizada\s+como|now)\b", re.IGNORECASE)

_LINK_FILLER = frozenset({"the", "la", "el", "los", "las", "a", "an", "company", "empresa",
                          "which", "que", "is", "es", "and", "y", "also", "in", "en"})

def utility_key(name: str) -> str:
    k = fold(name).replace("&", " and ")
    k = re.sub(r"^the\s+", "", k)
    for _ in range(2):
        k = _LEGAL.sub("", k).strip(" ,.")
    return " ".join(re.sub(r"[^\w\s]", " ", k).split())

def _clean(name: str) -> str:
    words = name.strip(" ,.;:()").split()
    while words and words[0].casefold().strip(".,") in _LEADING_STOP:
        words = words[1:]
    return " ".join(words)

# Words that make a name an organisation type ("... Company", "Servicio ...", "... Works").
ORG_TYPE_WORDS = frozenset({
    "utility", "utilities", "authority", "board", "department", "services", "company",
    "corporation", "commission", "district", "works", "waterworks", "servicio", "servico",
    "empresa", "compania", "companhia", "sociedad", "corporacion", "acueducto", "obras",
    "societe", "regie", "compagnie", "office", "stadtwerke", "wasserwerk", "wasserwerke",
    "sewerage", "agency"})

def _descriptor_name(name: str, city_words: frozenset[str]) -> bool:
    """A description built on the city's name ("<City> Drinking Water Operations"): its only
    distinctive words are the city's, the rest are water descriptors or activity nouns, and
    no organisation-type word names a body. "<City> Water" (no descriptor) is still a name."""
    words = utility_key(name).split()
    rest = [w for w in words if w not in _GENERIC and w not in city_words]
    return (bool(city_words & set(words)) and bool(rest)
            and all(w in _DESCRIPTORS or w in _AFTER_FRAGMENT or _NOMINAL.match(w)
                    for w in rest)
            and not ORG_TYPE_WORDS & set(words))

def _specific(name: str, exclude: set[str], places: frozenset[str] = frozenset(),
              city_words: frozenset[str] = frozenset()) -> bool:
    """An organisation name, not a description: at least one word that is neither generic,
    a water descriptor, nor a country/continent name; no possessive, digits or initials; not
    a description built on the city's name."""
    if not name or re.search(r"['’]s\b|\d", name) or re.search(r"\b[A-Z]\.\s", name):
        return False
    words = utility_key(name).split()
    if not words:
        return False
    if name.isupper() and 2 < len(name) <= 8:
        return True
    if fold(words[0]) in _NON_UTILITY_HEADS:
        return False
    # "<City> Public Works" is specific; the city name or "Public Works" alone is not.
    distinctive = [w for w in words if w not in _GENERIC and w not in _DESCRIPTORS
                   and w not in places and len(w) > 1]
    if city_words and _descriptor_name(name, city_words):
        return False
    return bool(distinctive) and utility_key(name) not in exclude

def _host(url: str) -> str:
    return (urlsplit(url or "").hostname or "").casefold().removeprefix("www.")

def _name_re(names: Iterable[str]) -> re.Pattern[str] | None:
    parts = []
    for n in sorted(set(names), key=len, reverse=True):
        words = re.findall(r"[\w'’&]+", n)
        if words:
            parts.append(r"[\s\-–,.()]*".join(map(re.escape, words)))
    if not parts:
        return None
    return re.compile(r"(?<!\w)(?:" + "|".join(parts) + r")(?!\w)", re.IGNORECASE)

@dataclass
class _Doc:
    url: str
    host: str
    title: str
    source_type: str | None
    scope: DocumentScope

_TITLE_SEGMENTS = re.compile(r"\s*[|–—]\s*|\s+-\s+")

def mine_names(texts: Iterable[str], exclude: set[str], titles: Iterable[str] = (),
               places: frozenset[str] = frozenset(), city_words: frozenset[str] = frozenset()
               ) -> tuple[list[str], dict[str, str]]:
    """(organisation names found, alias -> full-name key from "Full Name (ABBR)"). In titles
    only a whole title segment counts ("Home - <City> Water Company"), so title-case
    headlines ("<Name> Cuts Water ...") don't become names."""
    names: dict[str, str] = {}
    aliases: dict[str, str] = {}
    for title in titles:
        for segment in _TITLE_SEGMENTS.split(title or ""):
            seg = _clean(segment)
            if seg and any(p.fullmatch(seg) for p in _ORG_PATTERNS) and _specific(
                    seg, exclude, places, city_words):
                names.setdefault(utility_key(seg), seg)
    # One sentence at a time: a name never spans a sentence boundary ("United States.
    # <City> Public Works ...").
    sentences = (st for text in texts for st in _SENTENCE.split(text) if st.strip())
    for text in sentences:
        for pattern in _ORG_PATTERNS:
            for m in pattern.finditer(text):
                after = text[m.end():m.end() + 30].split()
                if after and after[0].casefold().strip(".,;:()") in _AFTER_FRAGMENT:
                    continue
                n = _clean(m.group(0))
                if _WATER_WORD.search(n) and _specific(n, exclude, places, city_words):
                    names.setdefault(utility_key(n), n)
        for m in _ABBR.finditer(text):
            full, abbr = _clean(m.group(1)), m.group(2).strip(".")
            # A name given with its own acronym is an organisation even when its words are
            # generic ("Servicio Municipal de Agua Potable y Alcantarillado (SMAPA)"), if they
            # name a body; "Non-Revenue Water (NRW)" is a measure, not an organisation.
            words = set(utility_key(full).split())
            if _WATER_WORD.search(full) and utility_key(full) not in exclude and (
                    _specific(full, exclude, places, city_words)
                    or (abbr.isupper() and ORG_TYPE_WORDS & words
                        and not _descriptor_name(full, city_words))) and len(
                    full.split()) > 1 and fold(utility_key(full).split()[0]) not in \
                    _NON_UTILITY_HEADS:
                names.setdefault(utility_key(full), full)
                aliases[utility_key(abbr)] = utility_key(full)
                aliases.setdefault(f"_abbr:{utility_key(abbr)}", abbr)
    return list(names.values()), aliases

def build_registry(state: CityResearchState, chunks: Sequence[EvidenceChunk],
                   documents: Mapping[str, Mapping] | None = None,
                   claim_names: Iterable[str] | None = None) -> UtilityRegistry:
    """The registry for the run's evidence. ``documents`` (url -> record with title and
    source_type) fills what the chunks don't carry; ``claim_names`` defaults to the state's
    utility_name candidates (rejected ones excluded)."""
    city, country = state.city_name, state.country
    matcher = matcher_for(city, country) or GeoMatcher(catalog_only(city, country or ""))
    scopes = document_scopes(matcher, chunks)
    docs: dict[str, _Doc] = {}
    for c in chunks:
        if c.url not in docs:
            rec = (documents or {}).get(c.url) or {}
            docs[c.url] = _Doc(c.url, _host(c.url), rec.get("title") or c.title or "",
                               rec.get("source_type") or c.source_type,
                               scopes.get(c.url, UNKNOWN_SCOPE))
    if claim_names is None:
        fs = state.fields.get("utility_name")
        claim_names = [str(cl.value) for cl in (fs.candidate_claims if fs else [])
                       if cl.value is not None and cl.review_status != "REJECTED"]
    exclude = {utility_key(x) for x in (city, country or "") if x} | {
        utility_key(n) for n in matcher.region_names + matcher.country_names}
    from water_source_research.services.places import gazetteer
    places = frozenset(w for n in (*gazetteer()[1], *matcher.country_names)
                       for w in utility_key(n).split())

    found: dict[str, UtilityCandidate] = {}

    def add(name: str, origin: str) -> UtilityCandidate | None:
        name = _clean(name)
        k = utility_key(name)
        if not k or (origin not in ("claim", "acronym")
                     and not _specific(name, exclude, places)):
            return None
        cand = found.get(k)
        if cand is None:
            cand = found[k] = UtilityCandidate(key=k, name=name)
        if origin not in cand.origins:
            cand.origins.append(origin)
        return cand

    for n in claim_names:
        add(n, "claim")
    city_words = frozenset(w for n in matcher.city_names for w in utility_key(n).split())
    mined, alias_map = mine_names([c.text for c in chunks], exclude,
                                  titles=[d.title for d in docs.values()], places=places,
                                  city_words=city_words)
    with_acronym = {v for k, v in alias_map.items() if not k.startswith("_abbr:")}
    for n in mined:
        add(n, "acronym" if utility_key(n) in with_acronym else "text")
    for abbr_key, full_key in list(alias_map.items()):
        if abbr_key.startswith("_abbr:"):
            continue
        full = found.get(full_key)
        abbr_text = alias_map.get(f"_abbr:{abbr_key}")
        if full is None or not abbr_text:
            continue
        if abbr_key in found and abbr_key != full_key:  # the acronym was a candidate itself
            other = found.pop(abbr_key)
            for o in other.origins:
                if o not in full.origins:
                    full.origins.append(o)
        if abbr_text not in full.aliases:
            full.aliases.append(abbr_text)

    _predecessors(found, chunks, add)
    patterns = {k: _name_re(c.all_names()) for k, c in found.items()}
    _bind_hosts(found, patterns, docs, chunks, places, city_words, city)
    owner: dict[str, list[str]] = {}
    for k, cand in found.items():
        for h in cand.bound_hosts:
            owner.setdefault(h, []).append(k)
    by_url: dict[str, list[EvidenceChunk]] = {}
    for c in chunks:
        by_url.setdefault(c.url, []).append(c)
    doc_places: dict[str, Any] = {}

    def doc_place(url: str):
        """Whether the document as a whole is about the city (title + first passages)."""
        if url not in doc_places:
            d = docs[url]
            text = " ".join([d.title or "", *(c.text[:2000] for c in by_url[url][:3])])
            doc_places[url] = matcher.match_place(text, d.scope)
        return doc_places[url]

    def section_of(c: EvidenceChunk, sentence: str):
        """The sentence's place from its local section (heading on the same page/chunk)."""
        def place():
            return matcher.section_place(c.text, max(0, c.text.find(sentence)),
                                         docs[c.url].scope)
        return place

    for c in chunks:
        doc = docs[c.url]
        low = c.text.casefold()
        named: set[str] = set()
        for k, cand in found.items():
            pattern = patterns[k]
            if pattern is None or not any(n.casefold()[:12] in low for n in cand.all_names()):
                continue
            for s in _SENTENCE.split(c.text):
                if s.strip() and pattern.search(s):
                    named.add(s)
                    ev = _classify(s, pattern, cand, doc, c.chunk_id, matcher, doc_place,
                                   section_of(c, s))
                    if ev is not None:
                        cand.evidence.append(ev)
        own = owner.get(doc.host, [])
        if len(own) > 1:  # several bound: the one the page's title names
            titled = [k for k in own if patterns[k] is not None
                      and patterns[k].search(doc.title or "")]
            own = titled
        if len(own) == 1:  # "we supply water to ..." on the utility's own site
            cand = found[own[0]]
            for s in _SENTENCE.split(c.text):
                if s.strip() and s not in named and _FIRST_PERSON.search(s):
                    ev = _classify(s, None, cand, doc, c.chunk_id, matcher, doc_place,
                                   section_of(c, s))
                    if ev is not None and ev.relationship != "MENTION_ONLY":
                        ev.geo_reason = (ev.geo_reason + "; first-person statement on its "
                                         "own site").strip("; ")
                        cand.evidence.append(ev)
    for cand in found.values():
        _decide(cand, found)
    order = {"VERIFIED": 0, "SANITATION_ONLY": 1, "CANDIDATE": 2, "AMBIGUOUS_GEOGRAPHY": 3,
             "HISTORICAL": 4, "OTHER_CITY": 5, "NON_SERVING": 6}
    # Names that are only mentioned (no relationship of any kind, no own host, no claim) are
    # not kept: a mention is not evidence of anything.
    kept = [c for c in found.values()
            if "claim" in c.origins or c.bound_hosts or c.predecessor_of or c.successor_of
            or any(e.relationship != "MENTION_ONLY" for e in c.evidence)]
    kept.sort(key=lambda c: (order[c.status], c.name.casefold()))
    return UtilityRegistry(policy_version=UTILITY_POLICY_VERSION, city=city, country=country,
                           identity=identity_signature(city, country),
                           geo_basis=geo_basis(city, country)["basis"], candidates=kept)

def registry_for_index(research: CityResearchState, index) -> UtilityRegistry:
    """The registry for a validation EvidenceIndex (services/normalization.py), cached on the
    index per set of utility_name candidates."""
    fs = research.fields.get("utility_name")
    names = tuple(sorted({str(c.value) for c in (fs.candidate_claims if fs else [])
                          if c.value is not None and c.review_status != "REJECTED"}))
    cache = index.__dict__.setdefault("_utility_registry", {})
    if names not in cache:
        chunks = []
        for (url, page), items in index.by_key.items():
            doc = index.documents.get(url) or {}
            for c in items:
                chunks.append(EvidenceChunk(chunk_id=c.chunk_id, url=url, text=c.text,
                                            title=doc.get("title"), page_number=page,
                                            source_type=c.source_type
                                            or doc.get("source_type")))
        cache[names] = build_registry(research, chunks, index.documents, names)
    return cache[names]

def _predecessors(found: dict[str, UtilityCandidate], chunks: Sequence[EvidenceChunk],
                  add) -> None:
    """Predecessor links stated in one sentence ("X, formerly ABC"). An acronym
    on the other side of the marker becomes a candidate of its own."""
    names = {k: c.all_names() for k, c in found.items()}
    for c in chunks:
        for s in _SENTENCE.split(c.text):
            if not (B_PREDECESSOR.search(s) or A_PREDECESSOR.search(s)):
                continue
            hits = []
            for k, ns in names.items():
                p = _name_re(ns)
                if p is not None and (m := p.search(s)):
                    hits.append((m.start(), m.end(), k))
            for m in _ACRONYM.finditer(s):
                if not any(a <= m.start() < b for a, b, _ in hits):
                    hits.append((m.start(), m.end(), f"_acr:{m.group(0)}"))
            hits.sort()
            for (_, b1, k1), (a2, _, k2) in pairwise(hits):
                if k1.startswith("_acr:") and k2.startswith("_acr:"):
                    continue
                # The marker must be all that stands between the two names ("A, formerly B",
                # "A (ex B)", "A became B"), so "A now operates the plant (PTAP)" isn't one.
                between = " ".join(w for w in re.sub(r"[^\w\s-]", " ", s[b1:a2]).split()
                                   if w.casefold() not in _LINK_FILLER)
                pred, succ = None, None
                if A_PREDECESSOR.fullmatch(between):
                    pred, succ = k1, k2
                elif B_PREDECESSOR.fullmatch(between):
                    pred, succ = k2, k1
                if pred is None:
                    continue
                keys = []
                for k in (pred, succ):
                    if k.startswith("_acr:"):
                        cand = add(k[5:], "text")
                        k = cand.key if cand else None
                    keys.append(k)
                pk, sk = keys
                if pk and sk and pk != sk:
                    p, q = found[pk], found[sk]
                    if sk not in p.predecessor_of:
                        p.predecessor_of.append(sk)
                    if pk not in q.successor_of:
                        q.successor_of.append(pk)
                    quote = " ".join(s.split())[:400]
                    p.evidence.append(UtilityEvidence(
                        relationship="PREDECESSOR_OF", role="HISTORICAL_OPERATOR",
                        geo_status="NONE", url=c.url, host=_host(c.url), chunk_id=c.chunk_id,
                        quote=quote, source_type=c.source_type, other=q.name))
                    q.evidence.append(UtilityEvidence(
                        relationship="SUCCESSOR_OF", geo_status="NONE", url=c.url,
                        host=_host(c.url), chunk_id=c.chunk_id, quote=quote,
                        source_type=c.source_type, other=p.name))

_UTILITY_LABEL = re.compile(r"water|agua|wasser|eau|publicworks|utilit|sanea|acueducto|"
                            r"sewer|sanitar", re.IGNORECASE)

def _spells(cand: UtilityCandidate, host: str, places: frozenset[str],
            city_words: frozenset[str] = frozenset(), city: str = "") -> bool:
    """The host spells the candidate itself: one of its distinctive words (4+ letters) or an
    acronym alias is in a host label, and discovery's org/host check agrees. A generic label
    ("water.igad.int") never counts."""
    labels = [lab for lab in host.split(".")[:-1] if lab not in ("www", "com", "org", "net",
                                                                 "co", "gov", "go", "ac")]
    words = {w for n in cand.all_names() for w in utility_key(n).split()
             if len(w) >= 4 and w not in _GENERIC and w not in _DESCRIPTORS and w not in places}
    acronyms = {utility_key(a).replace(" ", "") for a in cand.aliases if a.isupper()}
    def word_hit(lab: str) -> bool:
        matched = [w for w in words if w in lab]
        if not matched:
            return False
        # Only the city's name links them: the label must also be a water/utility label
        # ("<city>water", "<city>publicworks") or the city's own domain (a city department's
        # utility), not the city's assembly or tourism site.
        return (any(w not in city_words for w in matched) or bool(_UTILITY_LABEL.search(lab))
                or (bool(city) and is_city_domain(host, city)))
    hit = any(word_hit(lab.replace("-", "")) for lab in labels) or any(
        lab.startswith(a) for lab in labels for a in acronyms if len(a) >= 3)
    return hit and any(host_names_org(n, host) for n in cand.all_names()) or (
        hit and bool(acronyms))

def _bind_hosts(found: dict[str, UtilityCandidate], patterns: Mapping[str, re.Pattern | None],
                docs: Mapping[str, _Doc], chunks: Sequence[EvidenceChunk],
                places: frozenset[str] = frozenset(),
                city_words: frozenset[str] = frozenset(), city: str = "") -> None:
    """A host is the candidate's own when it spells the candidate's name and its pages name
    the candidate (title or text), and it isn't about another country or region."""
    text_by_host: dict[str, list[str]] = {}
    for c in chunks:
        text_by_host.setdefault(_host(c.url), []).append(c.text)
    for d in docs.values():
        text_by_host.setdefault(d.host, []).append(d.title)
    elsewhere = {h for h in text_by_host if all(
        d.scope.elsewhere for d in docs.values() if d.host == h)}
    for k, cand in found.items():
        p = patterns.get(k)
        if p is None:
            continue
        for host, texts in text_by_host.items():
            if not host or host in elsewhere:
                continue
            if (host not in cand.bound_hosts and _spells(cand, host, places, city_words, city)
                    and any(p.search(t) for t in texts)):
                cand.bound_hosts.append(host)

def _acting(sentence: str, pattern: re.Pattern[str] | None, rest: str) -> bool:
    """Is the candidate the one providing the service? Its name (or the first person, on its
    own site) comes before a service verb (not a service noun) within VERB_WINDOW characters,
    or it is introduced as "the water utility of ..."."""
    if UTILITY_IDENTITY.search(rest):
        return True
    starts = ([m.end() for m in pattern.finditer(sentence)] if pattern is not None
              else [m.end() for m in _FIRST_PERSON.finditer(sentence)])
    for v in SERVICE_VERBS.finditer(sentence):
        if _NOMINAL.match(v.group(0)):
            continue
        if any(0 <= v.start() - a <= VERB_WINDOW for a in starts):
            return True
    return False

VERB_WINDOW = 150

def _builds(sentence: str, pattern: re.Pattern[str] | None) -> bool:
    """The candidate itself builds, designs or engineers (its name before the verb, within
    VERB_WINDOW characters); contract-type names (BOT, design-build) don't count."""
    if pattern is None:
        return False
    text = _CONTRACT_TYPES.sub(" ", sentence)
    starts = [m.end() for m in pattern.finditer(text)]
    return any(0 <= v.start() - a <= VERB_WINDOW and not CLIENT.search(text, a, v.start())
               for v in CONTRACTOR.finditer(text) for a in starts)

def _classify(sentence: str, pattern: re.Pattern[str] | None, cand: UtilityCandidate,
              doc: _Doc, chunk_id: str, matcher: GeoMatcher,
              doc_place=None, section=None) -> UtilityEvidence | None:
    rest = pattern.sub(" ", sentence) if pattern is not None else sentence
    title = pattern.sub(" ", doc.title or "") if pattern is not None else (doc.title or "")
    place = matcher.match_place(rest, doc.scope, context=title)
    if place.status in ("NONE", "AMBIGUOUS") and section is not None:
        local = section()  # the sentence's own section heading names the city
        if local is not None:
            place = local
    # The whole document's place, for a sentence naming no place, unless the document is a
    # multi-country one (there only the section decides, above).
    if place.status == "NONE" and doc_place is not None and doc.scope.country != "MIXED":
        whole = doc_place(doc.url)  # the sentence names no place: the document's place
        if whole.is_target:
            from water_source_research.services.geo_match import GeoMatch
            place = GeoMatch("PROBABLE", f"the document is about the city ({whole.reason})",
                             whole.evidence)
    quote = " ".join(sentence.split())[:400]
    relationship, role = "MENTION_ONLY", None
    serving_words = _acting(sentence, pattern, rest)
    potable = POTABLE.search(rest) or UTILITY_IDENTITY.search(rest)
    to_people = re.search(r"\b(residents|inhabitants|population|customers|households|"
                          r"people|habitantes|clientes|hogares|vecinos|usuarios)\b", rest,
                          re.IGNORECASE)
    if _builds(sentence, pattern) and not (potable and to_people):
        relationship, role = "NON_SERVING_ROLE", "CONTRACTOR"
    elif serving_words:
        if WHOLESALE.search(rest):
            relationship, role = "SUPPLIES_WHOLESALE", "WHOLESALE_SUPPLIER"
        elif potable:
            relationship = "SERVES_PART" if PART.search(rest) else "SERVES"
            role = "DRINKING_WATER_OPERATOR"
        elif SANITATION.search(rest):
            relationship, role = "SERVES", "SANITATION_ONLY"
        if role is not None and role != "CONTRACTOR" and PAST.search(rest):
            role = "HISTORICAL_OPERATOR"
        if relationship in SERVING and not place.is_target:
            relationship = ("SERVES_OTHER" if place.status in ("OTHER_PLACE", "HOMONYM")
                            else relationship if place.status == "AMBIGUOUS" else "MENTION_ONLY")
    ev = UtilityEvidence(relationship=relationship, role=role, geo_status=place.status,
                         geo_reason=place.reason, url=doc.url, host=doc.host,
                         chunk_id=chunk_id, quote=quote, source_type=doc.source_type,
                         document_scope=doc.scope.label())
    ev.strength = _strength(ev, cand, doc)
    return ev

def _strength(ev: UtilityEvidence, cand: UtilityCandidate, doc: _Doc) -> str:
    if ev.relationship not in SERVING or ev.role in (None, "CONTRACTOR",
                                                     "HISTORICAL_OPERATOR"):
        return "NONE"
    if doc.scope.elsewhere or ev.geo_status not in ("CONFIRMED", "PROBABLE"):
        return "NONE"
    own = doc.host in cand.bound_hosts
    official = (doc.source_type or "").upper() in GOVERNMENT_TYPES
    # The sentence itself must place the service: a place taken from the document as a whole
    # ("the document is about the city") only supports.
    if ev.geo_reason.startswith("the document is about the city"):
        return "SUPPORTING"
    placed = ev.geo_status == "CONFIRMED" or doc.scope.target_country
    if placed and (own or official):
        return "AUTHORITATIVE"
    return "SUPPORTING"

def _verified_role(cand: UtilityCandidate, role: str) -> str | None:
    """Why ``role`` is verified for the candidate, or None."""
    statements = [e for e in cand.evidence if e.role == role and e.relationship in SERVING]
    auth = next((e for e in statements if e.strength == "AUTHORITATIVE"), None)
    if auth is not None:
        who = "its own site" if auth.host in cand.bound_hosts else "a government source"
        return f"stated by {who} ({auth.host}) with the city's region/country"
    support = [e for e in statements if e.strength in ("AUTHORITATIVE", "SUPPORTING")]
    hosts = {e.host for e in support}
    if len(hosts) >= 2 and any(e.document_scope == "TARGET_COUNTRY" for e in support):
        return f"two independent statements ({', '.join(sorted(hosts)[:3])})"
    return None

def _decide(cand: UtilityCandidate, found: Mapping[str, UtilityCandidate]) -> None:
    if cand.predecessor_of:
        names = ", ".join(found[k].name for k in cand.predecessor_of if k in found)
        cand.status, cand.status_reason = "HISTORICAL", f"predecessor of {names}"
        return
    roles = [r for r in (*POTABLE_ROLES, "SANITATION_ONLY") if _verified_role(cand, r)]
    cand.verified_roles = roles
    potable = [r for r in roles if r in POTABLE_ROLES]
    serving = [e for e in cand.evidence if e.relationship in SERVING]
    if potable:
        cand.status = "VERIFIED"
        cand.status_reason = "; ".join(f"{r}: {_verified_role(cand, r)}" for r in potable)
    elif "SANITATION_ONLY" in roles:
        cand.status = "SANITATION_ONLY"
        cand.status_reason = f"sanitation only: {_verified_role(cand, 'SANITATION_ONLY')}"
    elif any(e.role == "HISTORICAL_OPERATOR" and e.geo_status in ("CONFIRMED", "PROBABLE")
             for e in cand.evidence):
        cand.status, cand.status_reason = "HISTORICAL", "described in the past tense"
    elif serving and all(e.geo_status in ("AMBIGUOUS", "HOMONYM") for e in serving):
        cand.status = "AMBIGUOUS_GEOGRAPHY"
        cand.status_reason = "service statements name only a same-named or ambiguous place"
    elif any(e.relationship == "SERVES_OTHER" for e in cand.evidence) and not serving:
        cand.status, cand.status_reason = "OTHER_CITY", "serves another place"
    elif any(e.relationship == "NON_SERVING_ROLE" for e in cand.evidence) and not serving:
        cand.status = "NON_SERVING"
        cand.status_reason = "only construction, contracting or investment statements"
    else:
        cand.status = "CANDIDATE"
        cand.status_reason = ("one supporting statement; needs an authoritative or second "
                              "independent one" if serving else "no service statement yet")
