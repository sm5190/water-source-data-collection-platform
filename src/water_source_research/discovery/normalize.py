"""Pure normalization helpers used for deduplication and source-type classification."""

import re
from collections.abc import Mapping
from typing import Any
import unicodedata
from dataclasses import dataclass
from datetime import date
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit
from water_source_research.core.versions import CLASSIFICATION_VERSION

_DOI = re.compile(r"10\.\d{4,9}/\S+", re.IGNORECASE)
_DOI_PREFIXES = ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/",
                 "http://dx.doi.org/", "doi.org/", "dx.doi.org/", "doi:")
_TRACKING_PARAMS = re.compile(r"^(utm_\w+|fbclid|gclid|mc_cid|mc_eid|_ga|ref|source)$",
                              re.IGNORECASE)
_NON_ALNUM = re.compile(r"[^0-9a-z]+")

SCHOLARLY_PROVIDERS = frozenset({"openalex", "semantic_scholar", "crossref"})

def normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    text = unquote(value.strip())
    lowered = text.lower()
    for prefix in _DOI_PREFIXES:
        if lowered.startswith(prefix):
            text = text[len(prefix):]
            break
    match = _DOI.search(text)
    if not match:
        return None
    return match.group(0).rstrip(".,;)").lower()

def canonical_url(url: str | None) -> str | None:
    """A comparison key: https, no www., no fragment/tracking params, no trailing slash."""
    if not url or not url.strip():
        return None
    parts = urlsplit(url.strip())
    if not parts.netloc:
        return None
    host = (parts.hostname or "").lower()
    host = host.removeprefix("www.")
    if host in ("doi.org", "dx.doi.org"):
        doi = normalize_doi(url)
        if doi:
            return f"doi:{doi}"
    if parts.port and parts.port not in (80, 443):
        host = f"{host}:{parts.port}"
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                             if not _TRACKING_PARAMS.match(k)))
    path = parts.path.rstrip("/")
    return urlunsplit(("https", host, path, query, ""))

def fold(text: str) -> str:
    """Casefold and strip accents ("São Paulo" -> "sao paulo")."""
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c)).casefold()

def normalize_title(title: str | None) -> str | None:
    if not title:
        return None
    key = _NON_ALNUM.sub(" ", fold(title)).strip()
    return key if len(key) >= 12 else None  # very short titles collide too easily

def iso_date(parts: list | None) -> str | None:
    """Crossref ``date-parts`` ([[2021, 3, 4]]) -> '2021-03-04' / '2021-03' / '2021'."""
    if not isinstance(parts, list) or not parts or not isinstance(parts[0], list) \
            or not parts[0] or parts[0][0] is None:
        return None
    try:
        nums = [int(p) for p in parts[0][:3]]
        if len(nums) == 3:
            return date(*nums).isoformat()
    except (TypeError, ValueError):
        return None
    return "-".join(f"{n:04d}" if i == 0 else f"{n:02d}" for i, n in enumerate(nums))

# --- source-type classification --------------------------------------------------------

_GOV_HOST = re.compile(
    r"((^|\.)gov(\.[a-z]{2,3}){0,2}$|\.gc\.ca$|\.gouv\.[a-z]{2}$|\.gob\.[a-z]{2}$|\.gob$|"
    r"\.go\.[a-z]{2}$|\.govt\.[a-z]{2}$|\.gv\.[a-z]{2}$|\.admin\.ch$|\.bund\.de$|"
    r"(^|\.)europa\.eu$|\.mil$|\.int$|(^|\.)canada\.ca$|\.nic\.in$)")
_MUNICIPAL_HOST = re.compile(r"(^|\.)(city|cityof|ville|stadt|comune|ayuntamiento|municipio|"
                             r"prefeitura|council|county)[.-]")
_REGULATORY = re.compile(r"\b(regulat\w*|ofwat|commission|authority|board of water|"
                         r"water services regulation|epa)\b", re.IGNORECASE)
# Utility words anywhere in a host. English "water" is handled by _WATER_LABEL instead: as a
# bare substring it also matches retailers and blogs (homewater, tappwater, wmcwater).
_UTILITY = re.compile(r"(wasser|agua|aguas|eau|acqua|acque|vatten|vesi|woda|"
                      r"utility|utilities|sabesp|sanepar|sewer|waterworks|wasserwerk)",
                      re.IGNORECASE)
