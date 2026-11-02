"""Deterministic discovery query planning (no LLM).

Per round, the fields that still need evidence are grouped by ``field_type``. Procedure
``planned_queries`` (advisory, from procedural memory) come first as web queries; otherwise
queries are built from the city/utility and a per-field-type topic. Queries already run in an
earlier round (read back from the trusted TOOL_CALL events) are not repeated.

Discovery v2 (``DiscoveryPlan``, Milestone 3; DISCOVERY_POLICY_VERSION):
- place terms: city + country, plus the region when the identity lists a large homonym or a
  same-country homonym (Santiago, Hyderabad); local-language queries use the same place;
- verified utilities (utility registry) get ``site:<host> <topic>`` (no place terms: the host
  disambiguates) and quoted-name queries; unverified candidates get up to two verification
  queries per round; predecessor names only for historical withdrawals;
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
    place: str = ""  # identity-based place terms ("" = city + country)
    verified_hosts: tuple[str, ...] = ()
    verified_names: tuple[str, ...] = ()
    candidate_names: tuple[str, ...] = ()  # unverified: verification queries
    predecessor_names: tuple[str, ...] = ()
    features: tuple[str, ...] = ()  # linked water features (upstream queries)
    fallback: bool = False  # no verified utility: government/regulator searches
    skip_topics: frozenset[str] = frozenset()  # topics that twice retained nothing
    local_language: str | None = None  # the city's local language code, if configured
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

def place_terms(identity) -> str:
    """City + country, plus the first-level region when the name is ambiguous (a large
    homonym anywhere, or a homonym in the same country). ``identity``: a CityIdentity."""
    parts = [identity.city]
    region = identity.region.name if identity.region else None
    ambiguous = any((h.population or 0) >= 100_000 for h in identity.homonyms) or any(
        h.iso2 and identity.country_info and h.iso2 == identity.country_info.iso2
        for h in identity.homonyms)
    if region and ambiguous and region.casefold() not in identity.city.casefold():
        parts.append(region)
    parts.append(identity.country)
    return " ".join(dict.fromkeys(p for p in parts if p))

def ineffective_topics(events: Sequence[Mapping[str, Any]], topics: Sequence[str]) -> set[str]:
    """Topics whose earlier searches retained nothing at least twice."""
    misses: dict[str, int] = {}
    for e in events:
        a = e.get("attrs") if isinstance(e.get("attrs"), dict) else {}
        if e.get("kind") != "TOOL_CALL" or e.get("node") != "discover" or not a.get("query"):
            continue
        if a.get("sources_retained"):
            continue
        q = str(a["query"]).casefold()
        for t in topics:
            if t.casefold() in q:
                misses[t] = misses.get(t, 0) + 1
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
    # Local-language queries name the place too: a bare city name found same-named towns in
    # other countries (Santiago -> Colombian and Mexican national reports).
    local_place = plan.place or place
    verifications = 0
    for field_type, field_key in groups.items():
        web: list[PlannedQuery] = [PlannedQuery("web", q, field_type, field_key)
                                   for q in (planned_queries or {}).get(field_type, []) if q]
        academic: list[PlannedQuery] = []
        topic = TOPICS.get(field_type, _fallback_topic(fields[field_key]))
        if topic:
            web_topic, academic_topic = topic
            subject = utility or place
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
            if field_type == "utility" and plan.candidate_names:
                for name in plan.candidate_names:
                    if verifications >= MAX_VERIFICATION_QUERIES:
                        break
                    web.append(PlannedQuery("web", f'"{name}" {place} {VERIFICATION_TOPIC}',
                                            field_type, field_key))
                    verifications += 1
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
            academic.append(PlannedQuery("academic", f"{place} {academic_topic}", field_type,
                                         field_key))
        candidates["web"].append(web)
        candidates["academic"].append(academic)

    out: list[PlannedQuery] = []
    seen = {q.casefold() for q in already_run}
    for kind in ("web", "academic"):
        picked = 0
        # Round-robin across field types so one type can't use the whole budget.
        queues = [list(g) for g in candidates[kind]]
        while picked < max_per_kind and any(queues):
            for queue in queues:
                if not queue or picked >= max_per_kind:
                    continue
                q = queue.pop(0)
                text = " ".join(q.query.split())
                if text.casefold() in seen:
                    continue
                seen.add(text.casefold())
                out.append(PlannedQuery(q.kind, text, q.field_type, q.field_key))
                picked += 1
    return out

def _fallback_topic(d: FieldDefinition) -> tuple[str, str] | None:
    words = " ".join(d.keywords[:3])
    return (f"water {words}", f"water supply {words}") if words else None
