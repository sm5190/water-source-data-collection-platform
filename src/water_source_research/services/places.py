"""Place names in evidence text: the target city, other cities from the methodology's city list
(config/gazetteer_cities.yaml, independent of the reference workbook) and named water bodies.

Shared by retrieval (context_manager/manager.py: is a passage about the target city?) and
validation (services/normalization.py: does a claim's evidence name the city it is for?).
"""

import re
from functools import lru_cache
import yaml
from water_source_research.core.paths import CONFIG_DIR

GAZETTEER_PATH = CONFIG_DIR / "gazetteer_cities.yaml"
_NAME = r"[A-ZÀ-ÖØ-Þ][\w'’\-]+"
# A named water body: "Thika River", "Lake Houston", "Río Maipo", "Springs of the Rax".
HYDRONYM = re.compile(
    rf"\b(?:{_NAME}\s+(?:and\s+|&\s+)?){{1,3}}"
    r"(?i:rivers?|lakes?|springs?|aquifers?|wellfields?|well\s+fields?|creeks?|reservoirs?|dams?)\b"
    rf"|\b(?:River|Lake|Rio|Río|Lago|Lac|Rivière|Fleuve|Wadi)\s+(?:of\s+)?{_NAME}"
    rf"|\b[Ss]prings?\s+of\s+the\s+{_NAME}")

@lru_cache
def gazetteer() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(city names, country names) from the methodology's city list."""
    if not GAZETTEER_PATH.exists():
        return (), ()
    data = yaml.safe_load(GAZETTEER_PATH.read_text(encoding="utf-8")) or {}
    cities = tuple(dict.fromkeys(n for c in data.get("cities", []) for n in c.get("names", [])))
    countries = tuple(dict.fromkeys(c["country"] for c in data.get("cities", [])
                                    if c.get("country")))
    return cities, countries

def _alternation(names: list[str], ignore_case: bool) -> re.Pattern[str]:
    return re.compile("|".join(rf"(?<!\w){re.escape(n)}(?!\w)"
                               for n in sorted(names, key=len, reverse=True)) or r"(?!x)x",
                      re.IGNORECASE if ignore_case else 0)

@lru_cache(maxsize=64)
def place_patterns(city: str, country: str | None) -> tuple[re.Pattern[str], re.Pattern[str]]:
    """(target place pattern: the city or its country, case-insensitive; other places
    pattern, case-sensitive so common words aren't read as place names) for a city."""
    own = [city] + ([country] if country else [])
    own_keys = [o.casefold() for o in own]
    cities, countries = gazetteer()
    others = [n for n in (*cities, *countries) if len(n) >= 4 and not any(
        n.casefold() in k or k in n.casefold() for k in own_keys)]
    return _alternation(own, True), _alternation(others, False)

@lru_cache(maxsize=64)
def city_pattern(city: str) -> re.Pattern[str]:
    """The city's own name (not its country)."""
    return _alternation([city], True)

def without_water_bodies(text: str) -> str:
    """``text`` with named water bodies blanked out, so "Lake Houston" doesn't count as a
    mention of Houston."""
    return HYDRONYM.sub(lambda m: " " * len(m.group(0)), text)

def names_city(text: str, city: str, country: str | None = None, scope=None) -> str:
    """TARGET (the city is named, outside water-body names), OTHER (only other listed places
    are named) or NONE. With an approved city identity the City + Region + Country rules of
    services/geo_match.py decide (``scope``: the document's DocumentScope); AMBIGUOUS -> NONE."""
    from water_source_research.services.geo_match import matcher_for
    matcher = matcher_for(city, country)
    if matcher is not None:
        status = matcher.match_place(text, scope).status
        return {"CONFIRMED": "TARGET", "PROBABLE": "TARGET", "HOMONYM": "OTHER",
                "OTHER_PLACE": "OTHER"}.get(status, "NONE")
    text = without_water_bodies(text)
    if city_pattern(city).search(text):
        return "TARGET"
    _, other = place_patterns(city, country)
    return "OTHER" if other.search(text) else "NONE"