# A host label that names a water utility by itself: thameswater, sydneywater, waternsw.
_WATER_LABEL = re.compile(r"^([a-z]{2,}water|water[a-z]{2,4})$")
_GENERIC_LABELS = frozenset({"com", "co", "org", "net", "gov", "edu", "ac", "info", "biz"})
# Retail/service pages never count as the utility, whatever their name.
_COMMERCIAL = re.compile(
    r"\b(water (filters?|filtration systems?|softeners?|purifiers?|delivery|test kits?)|"
    r"test kits?|reverse osmosis|best water filters?|buy|shop|free (quote|estimate)|near me|"
    r"plumb(er|ers|ing))\b", re.IGNORECASE)
# Blog posts, news articles and listicles: a "...water" host alone doesn't make them the utility.
_EDITORIAL_PATH = re.compile(r"/(blogs?|posts?|news|articles?|stories)(/|$)|/(19|20)\d{2}/\d{1,2}/",
                             re.IGNORECASE)
_EDITORIAL_TITLE = re.compile(r"^\d+\s|\?|\b(everything you need to know|things to know|"
                              r"guide to|can (i|you) drink|is it safe)\b", re.IGNORECASE)
_TITLE_SEGMENTS = re.compile(r"\s*[|–—]\s*|\s+-\s+")
_ACADEMIC_HOST = re.compile(
    r"(^|\.)(doi\.org|sciencedirect\.com|springer\.com|wiley\.com|tandfonline\.com|"
    r"mdpi\.com|nature\.com|iwaponline\.com|researchgate\.net|semanticscholar\.org|"
    r"openalex\.org|arxiv\.org|jstor\.org|sagepub\.com|frontiersin\.org|plos\.org|"
    r"ssrn\.com|ncbi\.nlm\.nih\.gov|scholar\.google\.com|elsevier\.com|acs\.org|ieee\.org|"
    r"copernicus\.org|cambridge\.org|oup\.com)$")
# An organisation-style utility name forming a whole title segment: "Toronto Water",
# "SA Water | Home", "Denver Water Board". It only counts on the organisation's own host
# (see _host_names_org): "Sydney Water - SWAN Forum" is a forum page about the utility.
_UTILITY_ORG = re.compile(
    r"^(?:[A-Z][\w.'&]*\s+){1,3}Water(\s+(Utility|Authority|Board|Department|"
    r"Services|Company|Works|District|Corporation|Commission))?$")
_UTILITY_PHRASE = re.compile(
    r"\b(water|wastewater) (utility|authority|board|department|district|corporation)\b"
    r"|\bwaterworks\b|\bwasserwerke?\b", re.IGNORECASE)
_SECONDARY_HOST = re.compile(r"(^|\.)(wikipedia\.org|wikimedia\.org|reddit\.com|youtube\.com|"
                             r"facebook\.com|linkedin\.com|twitter\.com|x\.com|medium\.com)$")
_ACADEMIC_TLD = re.compile(r"\.(edu|ac\.[a-z]{2}|edu\.[a-z]{2})$")
_TECHNICAL = re.compile(r"\b(technical report|master plan|feasibility|assessment|"
                        r"study|annual report|water quality report|ccr)\b", re.IGNORECASE)

# Directory-style pages (lists, members, profiles) of networks and aggregators: a path
# segment naming one, alone or within a slug ("/our-members/x", "/member-profile-12").
_DIRECTORY_PATH = re.compile(
    r"/(members?|directory|directories|list|lists|listings?|profiles?|utilities|conflict|"
    r"companies|rankings?)(/|$)|/(?:[\w-]*-)?(members?|membership|profiles?)(?:-[\w-]*)?(/|$)",
    re.IGNORECASE)
# Corporate reporting of companies outside the water sector (energy, oil, banking): never a
# technical water source, whatever its format.
_CORPORATE_REPORT = re.compile(r"\b(annual|financial|integrated|sustainability)[\s_-]+report|"
                               r"annual[\s_-]+financial|\b10-?k\b", re.IGNORECASE)
_WATER_CONTEXT = re.compile(r"water|wasser|agua|água|eau|acqua|vatten|vesi|woda|utilit|sewer|"
                            r"sanita|hydro|municip|city|council|gov", re.IGNORECASE)
