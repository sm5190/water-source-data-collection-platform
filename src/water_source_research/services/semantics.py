"""Semantic checks: does a claim's evidence support what the field asks for, not just contain
the value? Deterministic; the cited quotes, their sentences, the page structure around them
(services/structure.py) and the run's other evidence chunks are the only input.

Every check here is PASS or WARN, never FAIL: a relationship the field needs that can't be
established from the evidence sends the claim to a person; it doesn't make it invalid.
(Standardization Rules column numbers in brackets.)

  CITY_NAMED / WRONG_PLACE / CITY_NOT_NAMED
      high-risk city fields: the quote's sentence, the heading/caption above it or the
      document title names the target city (a mention inside a water-body name, "Lake
      Houston", doesn't count); WRONG_PLACE when only other listed cities are named.
  JURISDICTION_LEVEL / JURISDICTION_LEVEL_UNKNOWN   [2]
      the value names the country's first-level subdivision, not a lower level
      (config/admin_levels.yaml); a country not listed can't be established.
  MEASUREMENT_TERM, SERVICE_AREA_POPULATION   [6, 8]
      a population figure is stated as a population; for city population, a utility
      service-area / served population is not the city's population.
  MEASUREMENT_TYPE / MEASUREMENT_TYPE_MISMATCH / MEASUREMENT_TYPE_UNKNOWN   [24, 26]
      withdrawals are water withdrawn/abstracted; production, treatment, delivery,
      consumption or capacity figures are not withdrawals.
  MEASUREMENT_YEAR   [7, 9, 25, 27]
      the measurement's year is stated with it (quote, sentence, table, heading, caption,
      record), not taken from the publication date.
  FRESHNESS / FRESHNESS_UNKNOWN / POSSIBLY_SUPERSEDED   [25, 27, 31: "most current"]
      the year is at least the field's latest expected reporting year (current year -
      publication lag - reporting cadence); older values may be superseded (kept, reviewed).
  YEAR_LINKED / YEAR_NOT_LINKED   [7, 9, 25, 27, 31]
      a year field's value is tied to its measurement in the evidence: the same sentence as
      the measurement's terms or a figure, a table row/header, a heading/caption or a record
      line above the figures. A page or publication date ("July 2018") never is.
  DESIGNATED_SOURCE / DESIGNATED_SOURCE_MISSING   [6, 7]
      city population comes from the methodology's designated dataset (none configured).
  UTILITY_SERVES_CITY / UTILITY_SERVICE_NOT_STATED   [12]
      some evidence in the run states that the utility serves the city (an official utility
      website or the UTILITY source type alone is not enough).
"""

import re
from collections.abc import Callable, Iterable, Mapping
from functools import lru_cache
from typing import Any
from urllib.parse import urlsplit
import yaml
from water_source_research.core.paths import CONFIG_DIR
from water_source_research.schemas.common import CheckResult, ValueStatus
from water_source_research.schemas.research import (
    CityResearchState, ClaimQualifiers, EvidenceRef, FieldClaim, FieldDefinition,
)
from .places import city_pattern, names_city, without_water_bodies
from .structure import (
    date_line_years, governed_lines, is_date_line, locate_line, structural_year, years_of,
)

ADMIN_LEVELS_PATH = CONFIG_DIR / "admin_levels.yaml"
LINKED_YEAR_BASES = frozenset({"quote", "passage", "table", "heading", "record"})

Add = Callable[[str, CheckResult, str | None], None]

# --- vocabularies -----------------------------------------------------------------------------

POPULATION_WORDS = re.compile(
    r"\b(population|populations|inhabitants?|residents?|people|persons|citizens|"
    r"habitantes|população|moradores|einwohner|bewohner|abitanti|census|censo|recensement)\b",
    re.IGNORECASE)
SERVICE_AREA_WORDS = re.compile(
    r"\b(serv(?:es|ed|ing|ice\s+area|ice\s+population)|customers?|connections?|consumers|"
    r"subscribers|population\s+served|people\s+served|abastec\w*|atendid\w*|desservi\w*|"
    r"versorgt\w*|servid[ao]s?)\b", re.IGNORECASE)
WITHDRAWAL_WORDS = re.compile(
    r"\b(withdr(?:aw|ew)\w*|abstract\w*|intakes?|raw\s+water|extract(?:ed|ion|s)?|pumped\s+from|"
    r"drawn\s+from|taken\s+from|diverted|captaci[oó]n\w*|captad[ao]s?|capta[çc][aã]o|"
    r"extracci[oó]n|agua\s+cruda|água\s+bruta|prélèvement\w*|prélevé\w*|entnahme|"
    r"rohwasser|gefördert\w*)\b", re.IGNORECASE)
