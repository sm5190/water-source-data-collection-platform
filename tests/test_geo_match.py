"""Geographic matching on the City + Region + Country identity: homonyms and qualified forms,
document scope from the host and the text, metropolitan names as context, and the name-only
fallback when no identity is approved."""

import pytest
from water_source_research.services.city_identity import (
    CityIdentity, CountryInfo, Homonym, NameSource, Region, load_city_identities,
)
from water_source_research.services.geo_match import (
    DocumentScope, GeoMatcher, identity_signature, matcher_for,
)

BY = {(i.city, i.country): i for i in load_city_identities()}
GN = NameSource(source="geonames", id="1", property="cities500")

@pytest.fixture(scope="module")
def santiago():
    return GeoMatcher(BY[("Santiago", "Chile")])

@pytest.fixture(scope="module")
def houston():
    return GeoMatcher(BY[("Houston", "United States of America")])

@pytest.fixture(scope="module")
def vienna():
    return GeoMatcher(BY[("Vienna", "Austria")])

def scope(m: GeoMatcher, url: str, title: str = "", text: str = "") -> DocumentScope:
    return m.document_scope(url, title, [text])

# --- Santiago, Chile ------------------------------------------------------------------------

def test_qualified_homonyms_are_other_places(santiago):
    for text in ("Agua potable en Santiago de Cali, Valle del Cauca.",
                 "El acueducto de Santiago de Querétaro abastece la zona metropolitana.",
                 "Santiago del Estero recibe agua del río Dulce."):
        assert santiago.match_place(text).status == "HOMONYM", text

def test_the_citys_own_qualified_name_is_confirmed(santiago):
    assert santiago.match_place("Aguas para Santiago de Chile y sus comunas.").status \
        == "CONFIRMED"

def test_a_bare_name_needs_the_region_country_or_document_scope(santiago):
    chile = santiago.match_place("La red de Santiago, Chile, abastece a 7 millones.")
    assert chile.status == "CONFIRMED" and "Chile" in chile.reason
    assert santiago.match_place("La red de Santiago abastece a 7 millones.").status \
        == "AMBIGUOUS"  # unknown scope and large homonyms (Querétaro, Cuba, ...)
    cl = scope(santiago, "https://www.siss.gob.cl/informe")
    assert cl.label() == "TARGET_COUNTRY"
    assert santiago.match_place("La red de Santiago abastece.", cl).status == "PROBABLE"

def test_a_colombian_government_document_is_not_about_santiago_chile(santiago):
    co = scope(santiago, "https://www.minvivienda.gov.co/informe.pdf",
               text="Informe nacional de calidad del agua. Santiago, municipio de Colombia.")
    assert co.label() == "OTHER_COUNTRY"
    assert santiago.match_place("Santiago registró IRCA bajo.", co).status == "HOMONYM"
    mx = scope(santiago, "https://sinav30.conagua.gob.mx/x")
    assert mx.label() == "OTHER_COUNTRY"

def test_text_decides_the_scope_of_a_generic_host(santiago):
    colombia = scope(santiago, "https://example.org/report", "Agua en Colombia",
                     "Colombia ... Colombia ... Bogotá, Colombia")
    assert colombia.label() == "OTHER_COUNTRY"
    chile = scope(santiago, "https://www.riotimesonline.com/x", "Water cuts",
                  "Aguas Andinas cut water in Santiago communes, Chile.")
    assert chile.label() == "TARGET_COUNTRY"

def test_neutralize_blanks_homonyms_and_keeps_length_and_water_bodies(santiago):
    text = "Santiago de Cali takes water from the Cauca River."
    n = santiago.neutralize(text)
    assert len(n.text) == len(text)
    assert "Santiago" not in n.text and "Cauca River" in n.text

def test_the_region_name_alone_is_not_the_city(santiago):
    assert santiago.match_place("Región Metropolitana water rights").status == "NONE"

# --- Cali, Colombia: the reverse direction ------------------------------------------------

