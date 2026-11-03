"""The water-supply relationship graph: explicit cited predicates only, role-aware utility
relationships from the registry, passage geography, temporal and contradiction handling,
no merging of names. Identities and sentences are synthetic test data modelled on the pilot
cities; production code holds no city, utility or source names."""

import pytest
from water_source_research.schemas.research import (
    ClaimQualifiers, EvidenceChunk, EvidenceRef, FieldClaim,
)
from water_source_research.services import geo_match, supply_relationships, utility_registry
from water_source_research.services.city_identity import (
    CityIdentity, CountryInfo, Homonym, IdentityName, NameSource, Region,
)
from water_source_research.services.field_state import initialize_city_state
from water_source_research.services.geo_match import GeoMatcher
from water_source_research.services.supply_relationships import build_relationships

GN = NameSource(source="geonames", id="1", property="cities500")
WD = NameSource(source="wikidata", id="Q1", property="label:en")

def identity(city, country, iso2, region, code, term, names=(), homonyms=()):
    return GeoMatcher(CityIdentity(
        city=city, country=country, status="APPROVED",
        region=Region(name=region, iso_3166_2=code, level_term=term, verified=True),
        country_info=CountryInfo(name=country, iso2=iso2, cctld=iso2.lower()),
        names=[IdentityName(name=n, sources=[GN, WD]) for n in names],
        homonyms=[Homonym(sources=[GN], **h) for h in homonyms]))

HOUSTON = identity("Houston", "United States of America", "US", "Texas", "US-TX", "state",
                   homonyms=[{"name": "Houston", "country": "United States", "iso2": "US",
                              "region": "Mississippi", "population": 3544}])
SANTIAGO = identity("Santiago", "Chile", "CL", "Santiago Metropolitan Region", "CL-RM",
                    "region", names=["Santiago de Chile"],
                    homonyms=[{"name": "Santiago de Cali", "country": "Colombia",
                               "iso2": "CO", "region": "Valle del Cauca",
                               "population": 2_392_877}])
VIENNA = identity("Vienna", "Austria", "AT", "Vienna", "AT-9", "state", names=["Wien"])
NAIROBI = identity("Nairobi", "Kenya", "KE", "Nairobi City County", "KE-30", "county")
MATCHERS = {"Houston": HOUSTON, "Santiago": SANTIAGO, "Vienna": VIENNA, "Nairobi": NAIROBI}
COUNTRY = {"Houston": "United States of America", "Santiago": "Chile", "Vienna": "Austria",
           "Nairobi": "Kenya"}

@pytest.fixture(autouse=True)
def synthetic_identities(monkeypatch):
    def pick(city, country):
        return MATCHERS.get((city or "").strip())
    for mod in (geo_match, utility_registry, supply_relationships):
        monkeypatch.setattr(mod, "matcher_for", pick)

_n = [0]

def chunk(url, text, source_type="OTHER", title="", page=None):
    _n[0] += 1
    return EvidenceChunk(chunk_id=f"c{_n[0]}", url=url, text=text, title=title,
                         source_type=source_type, page_number=page)

def graph(city, chunks, utilities=(), sources=()):
    s = initialize_city_state(city, COUNTRY[city])
    for name, url, quote in utilities:
        s.fields["utility_name"].candidate_claims.append(FieldClaim(
            field_key="utility_name", value=name, confidence=0.8,
            evidence=[EvidenceRef(url=url, passage=quote)]))
    for name, kind, relations in sources:
        s.fields["water_source"].candidate_claims.append(FieldClaim(
            field_key="water_source", value=name, confidence=0.8,
            evidence=[EvidenceRef(url="https://x.example", passage=name)],
            qualifiers=ClaimQualifiers(source_kind=kind, relations=list(relations))))
    return build_relationships(s, chunks, {c.url: {"title": c.title, "source_type":
                                                   c.source_type} for c in chunks},
                               document_ids={c.url: i for i, c in enumerate(chunks, 1)})

def rel(g, kind, subject_word, object_word=None):
    out = [r for r in g.of_kind(kind) if subject_word.casefold() in r.subject.name.casefold()
           and (object_word is None or object_word.casefold() in r.object.name.casefold())]
    return out[0] if out else None

# --- Houston-like: operator, wholesaler, vendor -----------------------------------------------