NOT_WITHDRAWAL_WORDS = re.compile(
    r"\b(produc\w*|treat\w*|deliver\w*|distribut\w*|consum\w*|demand|sold|billed|"
    r"capacity|design(?:ed)?|potable|supplied\s+to|tratad[ao]s?|tratamiento|"
    r"distribu[íi]d[ao]s?|consumo|produ[cç][aã]o|producci[oó]n|potabiliz\w*)\b",
    re.IGNORECASE)
SERVICE_VERBS = re.compile(
    r"\b(suppl(?:y|ies|ied|ying)|provid(?:e|es|ed|ing)|deliver(?:s|ed|ing)?|serv(?:e|es|ed|"
    r"ing)|distribut\w*|operat(?:es|ed|ing)|manag(?:es|ed|ing)|responsible\s+for|"
    r"abastec\w*|suministr\w*|fornec\w*|distribu\w*|versorg\w*|dessert\w*|aliment\w*)\b",
    re.IGNORECASE)
UTILITY_NOUNS = re.compile(
    r"\bwater\s+(?:utility|utilities|company|supplier|provider|board|authority|department|"
    r"division|services?|corporation|undertaking)\b|\b(?:utility|operator|concessionaire)\b|"
    r"\b(?:empresa|companhia|compañía|servicio)\s+(?:de\s+)?(?:agua|água|aguas|saneamento)\b",
    re.IGNORECASE)

# --- context of a citation --------------------------------------------------------------------

def page_text(index: Any, ref: EvidenceRef) -> str:
    """The text of the cited page (its chunks, in order) or of the document's chunks."""
    chunks = index.by_key.get((ref.url, ref.page_number)) or []
    return "\n".join(c.text for c in chunks)

def sentence_of(index: Any, ref: EvidenceRef, located: Any) -> str:
    from .normalization import sentence_around
    if located is not None and located.chunk is not None:
        return sentence_around(located.chunk.text, ref.passage) or ref.passage
    return ref.passage

def headings_above(text: str, quote: str, limit: int = 3) -> list[str]:
    """Up to ``limit`` short heading-like lines above the quote (titles of the section or
    table it is in)."""
    lines = text.splitlines()
    i = locate_line(lines, quote)
    if i is None:
        return []
    out = []
    for line in reversed(lines[max(0, i - 15):i]):
        s = line.strip()
        if s and len(s) <= 120 and not s.endswith((".", ";", ",")) and not is_date_line(s):
            out.append(s)
            if len(out) >= limit:
                break
    return out

def context_texts(claim: FieldClaim, located: list, index: Any) -> list[str]:
    """Per citation: the sentence, plus the headings above it."""
    out = []
    for ref, loc in located:
        sentence = sentence_of(index, ref, loc)
        out.append(" \n".join([sentence, *headings_above(page_text(index, ref), ref.passage)]))
    return out or [e.passage for e in claim.evidence]

def _titles(located: list) -> list[str]:
    out = []
    for ref, loc in located:
        for t in (ref.title, (loc.document or {}).get("title") if loc is not None else None):
            if t and t not in out:
                out.append(t)
    return out

# --- admin levels -----------------------------------------------------------------------------

@lru_cache
def admin_levels() -> dict[str, dict[str, Any]]:
    """Country (and alias), casefolded -> {first_level, lower_level, country}."""
    if not ADMIN_LEVELS_PATH.exists():
        return {}
    data = yaml.safe_load(ADMIN_LEVELS_PATH.read_text(encoding="utf-8")) or {}
    out: dict[str, dict[str, Any]] = {}
    for country, spec in (data.get("countries") or {}).items():
        entry = {"country": country, "first_level": list(spec.get("first_level") or []),
                 "lower_level": list(spec.get("lower_level") or [])}
        for name in [country, *(spec.get("aliases") or [])]:
            out[str(name).casefold()] = entry
    return out

