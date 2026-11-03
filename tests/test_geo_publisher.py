"""Milestone 3.1 Phase 2 controls: the publisher's country is not the document's subject;
multi-country documents are MIXED (not elsewhere); a bare name next to a foreign country is
a homonym only where one is listed, else AMBIGUOUS; the target region in administrative form
places the country; a passage may take its place from its own section heading, never across
headings, pages or conflicting places.

Identities are built here (synthetic entries modelled on real places; no research answers),
so the tests don't depend on the drafted file."""

import pytest
from water_source_research.schemas.research import EvidenceChunk
from water_source_research.services.city_identity import (
    CityIdentity, CountryInfo, Homonym, IdentityName, NameSource, Region,
)
from water_source_research.services.geo_match import GeoMatcher

GN = NameSource(source="geonames", id="1", property="cities500")
WD = NameSource(source="wikidata", id="Q1", property="label:en")

def make(city, country, iso2, region, iso_3166_2=None, level_term=None, homonyms=(),
         names=()):
    return GeoMatcher(CityIdentity(
        city=city, country=country, status="APPROVED",
        region=Region(name=region, iso_3166_2=iso_3166_2, level_term=level_term,
                      verified=True),
        country_info=CountryInfo(name=country, iso2=iso2, cctld=iso2.lower()),
        names=[IdentityName(name=n, sources=[GN, WD]) for n in names],
        homonyms=[Homonym(sources=[GN], **h) for h in homonyms]))

@pytest.fixture(scope="module")
def nairobi():
    return make("Nairobi", "Kenya", "KE", "Nairobi City County", "KE-30", "county")

@pytest.fixture(scope="module")
def luanda():
    return make("Luanda", "Angola", "AO", "Luanda Province", "AO-LUA", "province")

@pytest.fixture(scope="module")
def houston():
    return make("Houston", "United States of America", "US", "Texas", "US-TX", "state",
                homonyms=[{"name": "Houston", "country": "United States", "iso2": "US",
                           "region": "Mississippi", "population": 3544},
                          {"name": "Houston", "country": "United Kingdom", "iso2": "GB",
                           "region": "Scotland", "population": 6420}])

@pytest.fixture(scope="module")
def vienna():
    return make("Vienna", "Austria", "AT", "Vienna", "AT-9", "state", names=["Wien"],
                homonyms=[{"name": "Vienna", "country": "United States", "iso2": "US",
                           "region": "Virginia", "population": 16_000}])

@pytest.fixture(scope="module")
def santiago():
    return make("Santiago", "Chile", "CL", "Santiago Metropolitan Region", "CL-RM", "region",
                names=["Santiago de Chile"],
                homonyms=[{"name": "Santiago de Cali", "country": "Colombia", "iso2": "CO",
                           "region": "Valle del Cauca", "population": 2_392_877},
                          {"name": "Santiago de Querétaro", "country": "Mexico", "iso2": "MX",
                           "region": "Querétaro", "population": 1_594_212}])

# --- positive controls: a foreign publisher writing about the city ----------------------------

def test_an_international_report_hosted_abroad_is_not_elsewhere(nairobi):
    for url in ("https://www.grida.no/resources/1234", "https://www.afd.fr/en/carte-des-projets/x",
                "https://documents.worldbank.org/en/publication/y"):
        s = nairobi.document_scope(url, "Water services in informal settlements",
                                   ["Nairobi's piped network reaches half of the settlements."])
        assert not s.elsewhere, url
        assert nairobi.match_place("Nairobi's piped network reaches half.", s).is_target, url
    named = nairobi.document_scope("https://www.grida.no/x", "Report",
                                   ["Nairobi, Kenya draws on the Ndakaini Dam."])
    assert named.label() == "TARGET_COUNTRY" and named.publisher == "OTHER"

