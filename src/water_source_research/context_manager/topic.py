"""Is a passage on the field's drinking-water topic? (the topical half of the extraction gate)

Geography (context_manager/relevance.py) asks whether a passage is about the city; this asks
whether it is about what the field needs. A passage about the right city can still be off
topic (a company's headquarters, a congress venue), and those should not cost model calls.
Three decisions, judged on the passage together with its local context (the chunk's title
and the short heading lines above it, so a table under "Water production 2023" or a section
"Wasserversorgung" counts even when the passage itself doesn't repeat the words):

- PASS: the field's own vocabulary (its keywords and the multilingual terms of its field
  type), a named water body, or a passage from a verified serving utility's site or naming a
  known water feature of the city.
- UNCERTAIN: general water-supply vocabulary without the field's own terms, or a numeric table
  under a heading for numeric fields. Kept: the model decides.
- CLEARLY_IRRELEVANT: none of the above. Only these are skipped.

City-identity fields (name, jurisdiction, coordinates) are never judged off topic. No literal
match is required inside the passage itself; nothing here is geographic, so foreign
publishers and distant sources are untouched.
"""

import re
from functools import lru_cache
from water_source_research.schemas.research import EvidenceChunk, FieldDefinition
from water_source_research.services.places import HYDRONYM

PASS, UNCERTAIN, CLEARLY_IRRELEVANT = "PASS", "UNCERTAIN", "CLEARLY_IRRELEVANT"
TOPIC_POLICY_VERSION = "topic-1"

# General drinking-water / water-supply vocabulary (en, es, pt, de, fr).
WATER = (r"water|waters|potable|drinking|tap|supply|supplies|utility|utilities|waterworks|"
         r"sewer\w*|sanitation|wastewater|treatment|hydro\w*|watershed|catchment|basin|"
         r"agua|aguas|potable|abastecimiento|abastec\w*|suministro|acueducto|saneamiento|"
         r"alcantarillado|cuenca|[aá]gua|abastecimento|saneamento|mananci\w*|wasser\w*|"
         r"trinkwasser|versorgung|quell\w*|eau|eaux|approvisionnement|assainissement|"
         r"captage|r[eé]seau")
# Terms per field type (beyond each field's own keywords).
FIELD_TYPE_TERMS = {
    "population": r"population|inhabitants|residents|people|persons|census|customers|"
                  r"households|served|poblaci[oó]n|habitantes|censo|vecinos|popula[cç][aã]o|"
                  r"moradores|einwohner\w*|bev[öo]lkerung|haushalte|habitants|recensement|"
                  r"millions?|millones|milh[oõ]es|millionen",
    "utility": r"utility|utilities|authority|board|department|company|corporation|"
               r"operator|services?|waterworks|empresa|compa[nñ][ií]a|servicio|"
               r"concesionari\w*|sanitaria|companhia|autarquia|stadtwerke|wasserwerk\w*|"
               r"betrieb\w*|r[eé]gie|soci[eé]t[eé]|syndicat",
    "water_source": r"source|sources|river|rivers|lake|lakes|spring|springs|aquifer\w*|"
                    r"groundwater|well|wells|borehole\w*|reservoir|dam|intake|surface|"
                    r"r[ií]o|lago|laguna|manantial\w*|vertiente|acu[ií]fero|subterr[aá]ne\w*|"
                    r"pozo\w*|embalse|fonte|nascente|po[cç]o|fluss|see|quelle\w*|grundwasser|"
                    r"brunnen|rivi[eè]re|fleuve|nappe|forage|captage|desalin\w*|desalaci[oó]n",
    "source_type": r"surface|groundwater|aquifer|well|river|lake|desalin\w*|seawater|"
                   r"superficial|subterr[aá]ne\w*|grundwasser|oberfl[aä]chen\w*|souterrain",
    "transfer_system": r"aqueduct|transfer|pipeline|canal|tunnel|conveyance|main|mains|"
                       r"acueducto|trasvase|t[uú]nel|aqueduto|transposi[cç][aã]o|leitung\w*|"
                       r"aqueduc|transfert|conduite",
    "point_of_diversion": r"intake|diversion|abstraction|bocatoma|captaci[oó]n|toma|"
                          r"capta[cç][aã]o|entnahme|prise\s+d.eau|coordinates|latitude|"
                          r"longitude",
    "infrastructure": r"plant|plants|station|stations|pipeline|pipes?|network|reservoir|"
                      r"tank|filtration|treatment|pumping|infrastructure|planta|estaci[oó]n|"
                      r"red|tuber[ií]a|esta[cç][aã]o|rede|anlage|werk|netz|usine|station",
    "withdrawal": r"withdraw\w*|abstract\w*|production|produced|pumped|supplied|demand|"
                  r"consumption|m3|m³|megalit\w*|litres?|liters?|gallons?|mld|mgd|ml/d|"
                  r"extracci[oó]n|captaci[oó]n|producci[oó]n|consumo|caudal|"
                  r"capta[cç][aã]o|produ[cç][aã]o|vaz[aã]o|entnahme|f[öo]rder\w*|"
                  r"verbrauch|pr[eé]l[eè]vement|consommation|d[eé]bit",
    "water_quality": r"quality|turbidity|contaminant\w*|nitrate\w*|lead|compliance|"
                     r"treatment|chlorin\w*|e\.?\s?coli|bacteria|sampling|test\w*|"
                     r"calidad|turbiedad|cumplimiento|muestreo|qualidade|qualit[äa]t|"
                     r"qualit[eé]|analys\w*|parameter\w*",
}
NUMERIC_TYPES = frozenset({"population", "withdrawal"})
# Field words that are common outside water supply ("revenue source", "network", "annual
# report"): they count only together with drinking-water vocabulary.
GENERIC = frozenset({
    "source", "sources", "network", "service", "services", "share", "percent", "daily",
    "annual", "average", "historical", "trend", "previous years", "annual report", "report",
    "storage", "main", "mains", "transfer", "plant", "plants", "station", "stations", "test",
    "testing", "lead", "company", "corporation", "board", "authority", "department", "operator",
    "people", "persons", "households", "customers", "served", "serves", "demand", "production",
    "produced", "consumption", "estimate", "city", "state", "region", "seasonal", "summer",
    "winter", "backup", "red", "rede", "netz", "werk", "anlage", "empresa", "servicio",
    "millions", "million", "millones", "milhões", "millionen", "surface", "well", "wells",
    "compliance", "parameter", "parameters", "analysis", "sampling", "supplied", "pipes",
    "pipe", "infrastructure", "canal", "tunnel", "toma", "diversion"})