def jurisdiction_level(value: str, country: str | None) -> tuple[str, CheckResult, str]:
    """(code, result, detail): JURISDICTION_LEVEL PASS/WARN, or JURISDICTION_LEVEL_UNKNOWN
    WARN when the country's hierarchy isn't configured."""
    entry = admin_levels().get((country or "").casefold())
    if entry is None:
        return "JURISDICTION_LEVEL_UNKNOWN", CheckResult.WARN, (
            f"no administrative hierarchy is configured for {country or 'this country'}, so "
            "the level of the value can't be checked")
    terms = [(t, "first") for t in entry["first_level"]] + \
        [(t, "lower") for t in entry["lower_level"]]
    terms.sort(key=lambda x: len(x[0]), reverse=True)
    pattern = re.compile("|".join(rf"(?<![\w-])({re.escape(t)})(?![\w-])" for t, _ in terms),
                         re.IGNORECASE)
    kinds = {t.casefold(): k for t, k in terms}
    found = [(m.group(0).casefold(), kinds.get(m.group(0).casefold()))
             for m in pattern.finditer(value)]
    lower = [t for t, k in found if k == "lower"]
    if lower and not any(k == "first" for _, k in found):
        return "JURISDICTION_LEVEL", CheckResult.WARN, (
            f"{value!r} names a {lower[0]}, below the first-level subdivision of "
            f"{entry['country']} ({', '.join(entry['first_level'][:3])})")
    return "JURISDICTION_LEVEL", CheckResult.PASS, "first-level subdivision" + (
        f" ({found[0][0]})" if found else "")

# --- the checks -------------------------------------------------------------------------------

def semantic_checks(claim: FieldClaim, d: FieldDefinition, normalized: Any, located: list,
                    index: Any, research: CityResearchState, q: ClaimQualifiers, add: Add,
                    fields: Mapping[str, FieldDefinition], current_year: int) -> None:
    if claim.value_status is not ValueStatus.KNOWN or normalized is None or index is None:
        return
    is_year = d.type == "integer" and d.key.endswith("_year")
    contexts = context_texts(claim, located, index)
    if d.risk == "high" and d.subject == "city" and not is_year:
        _check_city_named(contexts, _titles(located), research, add,
                          _scope(index, located, research))
    if d.key == "jurisdiction":
        add(*jurisdiction_level(str(normalized), research.country))
    if d.measurement == "population":
        _check_population(d, contexts, add)
    if d.measurement == "withdrawal":
        _check_withdrawal(contexts, add)
    if d.authority == "designated_dataset":
        _check_designated(d, located, add)
    if is_year and d.depends_on:
        _check_year_linked(int(normalized), d, located, index, fields, q, add)
    elif d.year_required or d.freshness:
        _check_measurement_year(d, q, add, current_year)
    if d.key == "utility_name":
        url = utility_service_evidence(index, str(normalized), research)
        _service_result(url, str(normalized), add)
    elif d.key.startswith("utility_reported_city_population") and not is_year:
        _check_reported_by_serving_utility(located, index, research, add)

def _scope(index: Any, located: list, research: CityResearchState):
    """The cited document's scope (approved city identity only, else None)."""
    from water_source_research.services.geo_match import index_scope, matcher_for
    matcher = matcher_for(research.city_name, research.country)
    if matcher is None or not located:
        return None
    return index_scope(matcher, index, located[0][0].url)

def _check_city_named(contexts: list[str], titles: list[str], research: CityResearchState,
                      add: Add, scope: Any = None) -> None:
    text = "\n".join([*contexts, *titles])
    where = names_city(text, research.city_name, research.country, scope)
    if where == "TARGET":
        add("CITY_NAMED", CheckResult.PASS, None)
    elif where == "OTHER":
        add("WRONG_PLACE", CheckResult.WARN,
            f"the evidence names other places, not {research.city_name}")
    else:
        add("CITY_NOT_NAMED", CheckResult.WARN,
            f"neither the quoted sentence, its heading nor the document title names "
            f"{research.city_name}")

def _check_population(d: FieldDefinition, contexts: list[str], add: Add) -> None:
    text = " ".join(contexts)
    if POPULATION_WORDS.search(text):
        add("MEASUREMENT_TERM", CheckResult.PASS, "stated as a population")
    else:
        add("MEASUREMENT_TERM", CheckResult.WARN,
            "the evidence doesn't state the figure as a population")
    if d.key == "city_population_millions":
        hit = SERVICE_AREA_WORDS.search(text)
        if hit:
            add("SERVICE_AREA_POPULATION", CheckResult.WARN,
                f"the figure may be a service-area or served population ({hit.group(0)!r}), "
                "not the city's population")