def test_a_municipal_operator_and_a_raw_water_wholesaler_keep_their_roles():
    city = chunk("https://www.houstontx.gov/water/", "Houston Public Works provides drinking "
                 "water to residents of Houston, Texas.", "GOVERNMENT")
    news = chunk("https://www.example-news.com/a", "The Coastal Water Authority supplies "
                 "untreated raw water to Houston, Texas, from Lake Livingston.")
    g = graph("Houston", [city, news], utilities=[
        ("Houston Public Works", city.url, "Houston Public Works provides drinking water")])
    op = rel(g, "UTILITY_SERVES_CITY", "Houston Public Works")
    assert op is not None and op.status == "VERIFIED" and op.basis == ["GOVERNMENT_OR_REGULATOR"]
    ev = op.evidence[0]
    assert ev.document_id == 1 and ev.chunk_id == city.chunk_id and ev.span is not None
    wholesale = rel(g, "SUPPLIES_WHOLESALE_TO", "Coastal Water Authority")
    assert wholesale is not None and wholesale.object.type == "city"
    assert wholesale.status == "SUPPORTED"  # one independent statement
    assert rel(g, "UTILITY_SERVES_CITY", "Coastal Water Authority") is None  # never retail

def test_an_unrelated_water_company_gets_no_relationship():
    vendor = chunk("https://gulfwatersolutions.com/", "Gulf Water Solutions held a water "
                   "technology conference in Houston, Texas.", "UTILITY",
                   "Gulf Water Solutions")
    g = graph("Houston", [vendor])
    assert g.relationships == []

def test_a_self_description_is_supported_not_verified():
    own = chunk("https://www.bayouwater.example/about", "Bayou Water Authority supplies "
                "drinking water to 300,000 residents of Houston, Texas.", "UTILITY",
                "Bayou Water Authority")
    g = graph("Houston", [own], utilities=[("Bayou Water Authority", own.url,
                                            "Bayou Water Authority supplies drinking water")])
    r = rel(g, "UTILITY_SERVES_CITY", "Bayou Water Authority")
    assert r is not None and r.status == "SUPPORTED" and r.basis == ["SELF_DESCRIPTION"]

# --- Santiago-like -----------------------------------------------------------------------------

def test_a_utility_named_only_by_a_claim_is_a_candidate():
    home = chunk("https://www.aguasandinas.cl/", "Bienvenido a Aguas Andinas. Paga tu cuenta.",
                 "UTILITY", "Aguas Andinas")
    g = graph("Santiago", [home], utilities=[("Aguas Andinas", home.url, "Aguas Andinas")])
    r = rel(g, "UTILITY_SERVES_CITY", "Aguas Andinas")
    assert r is not None and r.status == "CANDIDATE" and r.basis == ["CLAIM"]

def test_an_explicit_source_to_utility_statement():
    gov = chunk("https://www.siss.gob.cl/informe", "Aguas Andinas obtiene agua del río Maipo "
                "para la ciudad.", "GOVERNMENT")
    g = graph("Santiago", [gov], utilities=[("Aguas Andinas", gov.url, "Aguas Andinas")])
    r = rel(g, "SOURCE_SUPPLIES_UTILITY", "Maipo", "Aguas Andinas")
    assert r is not None and r.status == "VERIFIED" and r.subject.kind == "river"
    assert "obtiene agua del" in r.evidence[0].predicate.casefold()

def test_the_client_keeps_its_role_and_the_contractor_gets_none():
    text = ("Aguas Andinas encargó a Hidroingeniería Ltda. el diseño de la nueva planta en "
            "Santiago, Chile. Hidroingeniería Ltda. diseñó y construyó la planta de "
            "tratamiento de agua potable.")
    doc = chunk("https://www.example-eng.cl/proyecto", text)
    g = graph("Santiago", [doc], utilities=[("Aguas Andinas", doc.url, "Aguas Andinas")])
    assert not [r for r in g.of_kind("UTILITY_SERVES_CITY")
                if "hidroingenier" in r.subject.name.casefold()]

# --- Vienna-like: springs and systems ---------------------------------------------------------

SPRINGS = chunk("https://www.wien.gv.at/wienwasser/quellen",
                "The Kaiserbrunn spring is one of the springs of the First Vienna Mountain "
                "Spring Pipeline.\nThe Stixenstein spring in Lower Austria supplies water to "
                "Vienna.", "GOVERNMENT")

def test_a_spring_is_part_of_a_system_only_when_stated():
    g = graph("Vienna", [SPRINGS])
    part = g.of_kind("SOURCE_PART_OF_SYSTEM")
    assert len(part) == 1
    assert "Kaiserbrunn" in part[0].subject.name and part[0].object.type == "system"
    assert part[0].subject.kind == "spring"

def test_springs_are_not_merged_and_a_distant_source_is_kept():
    g = graph("Vienna", [SPRINGS])
    ids = {e.entity_id for e in g.entities if e.type == "source"}
    assert len(ids) == 2  # Kaiserbrunn and Stixenstein stay separate entities
    distant = rel(g, "SOURCE_SUPPLIES_CITY", "Stixenstein")
    assert distant is not None and distant.status == "VERIFIED"  # its location doesn't matter

