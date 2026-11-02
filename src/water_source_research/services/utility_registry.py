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
- AUTHORITATIVE: CONFIRMED place (city + region/country, or a verified name) and published
  by the utility itself (a host bound to it) or a government/regulatory body. Verifies alone.
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
from dataclasses import dataclass
from urllib.parse import urlsplit
from water_source_research.discovery.normalize import host_names_org
from water_source_research.schemas.research import CityResearchState, EvidenceChunk
from water_source_research.schemas.utility import (
    POTABLE_ROLES, UtilityCandidate, UtilityEvidence, UtilityRegistry,
)
from water_source_research.services.city_identity import catalog_only, fold
from water_source_research.services.geo_match import (
    UNKNOWN_SCOPE, DocumentScope, GeoMatcher, document_scopes, identity_signature, matcher_for,
)

UTILITY_POLICY_VERSION = "utility-1"
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
               rf"de\s+los|das|dos|da|do|y|e)?\s*{_CAP}){{1,6}}(?:\s+S\.?\s?A\.?)?"),
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

_SENTENCE = re.compile(r"(?<=[.!?;])\s+|\n+")
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
    r"\b(bulk|wholesale|raw\s+water|treated\s+water\s+to\s+(?:the\s+)?(?:utilities|"
    r"municipalities|cities|member)|en\s+bloque|agua\s+cruda|[aá]gua\s+bruta|"
    r"water\s+to\s+(?:the\s+)?(?:utilities|municipalities|member\s+cities))\b", re.IGNORECASE)
PART = re.compile(
    r"\b(communes?\s+of|comunas?|parts?\s+of|portions?\s+of|districts?\s+of|sectors?\s+of|"
    r"areas?\s+of|zona\s+(?:norte|sur|oriente|poniente)|northern|southern|eastern|western|"
    r"some\s+(?:areas|communes|districts|neighbo\w+)|most\s+of|la\s+mayor\s+parte\s+de|"
    r"parte\s+de)\b", re.IGNORECASE)
CONTRACTOR = re.compile(
    r"\b(build|built|construct\w*|design(?:ed|s|ing)?|epc|engineering|consult\w*|"
    r"contract(?:ed|or|ors|s)?|award(?:ed|s)?|tender\w*|financ\w*|invest\w*|"
    r"acqui(?:re|red|res|ring|sition)\w*|stake|shareholder\w*|consortium|sponsor\w*|"
    r"equipment|membranes?|handover|transfer\s+of|commission(?:ed|ing)|"
    r"construcci[oó]n|constru(?:y|i)\w*|dise[ñn]\w*|contrat\w*|adjudic\w*|licitaci[oó]n|"
    r"financiamiento|inversi[oó]n|adquiri\w*|participaci[oó]n\s+accionaria|consorcio|"
    r"constru[çc][aã]o|aquisi[çc][aã]o|march[ée]|errichtet)\b", re.IGNORECASE)
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
    name = re.sub(r"^(?:The|the|La|El|Los|Las)\s+", "", name.strip(" ,.;:()"))
    return " ".join(name.split())

def _specific(name: str, exclude: set[str]) -> bool:
    words = utility_key(name).split()
    if not words:
        return False
    if name.isupper() and 2 < len(name) <= 8:
        return True
    # "<City> Public Works" is specific; the city name or "Public Works" alone is not.
    return any(w not in _GENERIC for w in words) and utility_key(name) not in exclude

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

def mine_names(texts: Iterable[str], exclude: set[str]) -> tuple[list[str], dict[str, str]]:
    """(organisation names found, alias -> full-name key from "Full Name (ABBR)")."""
    names: dict[str, str] = {}
    aliases: dict[str, str] = {}
    for text in texts:
        for pattern in _ORG_PATTERNS:
            for m in pattern.finditer(text):
                n = _clean(m.group(0))
                if _WATER_WORD.search(n) and _specific(n, exclude):
                    names.setdefault(utility_key(n), n)
        for m in _ABBR.finditer(text):
            full, abbr = _clean(m.group(1)), m.group(2).strip(".")
            if _WATER_WORD.search(full) and _specific(full, exclude):
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

    found: dict[str, UtilityCandidate] = {}

    def add(name: str, origin: str) -> UtilityCandidate | None:
        name = _clean(name)
        k = utility_key(name)
        if not k or not _specific(name, exclude):
            return None
        cand = found.get(k)
        if cand is None:
            cand = found[k] = UtilityCandidate(key=k, name=name)
        if origin not in cand.origins:
            cand.origins.append(origin)
        return cand

    for n in claim_names:
        add(n, "claim")
    mined, alias_map = mine_names([*(d.title for d in docs.values()),
                                   *(c.text for c in chunks)], exclude)
    for n in mined:
        add(n, "text")
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
    _bind_hosts(found, patterns, docs, chunks)
    for c in chunks:
        doc = docs[c.url]
        low = c.text.casefold()
        for k, cand in found.items():
            pattern = patterns[k]
            if pattern is None or not any(n.casefold()[:12] in low for n in cand.all_names()):
                continue
            for s in _SENTENCE.split(c.text):
                if s.strip() and pattern.search(s):
                    ev = _classify(s, pattern, cand, doc, c.chunk_id, matcher)
                    if ev is not None:
                        cand.evidence.append(ev)
    for cand in found.values():
        _decide(cand, found)
    order = {"VERIFIED": 0, "SANITATION_ONLY": 1, "CANDIDATE": 2, "AMBIGUOUS_GEOGRAPHY": 3,
             "HISTORICAL": 4, "OTHER_CITY": 5, "NON_SERVING": 6}
    kept = [c for c in found.values()
            if "claim" in c.origins or c.evidence or c.bound_hosts or c.predecessor_of
            or c.successor_of]
    kept.sort(key=lambda c: (order[c.status], c.name.casefold()))
    return UtilityRegistry(policy_version=UTILITY_POLICY_VERSION, city=city, country=country,
                           identity=identity_signature(city, country), candidates=kept)

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

def _bind_hosts(found: dict[str, UtilityCandidate], patterns: Mapping[str, re.Pattern | None],
                docs: Mapping[str, _Doc], chunks: Sequence[EvidenceChunk]) -> None:
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
            if (host not in cand.bound_hosts
                    and any(host_names_org(n, host) for n in cand.all_names())
                    and any(p.search(t) for t in texts)):
                cand.bound_hosts.append(host)

def _classify(sentence: str, pattern: re.Pattern[str], cand: UtilityCandidate, doc: _Doc,
              chunk_id: str, matcher: GeoMatcher) -> UtilityEvidence | None:
    rest = pattern.sub(" ", sentence)
    title = pattern.sub(" ", doc.title or "")
    place = matcher.match_place(rest, doc.scope, context=title)
    quote = " ".join(sentence.split())[:400]
    relationship, role = "MENTION_ONLY", None
    serving_words = SERVICE_VERBS.search(rest) or UTILITY_IDENTITY.search(rest)
    potable = POTABLE.search(rest) or UTILITY_IDENTITY.search(rest)
    to_people = re.search(r"\b(residents|inhabitants|population|customers|households|"
                          r"people|habitantes|clientes|hogares|vecinos|usuarios)\b", rest,
                          re.IGNORECASE)
    if CONTRACTOR.search(rest) and not (potable and to_people):
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
    if ev.geo_status == "CONFIRMED" and (own or official):
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
