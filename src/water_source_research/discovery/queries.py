"""Deterministic discovery query planning (no LLM).

Per round, the fields that still need evidence are grouped by ``field_type``. Procedure
``planned_queries`` (advisory, from procedural memory) come first as web queries; otherwise
queries are built from the city/utility and a per-field-type topic. Queries already run in an
earlier round (read back from the trusted TOOL_CALL events) are not repeated.

Discovery v2 (``DiscoveryPlan``, Milestone 3; DISCOVERY_POLICY_VERSION):
- place terms (discovery-3, ``place_variants``): natural wording, not every place term in
  every query. With a same-country homonym the region is always there ("Houston Texas"); with
  only foreign homonyms the country suffices and the region or the city's own qualified name
  ("Santiago de Chile") are variants; a region whose name contains the city's replaces it
  (never "Santiago Santiago ..."). Field types rotate through the variants (stable across
  rounds). Local-language queries use the identity's local names ("Wien");
- verified utilities (utility registry) get ``site:<host> <topic>`` (no place terms: the host
  disambiguates) and quoted-name queries; unverified candidates get verification queries:
  one web slot each round is reserved for them whatever fields are planned (discovery-3: a
  utility_name claim used to take the field out of the plan, so its candidate was never
  verified), up to two per round, in English and the local language;
  predecessor names only for historical withdrawals;
- no verified utility: government / regulator topics instead of guessing a utility;
- upstream: a linked water feature + topic (the city isn't required);
- a topic that returned nothing retained twice is dropped.
Results are judged against the full City + Region + Country identity whatever query found them.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal
import yaml
from water_source_research.core.paths import CONFIG_DIR
from water_source_research.schemas.research import FieldDefinition

QueryKind = Literal["web", "academic"]

# field_type -> (web topic, academic topic). None: don't search for this field type.
TOPICS: dict[str, tuple[str, str] | None] = {
    "city_identity": None,
    "references": None,
    "population": ("population served drinking water utility", "urban water supply population"),
    "utility": ("drinking water utility", "municipal drinking water supply"),
    # Sources, not reservoirs: "reservoirs" steered results to storage reservoirs.
    "water_source": ("water supply sources rivers springs groundwater",
                     "drinking water supply sources"),
    "source_type": ("drinking water source surface water groundwater",
                    "groundwater surface water supply"),
    "transfer_system": ("water supply aqueduct pipeline transfer", "inter-basin water transfer"),
    "point_of_diversion": ("drinking water intake location", "water intake"),
    "infrastructure": ("water treatment plant intake infrastructure",
                       "water supply infrastructure"),
    "withdrawal": ("water production annual report", "urban water withdrawals"),
    "water_quality": ("drinking water quality report", "drinking water quality"),
}

# Field types worth a site-restricted search of the city's own utility website.
UTILITY_SITE_TOPICS = {"water_source": "water sources", "infrastructure": "treatment plant intake",
                       "withdrawal": "annual report water production",
                       "water_quality": "water quality report",
                       "population": "population served", "transfer_system": "aqueduct transfer",
                       "source_type": "groundwater surface water"}
# Without a verified utility: authoritative public sources instead of a guessed company.
FALLBACK_TOPICS = ("drinking water regulator report", "government water supply report")
FALLBACK_FIELD_TYPES = frozenset({"utility", "water_source", "withdrawal", "population"})
VERIFICATION_TOPIC = "drinking water supply"
UPSTREAM_FIELD_TYPES = frozenset({"water_source", "transfer_system", "infrastructure"})
UPSTREAM_TOPIC = "water supply"
HISTORY_TOPIC = "water production history"
MAX_VERIFICATION_QUERIES = 2

@dataclass(frozen=True, slots=True)
class DiscoveryPlan:
    """What the run knows that steers the next round's searches (agents/nodes.py builds it
    from the utility registry, the city identity, the evidence and earlier searches)."""
    place: str = ""  # identity-based place terms ("" = city + country): the first variant
    places: tuple[str, ...] = ()  # all variants (place_variants); empty = (place,)
    local_place: str = ""  # local-language place ("" = place)
    verified_hosts: tuple[str, ...] = ()
    verified_names: tuple[str, ...] = ()
    candidate_names: tuple[str, ...] = ()  # unverified: verification queries
    predecessor_names: tuple[str, ...] = ()
    features: tuple[str, ...] = ()  # linked water features (upstream queries)
    fallback: bool = False  # no verified utility: government/regulator searches
    skip_topics: frozenset[str] = frozenset()  # topics that twice retained nothing
    local_language: str | None = None  # the city's local language code, if configured
    # site:<official city host> "<organisation>": corroborate a municipal-department candidate
    # (services/affiliation.py::bootstrap_queries); takes one of the round's web slots.
    affiliation_checks: tuple[str, ...] = ()
QUERY_LANGUAGES = CONFIG_DIR / "query_languages.yaml"

@lru_cache
def _local_languages() -> dict[str, dict[str, str]]:
    """country (casefolded) -> {"code": language code} for configured languages."""
    if not QUERY_LANGUAGES.exists():
        return {}
    data = yaml.safe_load(QUERY_LANGUAGES.read_text(encoding="utf-8")) or {}
    return {str(c).casefold(): {"code": code}
            for code, lang in (data.get("languages") or {}).items()
            for c in lang.get("countries") or []}

def _words(text: str) -> list[str]:
    return text.casefold().split()

def _contains(outer: str, inner: str) -> bool:
    """``inner``'s words appear in order, as whole words, inside ``outer``."""
    o, i = _words(outer), _words(inner)
    return bool(i) and any(o[k:k + len(i)] == i for k in range(len(o) - len(i) + 1))