# News outlets: a host label made of a news word ("riotimesonline", "dailynews") or a wire
# service. Generic vocabulary, not a list of sites.
_NEWS_HOST = re.compile(r"(news|times|post|tribune|herald|journal|gazette|daily|chronicle|"
                        r"telegraph|guardian|observer|courier|reuters|bloomberg|apnews|bbc|cnn|"
                        r"noticias|diario|periodico|jornal|zeitung|nachrichten|journalde|"
                        r"lemonde|elpais|prensa|tribuna|cronica)", re.IGNORECASE)
# A company name with a commercial suffix: "<X> Water Solutions", "<X> Water Technologies".
_COMMERCIAL_ORG = re.compile(
    r"\b(?:[A-Z][\w.'&]*\s+){1,3}Water\s+(Solutions|Technologies|Technology|Group|Systems|"
    r"International|Engineering|Consulting|Holdings|Ventures|Partners|Infrastructure|Capital|"
    r"Projects|Products|Equipment|Treatment\s+Systems)\b")
# A "...water" host label made of a generic word is an aggregator, campaign or brand, not a
# utility named after its place or system (thameswater, sydneywater, denverwater).
_GENERIC_WATER_PREFIX = re.compile(
    r"^(world|global|international|clean|safe|pure|blue|smart|best|my|your|the|fresh|home|tap|"
    r"tapp|drinking|save|life|wise|eco|green|aqua|one|all|every|open|true|good|healthy|rain|"
    r"know|water|hidden|sustainable|future|planet)water$")
# Independent datasets and statistics portals (not a publisher of the city's own numbers).
_DATABASE_HOST = re.compile(r"(^|\.)(data|stats|statistics|aquastat|database)\.|"
                            r"(tapwaterdata|waterdata|opendata)", re.IGNORECASE)

def _non_water_corporate_report(url: str, host: str, title: str) -> bool:
    return bool(_CORPORATE_REPORT.search(f"{title} {url}")) and not (
        _WATER_CONTEXT.search(host) or _WATER_CONTEXT.search(title))

@dataclass(frozen=True, slots=True)
class Classification:
    """Source authority as classified from the URL, title and snippet (never the city's
    relevance: that is judged per run, services/geo_match.py and the utility registry).

    ``source_type`` is the tier used everywhere (UTILITY, GOVERNMENT, REGULATORY, TECHNICAL,
    ACADEMIC, OTHER); ``publisher_kind`` says who published it (UTILITY_OPERATOR, GOVERNMENT,
    REGULATOR, ACADEMIC, TECHNICAL, INDEPENDENT_DATABASE, NEWS, DIRECTORY, COMMERCIAL, OTHER);
    ``signals`` are the rules that decided it."""
    source_type: str
    publisher_kind: str
    signals: tuple[str, ...] = ()
    version: str = CLASSIFICATION_VERSION

def classify(url: str | None, title: str | None = None, provider: str = "",
             doi: str | None = None, city: str | None = None,
             snippet: str | None = None) -> Classification:
    """Classification v2 (CLASSIFICATION_VERSION). Directory, news, aggregator and
    commercial pages are recognised before any utility rule, and a "...water" host label
    makes a page a utility only without commercial or generic-brand signals."""
    host = (urlsplit(url).hostname or "").lower() if url else ""
    title = title or ""
    path = urlsplit(url).path if url else ""
    if host and _ACADEMIC_HOST.search(host):
        return Classification("ACADEMIC", "ACADEMIC", ("academic host",))
    if host and _SECONDARY_HOST.search(host):
        return Classification("OTHER", "OTHER", ("encyclopedia or social site",))
    if host and _GOV_HOST.search(host):
        if _REGULATORY.search(title):
            return Classification("REGULATORY", "REGULATOR", ("government host",
                                                               "regulatory title"))
        return Classification("GOVERNMENT", "GOVERNMENT", ("government host",))
    if host and _MUNICIPAL_HOST.search(host):
        return Classification("GOVERNMENT", "GOVERNMENT", ("municipal host",))
    if provider in SCHOLARLY_PROVIDERS or doi:
        return Classification("ACADEMIC", "ACADEMIC", ("scholarly provider or DOI",))
    if host and _ACADEMIC_TLD.search(host):
        return Classification("ACADEMIC", "ACADEMIC", ("academic domain",))
    labels = [label.replace("-", "") for label in host.removeprefix("www.").split(".")[:-1]
              if label not in _GENERIC_LABELS]
    if _DIRECTORY_PATH.search(path):
        return Classification("OTHER", "DIRECTORY", ("directory path",))
    if any(_NEWS_HOST.search(label) for label in labels):
        return Classification("OTHER", "NEWS", ("news host",))
    utility = _utility_signal(url or "", host, labels, title, snippet or "", city)
    if utility == "commercial":
        return Classification("OTHER", "COMMERCIAL", ("commercial water company",))
    if utility == "aggregator":
        return Classification("OTHER", "DIRECTORY", ("generic water brand host",))
    if utility:
        return Classification("UTILITY", "UTILITY_OPERATOR", (utility,))
    if host and city and _is_city_domain(host, city):
        return Classification("GOVERNMENT", "GOVERNMENT", ("city domain",))
    if _REGULATORY.search(title):
        return Classification("REGULATORY", "REGULATOR", ("regulatory title",))
    if _non_water_corporate_report(url or "", host, title):
        return Classification("OTHER", "COMMERCIAL", ("non-water corporate report",))
    if host and _DATABASE_HOST.search(host):
        return Classification("TECHNICAL", "INDEPENDENT_DATABASE", ("dataset host",))
    if _TECHNICAL.search(title) or (url or "").lower().split("?")[0].endswith(".pdf"):
        return Classification("TECHNICAL", "TECHNICAL", ("technical title or PDF",))
    if _EDITORIAL_PATH.search(path) or _EDITORIAL_TITLE.search(title):
        return Classification("OTHER", "NEWS", ("editorial path or title",))
    return Classification("OTHER", "OTHER", ())

