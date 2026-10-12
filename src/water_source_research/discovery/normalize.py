"""Pure normalization helpers used for deduplication and source-type classification."""

import re
import unicodedata
from datetime import date
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

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

def classify_source_type(url: str | None, title: str | None = None, provider: str = "",
                         doi: str | None = None, city: str | None = None) -> str:
    """Heuristic source tier used for ranking discovery candidates.

    Returns one of UTILITY, GOVERNMENT, REGULATORY, TECHNICAL, ACADEMIC, OTHER (the same
    vocabulary as ``EvidenceChunk.source_type``). With ``city``, the city's own domain
    (toronto.ca, madrid.es, cityofsydney.nsw.gov.au) counts as municipal government.
    """
    host = (urlsplit(url).hostname or "").lower() if url else ""
    title = title or ""
    if host and _ACADEMIC_HOST.search(host):
        return "ACADEMIC"
    if host and _SECONDARY_HOST.search(host):
        return "OTHER"
    if host and _GOV_HOST.search(host):
        return "REGULATORY" if _REGULATORY.search(title) else "GOVERNMENT"
    if host and _MUNICIPAL_HOST.search(host):
        return "GOVERNMENT"
    if provider in SCHOLARLY_PROVIDERS or doi:
        return "ACADEMIC"
    if host and _ACADEMIC_TLD.search(host):
        return "ACADEMIC"
    if _is_utility(url or "", host, title, city):
        return "UTILITY"
    if host and city and _is_city_domain(host, city):
        return "GOVERNMENT"
    if _REGULATORY.search(title):
        return "REGULATORY"
    if _TECHNICAL.search(title) or (url or "").lower().split("?")[0].endswith(".pdf"):
        return "TECHNICAL"
    return "OTHER"

def _is_utility(url: str, host: str, title: str, city: str | None) -> bool:
    path = urlsplit(url).path if url else ""
    page = f"{title} {path.replace('-', ' ').replace('_', ' ')}"
    if _COMMERCIAL.search(page):
        return False
    if city and re.search(rf"\bwater (treatment|filtration|softening)( services?)? (in )?"
                          rf"{re.escape(city)}\b", page, re.IGNORECASE):
        return False  # local-service landing page ("Water Treatment Toronto | WMC Water")
    labels = [label.replace("-", "") for label in host.removeprefix("www.").split(".")[:-1]
              if label not in _GENERIC_LABELS]
    # A named utility in the title, corroborated by its own host.
    for segment in _TITLE_SEGMENTS.split(title.strip()):
        if _UTILITY_ORG.match(segment) and _host_names_org(segment, labels):
            return True
    if _EDITORIAL_PATH.search(path) or _EDITORIAL_TITLE.search(title):
        return False
    return (any(_WATER_LABEL.match(label) for label in labels)
            or bool(host and _UTILITY.search(host.removeprefix("www.")))
            or bool(_UTILITY_PHRASE.search(title)))

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

def _is_city_domain(host: str, city: str) -> bool:
    compact = _NON_ALNUM.sub("", fold(city))
    if len(compact) < 3:
        return False
    labels = host.removeprefix("www.").split(".")
    return any(label in (compact, f"cityof{compact}", f"{compact}city", f"ville{compact}")
               for label in labels[:-1])