def _join(*parts: str | None) -> str:
    """Place terms without repeating a term another one already contains."""
    kept: list[str] = []
    for p in (x for x in parts if x):
        if any(_contains(k, p) for k in kept):
            continue
        kept = [k for k in kept if not _contains(p, k)] + [p]
    return " ".join(kept)

def _local_name(names, lang: str) -> str | None:
    for n in names:
        if (n.lang or "").split("-")[0] == lang and n.independently_confirmed:
            return n.name
    return None

def place_variants(identity, lang: str | None = None) -> tuple[str, ...]:
    """Place wordings for queries (discovery-3), most specific need first. ``identity``: a
    CityIdentity; ``lang``: a local query language (ISO 639-1) for local-language queries."""
    city, country = identity.city, identity.country
    region = identity.region.name if identity.region else None
    same_country = any(h.iso2 and identity.country_info and h.iso2 == identity.country_info.iso2
                       for h in identity.homonyms)
    large = any((h.population or 0) >= 100_000 for h in identity.homonyms)
    if lang:
        local_city = next((n.name for n in identity.names if n.kind == "OFFICIAL_LOCAL"
                           and (n.lang or "").split("-")[0] == lang
                           and n.independently_confirmed), None) or city
        local_region = (_local_name(identity.region.names, lang) if identity.region
                        else None) or region
        if local_city != city and _contains(local_city, city):  # "Santiago de Chile"
            return (local_city,)
        if same_country and local_region:
            return (_join(local_city, local_region),)
        return (_join(local_city, country),)
    if same_country and region:  # Houston, Texas vs Houston, Mississippi
        return (_join(city, region), _join(city, region, country))
    if large:  # only foreign homonyms: the country disambiguates
        own = [n.name for n in identity.names if n.independently_confirmed
               and _contains(n.name, city) and n.name.casefold() != city.casefold()]
        out = [_join(city, country)]
        if region:
            out.append(_join(city, region, country))
        out += own[:1]
        return tuple(dict.fromkeys(out))
    return (_join(city, country),)

def place_terms(identity) -> str:
    """The first place variant (kept for callers that need one place string)."""
    return place_variants(identity)[0]

def local_language(country: str | None) -> str | None:
    """The configured local query language for the country, if any."""
    return (_local_languages().get((country or "").casefold()) or {}).get("code")

