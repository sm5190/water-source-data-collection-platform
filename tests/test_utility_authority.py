"""Utility authority in acceptance (normalize-6 / resolve-5): a utility's website is an
official source only when the utility registry verified, from explicit statements, that it
serves the city in a role the field accepts (config/authority_roles.yaml). Unverified claims
are kept and go to review; government, regulatory and academic sources keep their rules."""

import pytest
from resolution_helpers import FIELDS, World, checks, claim
from water_source_research.schemas.common import FieldResolutionStatus as S
from water_source_research.schemas.common import ValidationOutcome
from water_source_research.services import geo_match
from water_source_research.services.field_state import initialize_city_state
from water_source_research.services.normalization import normalize_research
from water_source_research.services.resolution import resolve_research

W = "total_city_water_withdrawals_m3_day"

def run(world, city, country, *claims):
    s = initialize_city_state(city, country)
    for c in claims:
        s.fields[c.field_key].candidate_claims.append(c)
    normalized = normalize_research(s, FIELDS, world.index, current_year=2026)
    resolved, _ = resolve_research(normalized, FIELDS, world.index)
    return normalized, resolved

def first(state, key):
    return state.fields[key].candidate_claims[0]

def reasons(state, key):
    return [r for e in state.fields[key].resolution.entities for r in e.reason_codes]

ALMAR = "https://almarwater.com/water-infrastructure/"
WITHDRAWAL = "In 2025 Santiago withdrew 1,500,000 m3/d of raw water from the Maipo River."

def test_an_unverified_utility_page_alone_goes_to_review_and_is_kept():
    world = World()
    world.doc(ALMAR, "Almar Water Solutions designs and builds desalination plants in Chile. "
              + WITHDRAWAL)
    normalized, resolved = run(world, "Santiago", "Chile", claim(W, 1_500_000, (
        ALMAR, "In 2025 Santiago withdrew 1,500,000 m3/d")))
    c = first(normalized, W)
    assert c.qualifiers.authority == ["UNVERIFIED_UTILITY"]
    assert c.qualifiers.official_types == []
    assert checks(c)["UTILITY_RELATIONSHIP_UNVERIFIED"] == "WARN"
    assert c.validation.outcome is not ValidationOutcome.INVALID  # kept, never invalid
    assert resolved.fields[W].status is not S.RESOLVED
    assert "NO_OFFICIAL_SOURCE" in reasons(resolved, W)

def test_a_government_source_keeps_its_own_authority():
    world = World()
    world.doc(ALMAR, "Almar Water Solutions builds plants. " + WITHDRAWAL)
    gov = world.doc("https://www.siss.gob.cl/informe", WITHDRAWAL, source_type="GOVERNMENT")
    normalized, _ = run(world, "Santiago", "Chile", claim(W, 1_500_000, (
        ALMAR, "In 2025 Santiago withdrew 1,500,000 m3/d"), (
        gov, "In 2025 Santiago withdrew 1,500,000 m3/d")))
    c = first(normalized, W)
    assert set(c.qualifiers.authority) == {"UNVERIFIED_UTILITY", "GOVERNMENT"}
    assert c.qualifiers.official_types == ["GOVERNMENT"]
    assert "UTILITY_RELATIONSHIP_UNVERIFIED" not in checks(c)

def test_a_verified_operators_site_is_official():
    world = World()
    own = world.doc("https://www.aguasandinas.cl/reporte",
                    "Aguas Andinas supplies drinking water to Santiago, Chile. " + WITHDRAWAL)
    normalized, resolved = run(world, "Santiago", "Chile", claim(W, 1_500_000, (
        own, "In 2025 Santiago withdrew 1,500,000 m3/d")))
    c = first(normalized, W)
    assert c.qualifiers.authority == ["VERIFIED_SERVING_UTILITY"]
    assert c.qualifiers.official_types == ["UTILITY"]
    assert "NO_OFFICIAL_SOURCE" not in reasons(resolved, W)

def test_a_verified_utilitys_site_counts_even_when_classified_otherwise(identity_approved):
    world = World()
    hpw = world.doc("https://www.houstonpublicworks.org/water", "Houston Public Works supplies "
                    "drinking water to Houston, Texas. In 2024 Houston withdrew 1,700,000 m3/d.",
                    source_type="TECHNICAL")
    normalized, _ = run(world, "Houston", "United States of America", claim(W, 1_700_000, (
        hpw, "In 2024 Houston withdrew 1,700,000 m3/d")))
    assert first(normalized, W).qualifiers.official_types == ["UTILITY"]

def test_sanitation_only_and_contractor_hosts_are_never_potable_authority():
    world = World()
    wwtp = world.doc("https://www.biofiltrowater.cl/plantas", "Biofiltro Water Services treats "
                     "the wastewater of Santiago, Chile. " + WITHDRAWAL, source_type="UTILITY")
    normalized, _ = run(world, "Santiago", "Chile", claim(W, 1_500_000, (
        wwtp, "In 2025 Santiago withdrew 1,500,000 m3/d")))
    assert first(normalized, W).qualifiers.authority == ["UNVERIFIED_UTILITY"]