def test_a_foreign_government_article_datelined_in_the_city(luanda):
    text = ("LUANDA — Despite rich water resources in Angola, poor water infrastructure has "
            "limited access to safe drinking water. Chinese companies built supply facilities.")
    s = luanda.document_scope("https://english.www.gov.cn/news/x.html",
                              "Water projects bolster living standards in Angola", [text])
    assert s.label() == "TARGET_COUNTRY" and not s.elsewhere
    assert luanda.match_place(text, s).status in ("CONFIRMED", "PROBABLE")

def test_a_multi_country_report_is_mixed_and_judged_passage_by_passage(luanda):
    texts = ["In Luanda, Angola, the utility supplies 40% of households.",
             "In Maputo, Mozambique, coverage rose to 60%.",
             "Mozambique and Kenya expanded standpipes; Kenya also metered connections."]
    s = luanda.document_scope("https://documents.worldbank.org/z", "Urban water in Africa",
                              texts)
    assert s.label() == "MIXED" and not s.elsewhere
    assert luanda.match_place(texts[0], s).status == "CONFIRMED"
    assert not luanda.match_place(texts[1], s).is_target  # Maputo's passage supports nothing
    # a bare "Luanda" next to Mozambique: no homonym is listed there, but nothing confirms it
    assert luanda.match_place("Luanda and Mozambique's capital share a contractor.",
                              s).status == "AMBIGUOUS"

def test_a_transboundary_source_named_with_the_city_is_kept(nairobi):
    text = "Nairobi, Kenya imports bulk water from a dam upstream in Uganda, per the treaty."
    s = nairobi.document_scope("https://www.mwe.go.ug/x", "Transboundary water", [text])
    # "a dam upstream in Uganda": Uganda is the source's location (feature location), so the
    # document is about Kenya, not a multi-country one
    assert not s.elsewhere and s.label() == "TARGET_COUNTRY"
    assert nairobi.match_place(text, s).status == "CONFIRMED"

def test_region_in_administrative_form_places_the_country(houston):
    for text in ("Houston Public Works, 611 Walker St, Houston, TX 77002.",
                 "Houston, Texas: the city treats surface water from three plants.",
                 "Rules of the State of Texas apply to public water systems."):
        s = houston.document_scope("https://www.houstonpublicworks.org/x", "Drinking water",
                                   [text])
        assert s.country == "TARGET" and s.region == "TARGET", text
        assert any("region" in r for r in s.reasons)
    bare = houston.document_scope("https://www.houstonpublicworks.org/x", "Drinking water",
                                  ["Texas weather was hot this year."])
    assert bare.country == "UNKNOWN"  # a bare region word is not administrative

def test_a_foreign_homonyms_region_never_makes_a_multi_country_report_elsewhere():
    """A Sub-Saharan groundwater report naming "Luanda (Angola)" and Bissau (the region of a
    Luanda homonym in Guinea-Bissau) is MIXED, not "another region"."""
    m = make("Luanda", "Angola", "AO", "Luanda Province", "AO-LUA", "province", homonyms=[
        {"name": "Luanda", "country": "Guinea-Bissau", "iso2": "GW", "region": "Bissau",
         "population": 1_500}])
    texts = ["Cities assessed: Nairobi (Kenya), Luanda (Angola), Lagos (Nigeria).",
             "Bissau and Dakar rely on aquifers.", "Bissau's wells are shallow."]
    s = m.document_scope("https://documents.worldbank.org/x", "Groundwater in Africa", texts)
    assert not s.elsewhere and s.label() == "MIXED"

# --- negative controls ----------------------------------------------------------------------

def test_a_homonyms_own_regional_host_is_elsewhere(vienna, houston):
    va = vienna.document_scope("https://www.viennava.gov/water", "Town water service",
                               ["The Town of Vienna maintains 170 miles of water mains."])
    assert va.elsewhere and va.label() == "OTHER_COUNTRY"
    assert vienna.match_place("The Town of Vienna water service", va).status == "HOMONYM"
    ms = houston.document_scope("https://houstonms.gov/water", "Public water", [""])
    assert ms.label() == "OTHER_REGION"
    state = houston.document_scope("https://msdh.ms.gov/page/30", "Public water systems", [""])
    assert state.label() == "OTHER_REGION"