def ineffective_topics(events: Sequence[Mapping[str, Any]], topics: Sequence[str]) -> set[str]:
    """Topics whose earlier searches retained nothing at least twice. A search counts once
    whatever the number of providers that ran it (discovery-3), only if some provider
    answered (failures and replay misses say nothing about the topic) and none of its results
    was retained; a topic matches only as the query's ending ("<place> <topic>"), so a topic
    inside a longer one isn't charged for the longer one's misses."""
    answered: dict[str, bool] = {}  # query -> retained something
    for e in events:
        a = e.get("attrs") if isinstance(e.get("attrs"), dict) else {}
        if e.get("kind") != "TOOL_CALL" or e.get("node") != "discover" or not a.get("query"):
            continue
        if e.get("result") == "FAILURE" or e.get("strategy") == "not_recorded":
            continue
        q = " ".join(str(a["query"]).split()).casefold()
        answered[q] = answered.get(q, False) or bool(a.get("sources_retained"))
    longest = sorted(topics, key=len, reverse=True)
    misses: dict[str, int] = {}
    for q, retained in answered.items():
        if retained:
            continue
        topic = next((t for t in longest if q == t.casefold()
                      or q.endswith(" " + t.casefold())), None)
        if topic is not None:
            misses[topic] = misses.get(topic, 0) + 1
    return {t for t, n in misses.items() if n >= 2}

def all_topics() -> list[str]:
    out = [t for pair in TOPICS.values() if pair for t in pair]
    out += list(UTILITY_SITE_TOPICS.values()) + list(FALLBACK_TOPICS)
    for topics in _local_topics().values():
        out += list(topics.values())
    return list(dict.fromkeys(out))

@lru_cache
def _local_topics() -> dict[str, dict[str, str]]:
    """country (casefolded) -> field_type -> local-language topic."""
    if not QUERY_LANGUAGES.exists():
        return {}
    data = yaml.safe_load(QUERY_LANGUAGES.read_text(encoding="utf-8")) or {}
    out: dict[str, dict[str, str]] = {}
    for lang in (data.get("languages") or {}).values():
        for country in lang.get("countries") or []:
            out.setdefault(str(country).casefold(), dict(lang.get("topics") or {}))
    return out

@dataclass(frozen=True, slots=True)
class PlannedQuery:
    kind: QueryKind
    query: str
    field_type: str
    field_key: str  # representative target field (trusted events are keyed by field)

def previous_queries(events: Sequence[Mapping[str, Any]]) -> set[str]:
    return {str(e["attrs"]["query"]).casefold() for e in events
            if e.get("kind") == "TOOL_CALL" and e.get("node") == "discover"
            and isinstance(e.get("attrs"), dict) and e["attrs"].get("query")}