def _check_withdrawal(contexts: list[str], add: Add) -> None:
    text = " ".join(contexts)
    yes, no = WITHDRAWAL_WORDS.search(text), NOT_WITHDRAWAL_WORDS.search(text)
    if yes:
        add("MEASUREMENT_TYPE", CheckResult.PASS, f"withdrawal ({yes.group(0)!r})")
    elif no:
        add("MEASUREMENT_TYPE_MISMATCH", CheckResult.WARN,
            f"the figure is described as {no.group(0)!r}, not as water withdrawn")
    else:
        add("MEASUREMENT_TYPE_UNKNOWN", CheckResult.WARN,
            "the evidence doesn't say the figure is water withdrawn")

def _check_designated(d: FieldDefinition, located: list, add: Add) -> None:
    dataset = d.designated_dataset or {}
    hosts = [h.casefold() for h in dataset.get("hosts") or []]
    if not hosts:
        add("DESIGNATED_SOURCE_MISSING", CheckResult.WARN,
            "no designated city population dataset is configured; the value needs a person")
        return
    cited = [(urlsplit(ref.url).hostname or "").casefold() for ref, _ in located]
    if any(c == h or c.endswith("." + h) for c in cited for h in hosts):
        add("DESIGNATED_SOURCE", CheckResult.PASS, dataset.get("name"))
    else:
        add("DESIGNATED_SOURCE_MISSING", CheckResult.WARN,
            f"not cited from the designated dataset ({dataset.get('name')})")

def _measurement_terms(parent: FieldDefinition | None) -> re.Pattern[str]:
    words = list(parent.keywords) if parent else []
    extra = []
    if parent is not None and parent.measurement == "population":
        extra.append(POPULATION_WORDS.pattern)
    if parent is not None and parent.measurement == "withdrawal":
        extra.append(WITHDRAWAL_WORDS.pattern)
    alternatives = [rf"\b{re.escape(w)}\b" for w in words if len(w) > 2] + extra
    return re.compile("|".join(alternatives) or r"(?!x)x", re.IGNORECASE)

def _figure(text: str, year: int) -> bool:
    """A number other than years (a measurement's figure)."""
    numbers = re.findall(r"\d[\d,.   ]*\d|\d", text)
    years = {str(y) for y in years_of(text)}
    return any(n.strip(",.") not in years and len(n.strip(",.")) >= 2 for n in numbers)

def _check_year_linked(year: int, d: FieldDefinition, located: list, index: Any,
                       fields: Mapping[str, FieldDefinition], q: ClaimQualifiers,
                       add: Add) -> None:
    parent = fields.get(d.depends_on[0])
    terms = _measurement_terms(parent)
    date_only = False
    for ref, loc in located:
        sentence = sentence_of(index, ref, loc)
        page = page_text(index, ref)
        if is_date_line(ref.passage):  # the quote is a page/publication date
            date_only = True
            continue
        if year in years_of(sentence) and year not in date_line_years(page) \
                and not is_date_line(sentence) \
                and (terms.search(sentence) or _figure(sentence, year)):
            q.year_basis = "passage" if sentence.strip() != ref.passage.strip() else "quote"
            add("YEAR_LINKED", CheckResult.PASS, "stated in the same sentence as the measurement")
            return
        line, below = governed_lines(page, ref.passage)
        if line is not None and years_of(line).count(year) and len(set(years_of(line))) == 1 \
                and any(terms.search(x) or _figure(x, year) for x in below):
            kind = "record" if re.match(r"^\W*\w+(?:\s+\w+)?\s*[:=]", line) else (
                "table" if any(len(re.split(r"\s{2,}|\t|\|", x.strip())) > 1 for x in below)
                else "heading")
            q.year_basis = kind
            add("YEAR_LINKED", CheckResult.PASS, f"{kind}: {line.strip()[:60]!r}")
            return
        link = structural_year(page, ref.passage)
        if link.year == year and link.basis == "table" and "row" in link.detail:
            q.year_basis = "table"
            add("YEAR_LINKED", CheckResult.PASS, "table row labelled with the year")
            return
        if is_date_line(ref.passage) or is_date_line(sentence):
            date_only = True
    add("YEAR_NOT_LINKED", CheckResult.WARN,
        "the year is a page or publication date, not the year of the measurement" if date_only
        else "the evidence doesn't tie the year to the measurement (sentence, table, heading, "
             "caption or record)")