def test_santiago_de_cali_is_cali():
    cali = GeoMatcher(BY[("Cali", "Colombia")])
    assert cali.match_place("Acueducto de Santiago de Cali").status == "CONFIRMED"

# --- Hyderabad: India and Pakistan --------------------------------------------------------

def test_hyderabad_india_vs_sindh():
    india = GeoMatcher(BY[("Hyderabad", "India")])
    assert india.match_place("Water supply in Hyderabad, Telangana.").status == "CONFIRMED"
    assert india.match_place("Water supply in Hyderabad, Sindh, from the Indus.").status \
        == "HOMONYM"
    pakistan = GeoMatcher(CityIdentity(
        city="Hyderabad", country="Pakistan", status="APPROVED",
        region=Region(name="Sindh", verified=True),
        country_info=CountryInfo(name="Pakistan", iso2="PK", cctld="pk"),
        homonyms=[Homonym(name="Hyderabad", country="India", iso2="IN", region="Telangana",
                          population=3_597_816, sources=[GN])]))
    assert pakistan.match_place("Hyderabad, Sindh draws on the Indus.").status == "CONFIRMED"
    assert pakistan.match_place("Hyderabad, Telangana: Osman Sagar reservoir.").status \
        == "HOMONYM"
    assert pakistan.match_place("Hyderabad Metropolitan Water Supply, India").status \
        == "HOMONYM"

# --- Houston, Texas -----------------------------------------------------------------------

def test_houston_mississippi_state_site_is_another_region(houston):
    ms = scope(houston, "https://msdh.ms.gov/page/30,0,76.html", "Public water systems")
    assert ms.label() == "OTHER_REGION"
    assert houston.match_place("Houston water system, Chickasaw County.", ms).status \
        == "HOMONYM"
    tx = scope(houston, "https://www.twdb.texas.gov/surfacewater/rivers/reservoirs/houston/")
    assert tx.region == "TARGET" and tx.country == "TARGET"
    assert houston.match_place("Houston, Mississippi water").status == "HOMONYM"

def test_lake_houston_is_not_houston(houston):
    assert houston.match_place("Lake Houston is on the San Jacinto River.").status == "NONE"

def test_metro_names_are_context_and_counties_are_not_the_city(houston):
    m = houston.match_place("Water demand in Greater Houston keeps growing.")
    assert m.status == "PROBABLE" and "metropolitan" in m.reason
    assert houston.match_place("Harris County water districts").status == "NONE"

def test_a_bare_houston_without_large_homonyms_is_probable(houston):
    assert houston.match_place("Houston Public Works treats surface water.").status \
        == "PROBABLE"

# --- Vienna, Austria ------------------------------------------------------------------------

def test_vienna_virginia_is_a_homonym(vienna):
    va = scope(vienna, "https://www.viennava.gov/water")
    assert va.label() == "OTHER_COUNTRY"
    assert vienna.match_place("The Town of Vienna water service", va).status == "HOMONYM"
    assert vienna.match_place("Vienna, Virginia water").status == "HOMONYM"
    assert vienna.match_place("Wiener Wasser: Wien bezieht Quellwasser.").status == "CONFIRMED"
    at = scope(vienna, "https://www.wien.gv.at/wienwasser/")
    assert at.label() == "TARGET_COUNTRY"

# --- no approved identity: name-only behaviour --------------------------------------------

def test_no_matcher_without_an_approved_identity(monkeypatch):
    monkeypatch.delenv("CITY_IDENTITY_ALLOW_DRAFT", raising=False)
    assert matcher_for("Santiago", "Chile") is None  # the file is still a draft
    assert identity_signature("Santiago", "Chile") == "catalog-only"
    monkeypatch.setenv("CITY_IDENTITY_ALLOW_DRAFT", "1")
    assert matcher_for("Santiago", "Chile") is not None
    assert identity_signature("Santiago", "Chile") != "catalog-only"