NEVER_OFF_TOPIC = frozenset({"city_identity", "references"})
HEADING_LINES = 15

@lru_cache(maxsize=256)
def _field_re(field_type: str, keywords: tuple[str, ...]) -> re.Pattern[str]:
    parts = [FIELD_TYPE_TERMS[field_type]] if field_type in FIELD_TYPE_TERMS else []
    parts += [re.escape(k).replace(r"\ ", r"\s+") for k in keywords if k]
    return re.compile(rf"\b(?:{'|'.join(parts)})\b" if parts else r"(?!x)x", re.IGNORECASE)

_WATER_RE = re.compile(rf"\b(?:{WATER})\b", re.IGNORECASE)
_DIGITS = re.compile(r"\d[\d.,]*\s*\d|\d{2,}")

def local_context(chunk: EvidenceChunk, passage: str) -> str:
    """The chunk's title and the short heading-like lines above the passage."""
    text = chunk.text or ""
    head = (passage or "")[:40]
    pos = text.find(head) if head else -1
    lines = text[:pos].split("\n")[-HEADING_LINES:] if pos > 0 else []
    headings = [ln.strip() for ln in lines if ln.strip() and len(ln.strip()) <= 120
                and not ln.strip().endswith((".", ";", ","))]
    return "\n".join([chunk.title or "", *headings[-3:]])

def decide(chunk: EvidenceChunk, passage: str, field: FieldDefinition,
           geo_rules: list[str] | None = None) -> tuple[str, str]:
    """(PASS | UNCERTAIN | CLEARLY_IRRELEVANT, reason) for ``passage`` and ``field``."""
    if field.field_type in NEVER_OFF_TOPIC:
        return UNCERTAIN, "identity field: never judged off topic"
    rules = set(geo_rules or [])
    if rules & {"VERIFIED_UTILITY", "LOCAL_WATER_FEATURE"}:
        return PASS, "a verified utility's site or a known water feature"
    context = local_context(chunk, passage)
    own = _field_re(field.field_type, tuple(field.keywords))
    water = _WATER_RE.search(passage) or _WATER_RE.search(context)
    for text in (passage, context):
        for m in own.finditer(text):
            if m.group(0).casefold() not in GENERIC or water:
                return PASS, f"field term {m.group(0)!r}"
    if HYDRONYM.search(passage) and field.field_type not in NUMERIC_TYPES:
        return PASS, "names a water body"
    if water:
        return UNCERTAIN, f"water-supply term {water.group(0)!r}, not the field's own"
    if field.field_type in NUMERIC_TYPES and _DIGITS.search(passage) and context.strip():
        return UNCERTAIN, "figures under a heading"
    return CLEARLY_IRRELEVANT, "no drinking-water or field vocabulary in passage or headings"