# --- general ----------------------------------------------------------------------------------

def test_two_utilities_serving_different_areas():
    a = chunk("https://www.houstontx.gov/a", "Northside Water Company supplies drinking water "
              "to the northern districts of Houston, Texas.", "GOVERNMENT")
    b = chunk("https://www.houstontx.gov/b", "Southbay Water Company supplies drinking water "
              "to southern neighbourhoods of Houston, Texas.", "GOVERNMENT")
    g = graph("Houston", [a, b], utilities=[("Northside Water Company", a.url, "Northside"),
                                            ("Southbay Water Company", b.url, "Southbay")])
    assert rel(g, "UTILITY_SERVES_CITY", "Northside") and rel(g, "UTILITY_SERVES_CITY",
                                                              "Southbay")

def test_a_regional_supplier_serving_several_cities_links_only_the_target():
    doc = chunk("https://www.example-news.com/b", "The Trinity Water Authority supplies treated "
                "water to the cities of Houston, Pasadena and Baytown in Texas.")
    g = graph("Houston", [doc])
    r = g.of_kind("SUPPLIES_WHOLESALE_TO")
    assert len(r) == 1 and r[0].object.type == "city" and r[0].object.name == "Houston"

def test_a_multi_country_report_counts_only_the_target_section():
    text = ("Urban water in East Africa\n\nKampala, Uganda\nThe city takes its water from Lake "
            "Victoria.\n\nNairobi, Kenya\nNairobi takes its water from the Thika River.")
    g = graph("Nairobi", [chunk("https://documents.worldbank.org/x", text, "TECHNICAL")])
    assert rel(g, "SOURCE_SUPPLIES_CITY", "Thika", "Nairobi") is not None
    assert rel(g, "SOURCE_SUPPLIES_CITY", "Victoria") is None

def test_a_river_and_a_utility_together_without_a_predicate_are_not_a_relationship():
    doc = chunk("https://www.example-news.cl/c", "Aguas Andinas and the Maipo River were "
                "discussed at a conference in Santiago, Chile.")
    g = graph("Santiago", [doc], utilities=[("Aguas Andinas", doc.url, "Aguas Andinas")])
    assert g.of_kind("SOURCE_SUPPLIES_UTILITY") == [] and g.of_kind("SOURCE_SUPPLIES_CITY") == []

def test_historical_needs_temporal_evidence_and_past_tense_alone_is_unknown():
    hist = chunk("https://www.example-history.org/a", "Until 1998, Houston drew its water from "
                 "the Brazos River in Texas.")
    past = chunk("https://www.example-history.org/b", "Houston drew its water from the San "
                 "Jacinto River in Texas.")
    g = graph("Houston", [hist, past])
    h = rel(g, "SOURCE_SUPPLIES_CITY", "Brazos")
    assert h is not None and h.status == "HISTORICAL" and h.temporal == "HISTORICAL"
    p = rel(g, "SOURCE_SUPPLIES_CITY", "San Jacinto")
    assert p is not None and p.status != "HISTORICAL" and p.temporal == "UNKNOWN"

def test_a_discontinuation_beside_a_current_statement_is_contradicted():
    now = chunk("https://www.example-a.org/x", "Houston takes its water from the Brazos River, "
                "Texas.")
    gone = chunk("https://www.example-b.org/y", "Houston no longer takes water from the Brazos "
                 "River, Texas.")
    g = graph("Houston", [now, gone])
    r = rel(g, "SOURCE_SUPPLIES_CITY", "Brazos")
    assert r is not None and r.status == "CONTRADICTED" and len(r.evidence) == 2

def test_a_homonyms_supply_statement_is_rejected():
    ms = chunk("https://www.example-ms.org/x", "Houston, Mississippi takes its water from the "
               "Eutaw Aquifer.")
    g = graph("Houston", [ms])
    assert g.of_kind("SOURCE_SUPPLIES_CITY") == []
    assert any("Eutaw" in r.subject for r in g.rejected)

def test_two_independent_passages_support_one_relationship():
    a = chunk("https://www.example-a.org/1", "Houston, Texas takes its water from Lake "
              "Livingston.")
    b = chunk("https://www.example-b.org/2", "Houston, Texas draws water from Lake Livingston.")
    g = graph("Houston", [a, b])
    r = rel(g, "SOURCE_SUPPLIES_CITY", "Livingston")
    assert r is not None and len(r.evidence) == 2 and r.status == "VERIFIED"