def test_a_state_host_without_a_homonym_there_is_not_elsewhere(houston):
    """A Louisiana agency writing about a reservoir that supplies Houston is not 'another
    region' because of its host: no Houston homonym is listed in Louisiana."""
    la = houston.document_scope("https://www.dnr.louisiana.gov/x", "Toledo Bend",
                                ["Toledo Bend Reservoir releases water to Houston."])
    assert not la.elsewhere

def test_qualified_and_listed_homonyms_are_other_places(santiago):
    assert santiago.match_place("La catedral de Santiago de Compostela.").status == "HOMONYM"
    co = santiago.document_scope("https://www.ideam.gov.co/x", "Agua en Colombia",
                                 ["Santiago de Cali, Colombia, toma agua del río Cauca.",
                                  "En Colombia el IDEAM mide caudales."])
    assert co.elsewhere and co.label() == "OTHER_COUNTRY"

def test_a_document_only_about_another_country_is_elsewhere(nairobi):
    s = nairobi.document_scope("https://example.org/r", "Water in Uganda",
                               ["Uganda's utilities expanded. Uganda metered 90% of users."])
    assert s.elsewhere

def test_a_bare_name_on_a_homonym_countrys_commercial_host_is_uncertain(santiago):
    s = santiago.document_scope("https://www.periodico.com.mx/nota", "Agua potable", [
        "La red de Santiago abastece a la ciudad."])
    assert s.homonym_publisher and not s.elsewhere
    assert santiago.match_place("La red de Santiago abastece a la ciudad.",
                                s).status == "AMBIGUOUS"

def test_a_region_name_that_is_also_a_country_infers_nothing():
    atlanta = make("Atlanta", "United States of America", "US", "Georgia", "US-GA", "state")
    s = atlanta.document_scope("https://example.org/x", "Water", ["State of Georgia rules."])
    assert s.country == "UNKNOWN"

def test_a_bare_name_next_to_a_foreign_country_without_a_listed_homonym_is_ambiguous(nairobi):
    m = nairobi.match_place("Officials from Nairobi met in Uganda to discuss water.")
    assert m.status == "AMBIGUOUS" and "Uganda" in m.reason
    assert nairobi.match_place("Officials from Nairobi, Kenya met in Uganda.").status \
        == "CONFIRMED"

# --- section context ------------------------------------------------------------------------

PDF_PAGE = ("Annual Technical Report 2023\n\n"
            "3.2 Water supply of Nairobi, Kenya\n"
            "The main source is the Ndakaini Dam on the Thika River.\n"
            "It supplies 84 percent of the water distributed by the utility.\n")

def test_a_technical_report_sentence_takes_its_place_from_the_heading(nairobi):
    i = PDF_PAGE.index("It supplies")
    assert nairobi.match_place("It supplies 84 percent of the water.").status == "NONE"
    place = nairobi.section_place(PDF_PAGE, i)
    assert place is not None and place.status == "CONFIRMED" and "section heading" in place.reason

def test_a_multi_city_table_header_names_the_city(nairobi):
    table = ("Nairobi, Kenya  2019  2020  2021\n"
             "Production (m3/d)  500000  510000  525000\n")
    assert nairobi.section_place(table, table.index("Production")).status == "CONFIRMED"

def test_section_context_stops_at_another_citys_heading(nairobi):
    text = PDF_PAGE + "\n3.3 Water supply of Mombasa\nIt relies on the Mzima Springs.\n"
    assert nairobi.section_place(text, text.index("It relies")) is None

def test_section_context_never_crosses_a_page(nairobi):
    next_page = "It supplies 84 percent of the water distributed by the utility.\n"
    assert nairobi.section_place(next_page, 0) is None  # the heading was on the page before

def test_a_conflicting_place_inside_the_section_drops_the_context(nairobi):
    text = PDF_PAGE.replace("It supplies", "Kampala, Uganda also buys from it. It supplies")
    assert nairobi.section_place(text, text.index("It supplies")) is None