def _check_measurement_year(d: FieldDefinition, q: ClaimQualifiers, add: Add,
                            current_year: int) -> None:
    linked = q.reference_year is not None and q.year_basis in LINKED_YEAR_BASES
    if d.year_required:
        if linked:
            add("MEASUREMENT_YEAR", CheckResult.PASS, f"{q.reference_year} ({q.year_basis})")
        else:
            add("MEASUREMENT_YEAR", CheckResult.WARN,
                "no year is stated with the measurement" + (
                    " (only the publication date)" if q.year_basis == "document" else ""))
    if d.freshness != "most_recent":
        return
    if not linked:
        add("FRESHNESS_UNKNOWN", CheckResult.WARN,
            "how current the value is can't be established (no measurement year)")
        return
    expected = current_year - (d.publication_lag_years or 0) - (d.reporting_cadence_years or 0)
    if q.reference_year < expected:
        add("POSSIBLY_SUPERSEDED", CheckResult.WARN,
            f"{q.reference_year} is older than the latest expected reporting year ({expected}); "
            "a more recent value may exist")
    else:
        add("FRESHNESS", CheckResult.PASS, f"{q.reference_year} (expected {expected} or later)")

# --- utilities serving the city ------------------------------------------------------------

def _sentences(text: str) -> Iterable[str]:
    return (s for s in re.split(r"(?<=[.!?;])\s+|\n+", text) if s.strip())

def _name_pattern(name: str) -> re.Pattern[str] | None:
    words = re.findall(r"[\w'’]+", name)
    if not words:
        return None
    return re.compile(r"(?<!\w)" + r"[\s\-–,.()]*".join(map(re.escape, words)) + r"(?!\w)",
                      re.IGNORECASE)

def utility_service_evidence(index: Any, name: str, research: CityResearchState) -> str | None:
    """The URL of a run chunk stating that ``name`` serves the city (the name, the city outside
    the name and water-body names, and a service verb or utility noun in one sentence)."""
    cache = index.__dict__.setdefault("_service_cache", {})
    key = (name.casefold(), research.city_name.casefold())
    if key in cache:
        return cache[key]
    found = service_statement_url(((c.text, c.url) for chunks in index.by_key.values()
                                   for c in chunks), name, research.city_name)
    cache[key] = found
    return found

def service_statement_url(texts: Iterable[tuple[str, str]], name: str,
                          city_name: str) -> str | None:
    """The URL of the first (text, url) whose sentence names ``name``, names the city outside
    the name and water-body names, and has a service verb or utility noun."""
    pattern = _name_pattern(name)
    if pattern is None:
        return None
    city = city_pattern(city_name)
    for text, url in texts:
        if not pattern.search(text):
            continue
        for s in _sentences(text):
            if pattern.search(s) is None:
                continue
            rest = without_water_bodies(pattern.sub(" ", s))
            if city.search(rest) and (SERVICE_VERBS.search(rest) or UTILITY_NOUNS.search(rest)):
                return url
    return None

def _service_result(url: str | None, name: str, add: Add) -> None:
    if url:
        add("UTILITY_SERVES_CITY", CheckResult.PASS, f"stated in {url}")
    else:
        add("UTILITY_SERVICE_NOT_STATED", CheckResult.WARN,
            f"no evidence in the run states that {name} serves the city")

def _check_reported_by_serving_utility(located: list, index: Any, research: CityResearchState,
                                       add: Add) -> None:
    """A utility-reported population counts when it comes from a utility shown to serve the
    city: one of the run's utility names with service evidence, cited from the same host."""
    hosts = {(urlsplit(ref.url).hostname or "").casefold() for ref, _ in located}
    fs = research.fields.get("utility_name")
    names = []
    for c in (fs.candidate_claims if fs else []):
        if c.value_status is ValueStatus.KNOWN and c.value and str(c.value) not in names:
            names.append(str(c.value))
    for name in names:
        url = utility_service_evidence(index, name, research)
        if url is None:
            continue
        name_hosts = {(urlsplit(e.url).hostname or "").casefold()
                      for c in fs.candidate_claims if str(c.value) == name for e in c.evidence}
        if hosts & name_hosts:
            add("UTILITY_SERVES_CITY", CheckResult.PASS,
                f"reported by {name}, which serves the city ({url})")
            return
    add("UTILITY_SERVICE_NOT_STATED", CheckResult.WARN,
        "the figure isn't cited from a utility shown to serve the city")