def test_a_claim_part_of_system_relation_is_kept_with_its_quote():
    quote = "Hirschwang springs feed the Spring Line"
    g = graph("Vienna", [chunk("https://www.wien.gv.at/q", quote + ".", "GOVERNMENT")],
              sources=[("Hirschwang springs", "spring", [
                  {"relation": "part_of_system", "target": "Spring Line", "quote": quote,
                   "url": "https://www.wien.gv.at/q"}])])
    r = g.of_kind("SOURCE_PART_OF_SYSTEM")
    assert r and r[0].evidence[0].quote == quote

def test_a_rebuild_is_identical():
    chunks = [SPRINGS, chunk("https://www.houstontx.gov/z", "Houston Public Works provides "
                             "drinking water to Houston, Texas.", "GOVERNMENT")]
    a = graph("Vienna", chunks).model_dump()
    b = graph("Vienna", chunks).model_dump()
    assert a == b


# --- persistence -------------------------------------------------------------------------------

def test_a_stored_graph_is_replaced_not_duplicated(tmp_path):
    from procedure_helpers import make_engine
    from water_source_research.services.evidence_store import EvidenceRepository
    repo = EvidenceRepository(make_engine(tmp_path, "rel.db"))
    g = graph("Vienna", [SPRINGS]).model_dump(mode="json")
    n = repo.save_relationships("run-1", g)
    assert repo.save_relationships("run-1", g) == n  # rebuild: same rows, no duplicates
    stored = repo.relationships("run-1")
    assert len(stored["relationships"]) == len(g["relationships"])
    assert stored["geo_basis"] == g["geo_basis"] and stored["versions"] == ["relationship-1"]
    other = {**g, "policy_version": "relationship-0"}
    repo.save_relationships("run-1", other)  # another version is kept apart
    assert repo.relationships("run-1")["versions"] == ["relationship-0", "relationship-1"]
    assert repo.relationships("run-2")["relationships"] == []

# --- precision lessons from the stored runs ----------------------------------------------------

def test_a_title_naming_the_city_does_not_place_a_list_or_glossary_sentence():
    """A congress report titled with the city lists utilities from elsewhere; a glossary
    defines a treatment plant. Neither sentence places a service at the city."""
    congress = chunk("https://www.example-congress.org/report.pdf", "Speakers came from "
                     "Harbour Water Board (Scotland) and Island Water Authority, which supplies "
                     "drinking water to customers.", "TECHNICAL", "Congress Report – Houston")
    g = graph("Houston", [congress], utilities=[("Harbour Water Board", congress.url, "x"),
                                                ("Island Water Authority", congress.url, "x")])
    assert not [r for r in g.of_kind("UTILITY_SERVES_CITY") if r.status in ("SUPPORTED",
                                                                            "VERIFIED")]

def test_a_comprises_list_ends_at_the_next_clause():
    text = ("The spring zone of the First Valley Spring Pipeline comprises the Alder spring and "
            "the Birch spring, while the Second Valley Spring Pipeline draws on the Cedar "
            "spring.")
    g = graph("Vienna", [chunk("https://www.wien.gv.at/z", text, "GOVERNMENT")])
    parts = {(r.subject.name, r.object.name) for r in g.of_kind("SOURCE_PART_OF_SYSTEM")}
    assert ("Alder spring", "First Valley Spring Pipeline") in parts
    assert ("Birch spring", "First Valley Spring Pipeline") in parts
    assert not any("Second" in s or "Cedar" in s for s, _ in parts)

def test_no_plans_for_bulk_supply_is_not_a_wholesale_relationship():
    doc = chunk("https://www.example-groundwater.org/x", "The Delta Water Company has indicated "
                "that there are currently no plans to exploit groundwater in Houston, Texas for "
                "bulk supply.", "TECHNICAL")
    g = graph("Houston", [doc], utilities=[("Delta Water Company", doc.url, "Delta Water")])
    assert g.of_kind("SUPPLIES_WHOLESALE_TO") == []

# --- feature location ---------------------------------------------------------------------------

def test_a_springs_location_is_recorded_not_used_against_the_city():
    g = graph("Vienna", [SPRINGS])
    loc = g.of_kind("SOURCE_LOCATED_IN")
    assert [(r.subject.name, r.object.name, r.object.type) for r in loc] == [
        ("Stixenstein spring", "Lower Austria", "region")]
    assert rel(g, "SOURCE_SUPPLIES_CITY", "Stixenstein").status == "VERIFIED"

def test_a_distant_feature_without_a_link_to_the_city_supplies_nothing():
    doc = chunk("https://www.example-dams.org/x", "The Katse Dam in Lesotho stores water for "
                "irrigation.")
    g = graph("Nairobi", [doc])
    assert g.of_kind("SOURCE_SUPPLIES_CITY") == [] and g.of_kind("SOURCE_SUPPLIES_UTILITY") == []
    assert [r.object.name for r in g.of_kind("SOURCE_LOCATED_IN")] == ["Lesotho"]