def test_a_homonym_heading_gives_no_context(vienna):
    text = "Water supply of Vienna, Virginia\nThe town buys water from Fairfax.\n"
    assert vienna.section_place(text, text.index("The town")) is None

def test_a_web_page_with_sections_for_several_cities(nairobi):
    text = ("Nairobi, Kenya\nThe city water company serves 4 million people.\n\n"
            "Kisumu\nThe lake supplies the town.\n")
    assert nairobi.section_place(text, text.index("The city water")).status == "CONFIRMED"
    assert nairobi.section_place(text, text.index("The lake")) is None

# --- the utility registry uses the section, not the whole multi-country document ------------

def test_registry_statement_placed_by_its_section_in_a_mixed_document(monkeypatch):
    from water_source_research.services import geo_match, utility_registry
    from water_source_research.services.field_state import initialize_city_state
    m = make("Nairobi", "Kenya", "KE", "Nairobi City County", "KE-30", "county")
    monkeypatch.setattr(geo_match, "matcher_for", lambda city, country: m)
    monkeypatch.setattr(utility_registry, "matcher_for", lambda city, country: m)
    text = ("Urban water in East Africa: Kenya, Uganda and Tanzania\n\n"
            "Kampala, Uganda\nThe National Water and Sewerage Corporation supplies drinking "
            "water to residents.\n\n"
            "Nairobi, Kenya\nThe Nairobi City Water and Sewerage Company supplies drinking "
            "water to residents and businesses.\n")
    chunk = EvidenceChunk(chunk_id="c1", url="https://www.example.org/report", text=text,
                          title="Urban water in East Africa", source_type="TECHNICAL")
    reg = utility_registry.build_registry(initialize_city_state("Nairobi", "Kenya"), [chunk])
    ncwsc = reg.find("Nairobi City Water and Sewerage Company")
    assert ncwsc is not None
    placed = [e for e in ncwsc.evidence if e.relationship == "SERVES"]
    assert placed and placed[0].geo_status == "CONFIRMED"
    nwsc = reg.find("National Water and Sewerage Corporation")
    assert nwsc is None or not any(e.relationship == "SERVES" and e.geo_status in (
        "CONFIRMED", "PROBABLE") for e in nwsc.evidence)


# --- feature location: the source's place is a third dimension -------------------------------

def test_a_source_located_abroad_does_not_make_the_city_uncertain(nairobi):
    text = "Nairobi draws water from the Sasumua dam in Uganda."  # synthetic geography
    assert nairobi.match_place(text).status != "AMBIGUOUS"
    assert [f.place for f in nairobi.feature_locations(text)] == ["Uganda"]
    # without the locating phrase, the same country next to the city stays uncertain
    assert nairobi.match_place("Officials from Nairobi met in Uganda.").status == "AMBIGUOUS"

def test_springs_in_another_region_and_a_reservoir_in_a_county(vienna, houston):
    s = vienna.document_scope("https://www.wien.gv.at/q", "Springs",
                              ["The springs in Styria supply Vienna."])
    assert not s.elsewhere
    assert [f.place for f in houston.feature_locations(
        "Lake Livingston in Polk County feeds Houston.")] == ["Polk County"]

def test_the_target_itself_is_never_masked_as_a_feature_location(houston):
    assert houston.feature_locations("the reservoirs in Houston, Texas") == []

def test_a_document_about_another_country_stays_elsewhere(nairobi):
    s = nairobi.document_scope("https://example.org/u", "Water in Uganda",
                               ["Uganda's utilities expanded. Uganda metered all users."])
    assert s.elsewhere  # possessive "Uganda's utilities" is not a water-feature location

def test_a_homonym_host_stays_a_homonym(vienna):
    va = vienna.document_scope("https://www.viennava.gov/water", "Town water",
                               ["The Town of Vienna buys water from springs in Fairfax."])
    assert va.elsewhere