def build_queries(
    *,
    city: str,
    country: str | None,
    utility: str | None,
    field_keys: Sequence[str],
    fields: Mapping[str, FieldDefinition],
    planned_queries: Mapping[str, Sequence[str]] | None = None,
    already_run: set[str] = frozenset(),  # type: ignore[assignment]
    max_per_kind: int = 6,
    utility_hosts: Sequence[str] = (),
    plan: DiscoveryPlan | None = None,
) -> list[PlannedQuery]:
    """``utility_hosts``: hosts of the city's verified utilities; their site gets one
    restricted query for the fields that live on utility websites. ``plan``: discovery v2
    (see the module docstring); without it queries are built as before."""
    plan = plan or DiscoveryPlan()
    hosts = list(dict.fromkeys([*utility_hosts, *plan.verified_hosts]))
    skip = plan.skip_topics
    groups: dict[str, str] = {}  # field_type -> first target field
    for key in field_keys:
        d = fields.get(key)
        if d is not None and not d.derived:
            groups.setdefault(d.field_type, key)

    candidates: dict[QueryKind, list[list[PlannedQuery]]] = {"web": [], "academic": []}
    place = plan.place or " ".join(p for p in (city, country) if p)
    places = plan.places or (place,)
    # Local-language queries name the place too: a bare city name found same-named towns in
    # other countries (Santiago -> Colombian and Mexican national reports).
    local_place = plan.local_place or place
    utility_key = next((k for k, d in fields.items() if d.field_type == "utility"
                        and not d.derived), "utility_name")
    local_verification = _local_topics().get((country or "").casefold(), {}).get(
        "verification")
    # Every candidate's English query before any local-language one, so two candidates are
    # both checked in the same round.
    verify = [PlannedQuery("web", f'"{name}" {place} {VERIFICATION_TOPIC}', "utility",
                           utility_key) for name in plan.candidate_names]
    if local_verification:
        verify += [PlannedQuery("web", f'"{name}" {local_place} {local_verification}',
                                "utility", utility_key) for name in plan.candidate_names]
    for index, (field_type, field_key) in enumerate(groups.items()):
        variant = places[index % len(places)]
        web: list[PlannedQuery] = [PlannedQuery("web", q, field_type, field_key)
                                   for q in (planned_queries or {}).get(field_type, []) if q]
        academic: list[PlannedQuery] = []
        topic = TOPICS.get(field_type, _fallback_topic(fields[field_key]))
        if topic:
            web_topic, academic_topic = topic
            subject = utility or variant
            site_topic = UTILITY_SITE_TOPICS.get(field_type)
            # Priority order (round-robin takes each type's first query not yet run): the
            # field type's own topic, its local-language topic, then what the run learned.
            if web_topic not in skip:
                web.append(PlannedQuery("web", f"{subject} {web_topic}", field_type, field_key))
            local = _local_topics().get((country or "").casefold(), {}).get(field_type)
            if local and local not in skip:
                web.append(PlannedQuery("web", f"{local_place} {local}", field_type, field_key))
            if hosts and site_topic and site_topic not in skip:
                web.append(PlannedQuery("web", f"site:{hosts[0]} {site_topic}", field_type,
                                        field_key))
            if plan.verified_names and site_topic:
                web.append(PlannedQuery("web", f'"{plan.verified_names[0]}" {site_topic}',
                                        field_type, field_key))
            if field_type in UPSTREAM_FIELD_TYPES and plan.features:
                feature = plan.features[0]
                extra = f" {country}" if country and len(feature.split()) < 2 else ""
                web.append(PlannedQuery("web", f"{feature}{extra} {UPSTREAM_TOPIC}", field_type,
                                        field_key))
            if field_type == "withdrawal" and plan.predecessor_names:
                web.append(PlannedQuery("web", f'"{plan.predecessor_names[0]}" {city} '
                                        f"{HISTORY_TOPIC}", field_type, field_key))
            if plan.fallback and field_type in FALLBACK_FIELD_TYPES:
                web += [PlannedQuery("web", f"{place} {fb}", field_type, field_key)
                        for fb in FALLBACK_TOPICS if fb not in skip]
                local_fb = _local_topics().get((country or "").casefold(), {}).get("regulator")
                if local_fb and local_fb not in skip:
                    web.append(PlannedQuery("web", f"{local_place} {local_fb}", field_type,
                                            field_key))
            academic.append(PlannedQuery("academic", f"{variant} {academic_topic}",
                                         field_type, field_key))
        candidates["web"].append(web)
        candidates["academic"].append(academic)

    out: list[PlannedQuery] = []
    seen = {q.casefold() for q in already_run}

    def take(q: PlannedQuery) -> bool:
        text = " ".join(q.query.split())
        if text.casefold() in seen:
            return False
        seen.add(text.casefold())
        out.append(PlannedQuery(q.kind, text, q.field_type, q.field_key))
        return True

    # The reserved verification slot; the utility field type (when planned) may take one
    # more of the verification queries through the round-robin.
    reserved = next((q for q in verify if take(q)), None)
    affiliation = next((q for q in (PlannedQuery("web", a, "utility", utility_key)
                                    for a in (plan.affiliation_checks if plan else ()))
                        if take(q)), None)
    remaining = [q for q in verify if " ".join(q.query.split()).casefold() not in seen]
    if "utility" in groups and candidates["web"]:
        utility_index = list(groups).index("utility")
        candidates["web"][utility_index] += remaining[:MAX_VERIFICATION_QUERIES - 1]
    for kind in ("web", "academic"):
        picked = (int(reserved is not None) + int(affiliation is not None)
                  if kind == "web" else 0)
        # Round-robin across field types so one type can't use the whole budget.
        queues = [list(g) for g in candidates[kind]]
        while picked < max_per_kind and any(queues):
            for queue in queues:
                if not queue or picked >= max_per_kind:
                    continue
                if take(queue.pop(0)):
                    picked += 1
    return out

def _fallback_topic(d: FieldDefinition) -> tuple[str, str] | None:
    words = " ".join(d.keywords[:3])
    return (f"water {words}", f"water supply {words}") if words else None