def classify_source_type(url: str | None, title: str | None = None, provider: str = "",
                         doi: str | None = None, city: str | None = None,
                         snippet: str | None = None) -> str:
    """The source tier (see ``classify``). With ``city``, the city's own domain (toronto.ca,
    madrid.es, cityofsydney.nsw.gov.au) counts as municipal government."""
    return classify(url, title, provider, doi, city, snippet).source_type

def reclassify_record(record: Mapping[str, Any], city: str | None) -> Classification | None:
    """The classification a stored document gets under the current rules (replays,
    reprocessing and revalidation re-check what was stored when it was fetched). Academic
    labels from a scholarly provider or DOI are kept: the provider is the evidence for them."""
    url = record.get("final_url") or record.get("requested_url") or record.get("canonical_url")
    if not url:
        return None
    new = classify(url, record.get("title"), provider=record.get("discovery_provider") or "",
                   city=city)
    if record.get("source_type") == "ACADEMIC" and new.source_type != "ACADEMIC":
        return Classification("ACADEMIC", "ACADEMIC", ("kept: academic when acquired",))
    return new

def reclassify(record: Mapping[str, Any], city: str | None) -> str | None:
    """The source type under the current rules (see ``reclassify_record``)."""
    c = reclassify_record(record, city)
    return record.get("source_type") if c is None else c.source_type

def reclassified_columns(record: Mapping[str, Any], city: str | None) -> dict[str, Any]:
    """The classification columns of a copied document under the current rules, keeping the
    classification it was stored with in ``classification_signals['original']``."""
    c = reclassify_record(record, city)
    if c is None:
        return {}
    original = {"source_type": record.get("source_type"),
                "publisher_kind": record.get("publisher_kind"),
                "classification_version": record.get("classification_version") or "classify-1"}
    return {"source_type": c.source_type, "publisher_kind": c.publisher_kind,
            "classification_version": c.version,
            "classification_signals": {"signals": list(c.signals), "original": original}}

_SELF_DESCRIPTION = re.compile(
    r"\b(water (utility|supplier|provider|company|department|authority|board|services|supply)|"
    r"(water|wastewater) and (wastewater|sewer\w*|sanitation)|drinking water|tap water|"
    r"your water|pay (your|my) bill|billing|customers?|agua potable|empresa sanitaria|"
    r"abastecimiento|clientes|[aá]gua pot[aá]vel|saneamento|eau potable|trinkwasser|"
    r"wasserversorgung)\b", re.IGNORECASE)
_COMMERCIAL_WORDS = re.compile(
    r"\b(solutions|technologies|desalination projects?|epc|investors?|our projects|"
    r"products|equipment|engineering services|private developer|water infrastructure "
    r"developer|concessions? portfolio)\b", re.IGNORECASE)