def test_a_wholesaler_is_authority_for_withdrawals_not_for_reported_population():
    world = World()
    bulk = world.doc("https://www.maipowater.cl/x", "Maipo Water Authority supplies raw water "
                     "in bulk to the utilities of Santiago, Chile. " + WITHDRAWAL + " In 2025 "
                     "Maipo Water Authority reported a population of 7,000,000 residents in "
                     "Santiago.", source_type="UTILITY")
    normalized, _ = run(world, "Santiago", "Chile",
                        claim(W, 1_500_000, (bulk, "In 2025 Santiago withdrew 1,500,000 m3/d")),
                        claim("utility_reported_city_population_millions", 7.0, (
                            bulk, "reported a population of 7,000,000 residents")))
    assert first(normalized, W).qualifiers.authority == ["VERIFIED_SERVING_UTILITY"]
    pop = first(normalized, "utility_reported_city_population_millions")
    assert pop.qualifiers.authority == ["UNVERIFIED_UTILITY"]
    assert checks(pop)["UTILITY_SERVICE_NOT_STATED"] == "WARN"

def test_a_historical_name_goes_to_review():
    world = World()
    gov = world.doc("https://www.siss.gob.cl/historia", "Aguas Andinas, formerly EMOS, supplies "
                    "drinking water to Santiago, Chile.", source_type="GOVERNMENT")
    normalized, _ = run(world, "Santiago", "Chile", claim("utility_name", "EMOS", (
        gov, "Aguas Andinas, formerly EMOS")))
    cs = checks(first(normalized, "utility_name"))
    assert cs["UTILITY_HISTORICAL_NAME"] == "WARN"
    assert cs["UTILITY_SERVICE_NOT_STATED"] == "WARN"

def test_a_utility_page_with_no_statement_is_not_circular_evidence():
    world = World()
    home = world.doc("https://www.aguasandinas.cl/", "Bienvenido a Aguas Andinas.",
                     source_type="UTILITY")
    normalized, resolved = run(world, "Santiago", "Chile", claim("utility_name",
                                                                 "Aguas Andinas", (
        home, "Bienvenido a Aguas Andinas")))
    c = first(normalized, "utility_name")
    assert checks(c)["UTILITY_SERVICE_NOT_STATED"] == "WARN"
    assert resolved.fields["utility_name"].status is not S.RESOLVED

@pytest.fixture
def identity_on(monkeypatch):
    monkeypatch.setenv("CITY_IDENTITY_ALLOW_DRAFT", "1")
    geo_match._matcher.cache_clear()
    yield
    geo_match._matcher.cache_clear()

def test_a_bare_city_name_on_the_own_site_needs_the_documents_country(identity_on):
    """With the approved identity the .cl host establishes the country, so the utility's own
    statement naming only "Santiago" is authoritative; without an identity it isn't (above)."""
    world = World()
    own = world.doc("https://www.aguasandinas.cl/nosotros", "Aguas Andinas supplies drinking "
                    "water to Santiago and its communes.", source_type="UTILITY")
    normalized, resolved = run(world, "Santiago", "Chile", claim("utility_name",
                                                                 "Aguas Andinas", (
        own, "Aguas Andinas supplies drinking water to Santiago")))
    assert checks(first(normalized, "utility_name"))["UTILITY_SERVES_CITY"] == "PASS"
    assert resolved.fields["utility_name"].status is S.RESOLVED

def test_claims_validated_before_normalize_6_read_their_source_types():
    from water_source_research.schemas.research import ClaimQualifiers
    from water_source_research.services.resolution import ResolutionPolicy, _official
    c = claim(W, 1, ("https://x.example", "q"))
    c.qualifiers = ClaimQualifiers(source_types=["UTILITY"])  # official_types None
    assert _official(c, FIELDS[W], ResolutionPolicy())
    c.qualifiers = ClaimQualifiers(source_types=["UTILITY"], official_types=[])
    assert not _official(c, FIELDS[W], ResolutionPolicy())

def test_without_an_approved_identity_a_bare_city_name_is_only_supporting(no_identity):
    """Production until the identity file is approved: the city's country can't be read from
    the .ca host, so "...drinking water to Toronto." is one supporting statement and the
    utility goes to review (with the country named, it verifies alone)."""
    world = World()
    bare = world.doc("https://www.toronto.ca/services/water", "Toronto Water provides drinking "
                     "water to Toronto.", source_type="GOVERNMENT")
    normalized, resolved = run(world, "Toronto", "Canada", claim("utility_name", "Toronto Water",
                                                                 (bare, ("Toronto Water provides "
                                                                        "drinking water"))))
    assert checks(first(normalized, "utility_name"))["UTILITY_SERVICE_NOT_STATED"] == "WARN"
    assert resolved.fields["utility_name"].status is not S.RESOLVED