def _utility_signal(url: str, host: str, labels: list[str], title: str, snippet: str,
                    city: str | None) -> str | None:
    """Why the page is the utility's own ("named utility on its own host", ...), or
    "commercial" / "aggregator", or None."""
    path = urlsplit(url).path if url else ""
    page = f"{title} {path.replace('-', ' ').replace('_', ' ')}"
    if _COMMERCIAL.search(page):
        return None
    if city and re.search(rf"\bwater (treatment|filtration|softening)( services?)? (in )?"
                          rf"{re.escape(city)}\b", page, re.IGNORECASE):
        return None  # local-service landing page ("Water Treatment Toronto | WMC Water")
    for segment in _TITLE_SEGMENTS.split(title.strip()):
        if _COMMERCIAL_ORG.search(segment) and _host_names_org(segment, labels):
            return "commercial"
    if any(_GENERIC_WATER_PREFIX.match(label) for label in labels):
        return "aggregator"
    # A named utility in the title, corroborated by its own host.
    for segment in _TITLE_SEGMENTS.split(title.strip()):
        if _UTILITY_ORG.match(segment) and _host_names_org(segment, labels):
            return "named utility on its own host"
    if _EDITORIAL_PATH.search(path) or _EDITORIAL_TITLE.search(title):
        return None
    text = f"{title} {snippet}"
    water_label = any(_WATER_LABEL.match(label) for label in labels)
    if water_label and _COMMERCIAL_WORDS.search(text) and not _SELF_DESCRIPTION.search(text):
        return "commercial"
    if water_label:
        return "water utility host label"
    if host and _UTILITY.search(host.removeprefix("www.")):
        return "utility word in host"
    if _UTILITY_PHRASE.search(title):
        return "utility phrase in title"
    return None

def host_names_org(org: str, host: str) -> bool:
    """Does ``host`` spell the organisation ``org`` (see ``_host_names_org``)?"""
    labels = [label.replace("-", "") for label in host.removeprefix("www.").split(".")[:-1]
              if label not in _GENERIC_LABELS]
    return _host_names_org(org, labels)

def _host_names_org(org: str, labels: list[str]) -> bool:
    """Does a host label spell the organisation: in full (sydneywater ~ "Sydney Water"), by
    initials (cmwc ~ "Consolidated Mutual Water Company"), or abbreviated before "water"
    (amwater ~ "Illinois American Water")?"""
    words = _NON_ALNUM.sub(" ", fold(org)).split()
    compact = "".join(words)
    initials = "".join(w[0] for w in words)
    for label in labels:
        if len(label) >= 4 and (label in compact or compact in label):
            return True
        if len(initials) >= 3 and label.startswith(initials):
            return True
        prefix = label.removesuffix("water")
        if prefix != label and len(prefix) >= 2 and any(w.startswith(prefix)
                                                         for w in words[:-1]):
            return True
    return False

def is_city_domain(host: str, city: str) -> bool:
    """Is ``host`` the city's own website (toronto.ca, cityofhouston.gov, ville-paris.fr)?"""
    return _is_city_domain(host.replace("-", ""), city)

# A city department's own host spelled from the city's name ("<city>publicworks.org"): a hint
# for acquisition priority only (MUNICIPAL_CANDIDATE). Never authority: anyone can register
# such a name; affiliation needs independent corroboration (Milestone 3.1 Phase 3).
_DEPARTMENT_LABELS = ("publicworks", "dpw", "waterworks", "utilities", "waterdept",
                      "waterdepartment", "watersewer", "waterandsewer")

def municipal_hint(host: str, city: str | None) -> bool:
    compact = _NON_ALNUM.sub("", fold(city or ""))
    if len(compact) < 3:
        return False
    labels = (host or "").casefold().removeprefix("www.").split(".")[:-1]
    return any(label in {f"{compact}{d}" for d in _DEPARTMENT_LABELS}
               | {f"{d}{compact}" for d in _DEPARTMENT_LABELS}
               | {f"cityof{compact}{d}" for d in _DEPARTMENT_LABELS} for label in labels)

def _is_city_domain(host: str, city: str) -> bool:
    compact = _NON_ALNUM.sub("", fold(city))
    if len(compact) < 3:
        return False
    labels = host.removeprefix("www.").split(".")
    return any(label in (compact, f"cityof{compact}", f"{compact}city", f"ville{compact}")
               for label in labels[:-1])
