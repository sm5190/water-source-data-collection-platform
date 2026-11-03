"""The utility registry: role-aware, evidence-based city-to-utility relationships. Utility
names below are test expectations only (synthetic sentences modelled on the saved runs); the
registry has no list of utilities."""

import pytest
from water_source_research.schemas.research import EvidenceChunk, EvidenceRef, FieldClaim
from water_source_research.services import geo_match
from water_source_research.services.field_state import initialize_city_state
from water_source_research.services.utility_registry import build_registry, utility_key

@pytest.fixture(autouse=True)
def identity_on(monkeypatch):
    monkeypatch.setenv("CITY_IDENTITY_ALLOW_DRAFT", "1")
    geo_match._matcher.cache_clear()
    yield
    geo_match._matcher.cache_clear()

def chunk(url, text, source_type="OTHER", title=None, cid=None):
    return EvidenceChunk(chunk_id=cid or str(abs(hash((url, text))) % 10**8), url=url,
                         text=text, title=title or "", source_type=source_type)

def registry(chunks, city="Santiago", country="Chile", claims=()):
    s = initialize_city_state(city, country)
    for v in claims:
        s.fields["utility_name"].candidate_claims.append(FieldClaim(
            field_key="utility_name", value=v, confidence=0.8,
            evidence=[EvidenceRef(url="https://x.example", passage=v)]))
    return build_registry(s, chunks)

def status(reg, name):
    c = reg.find(name)
    return c.status if c else None

OWN = chunk("https://www.aguasandinas.cl/nosotros",
            "Aguas Andinas supplies drinking water to Santiago, Chile, and its communes.",
            source_type="UTILITY", title="Aguas Andinas | Nosotros")
ALMAR = [
    chunk("https://almarwater.com/news-centinela/",
          "Almar Water Solutions and Transelec receive the transfer of Centinela's first water "
          "pipeline at an event in Santiago.", source_type="UTILITY",
          title="Almar Water Solutions"),
    chunk("https://almarwater.com/water-infrastructure/",
          "Almar Water Solutions designs, builds and operates desalination plants for mining "
          "and industry in Chile.", source_type="UTILITY", title="Almar Water Solutions"),
]

# --- misleading official websites and circularity ------------------------------------------

def test_a_commercial_water_company_is_not_the_citys_utility():
    reg = registry(ALMAR)
    assert status(reg, "Almar Water Solutions") in ("NON_SERVING", "CANDIDATE")
    assert reg.verified_hosts() == []

def test_a_utility_classified_page_without_a_statement_is_not_verified():
    home = chunk("https://www.aguasandinas.cl/", "Bienvenido a Aguas Andinas. Paga tu cuenta.",
                 source_type="UTILITY", title="Aguas Andinas")
    reg = registry([home])
    cand = reg.find("Aguas Andinas")
    assert cand.status == "CANDIDATE" and cand.bound_hosts == ["aguasandinas.cl"]
    assert reg.verified_hosts() == []

def test_the_utilitys_own_explicit_statement_verifies_it():
    reg = registry([OWN, *ALMAR])
    cand = reg.find("Aguas Andinas")
    assert cand.status == "VERIFIED" and "DRINKING_WATER_OPERATOR" in cand.verified_roles
    assert "own site" in cand.status_reason
    assert reg.verified_hosts() == ["aguasandinas.cl"]

# --- verification strength --------------------------------------------------------------

NEWS = chunk("https://www.riotimesonline.com/aguas-andinas/",
             "Aguas Andinas provides drinking water in Santiago. Chile's regulator fined it.",
             title="Water cuts in Chile")

def test_one_supporting_news_statement_does_not_verify():
    reg = registry([NEWS])
    cand = reg.find("Aguas Andinas")
    assert cand.status == "CANDIDATE"
    assert [e.strength for e in cand.evidence if e.relationship == "SERVES"] == ["SUPPORTING"]

def test_two_independent_supporting_statements_verify():
    other = chunk("https://www.biobiochile.cl/noticias/1", "Aguas Andinas provides drinking "
                  "water to most Santiago households.")
    reg = registry([NEWS, other])
    assert status(reg, "Aguas Andinas") == "VERIFIED"
    assert "two independent statements" in reg.find("Aguas Andinas").status_reason

def test_a_government_statement_with_the_country_verifies_alone():
    gov = chunk("https://www.siss.gob.cl/586/w3-article.html", "Aguas Andinas presta el "
                "servicio de agua potable en Santiago, Chile.", source_type="GOVERNMENT")
    assert status(registry([gov]), "Aguas Andinas") == "VERIFIED"

def test_ambiguous_geography_never_verifies():
    a = chunk("https://example.com/a", "Aguas Andinas supplies drinking water to Santiago.")
    b = chunk("https://example.net/b", "Aguas Andinas provides drinking water in Santiago.")
    reg = registry([a, b])
    assert status(reg, "Aguas Andinas") == "AMBIGUOUS_GEOGRAPHY"

# --- roles --------------------------------------------------------------------------------

def test_sanitation_only_is_not_a_drinking_water_utility():
    gov = chunk("https://www.siss.gob.cl/x", "Biofiltro Water Services treats the wastewater "
                "of Santiago, Chile.", source_type="GOVERNMENT")
    reg = registry([gov])
    assert status(reg, "Biofiltro Water Services") == "SANITATION_ONLY"
    assert reg.verified_hosts() == [] and reg.verified() == []

def test_water_and_sanitation_services_are_the_drinking_water_role():
    gov = chunk("https://www.siss.gob.cl/y", "Aguas Andinas provides water and sanitation "
                "services to Santiago, Chile.", source_type="GOVERNMENT")
    assert registry([gov]).find("Aguas Andinas").verified_roles == ["DRINKING_WATER_OPERATOR"]

def test_a_bulk_supplier_is_a_wholesaler():
    gov = chunk("https://www.dga.mop.gob.cl/z", "Maipo Water Authority supplies raw water in "
                "bulk to the utilities of Santiago, Chile.", source_type="GOVERNMENT")
    cand = registry([gov]).find("Maipo Water Authority")
    assert cand.status == "VERIFIED" and cand.verified_roles == ["WHOLESALE_SUPPLIER"]

# --- several utilities, historical names --------------------------------------------------

def test_several_utilities_serving_parts_of_the_city_are_all_kept():
    gov = chunk("https://www.siss.gob.cl/m", "Servicio Municipal de Agua Potable y "
                "Alcantarillado (SMAPA) supplies drinking water to the commune of Maipú in "
                "Santiago, Chile.", source_type="GOVERNMENT")
    reg = registry([OWN, gov])
    smapa = reg.find("SMAPA")
    assert smapa is not None and smapa.status == "VERIFIED"
    assert any(e.relationship == "SERVES_PART" for e in smapa.evidence)
    assert {c.key for c in reg.verified()} >= {utility_key("Aguas Andinas"), smapa.key}

def test_a_predecessor_is_historical_and_not_merged():
    gov = chunk("https://www.siss.gob.cl/h", "Aguas Andinas, formerly EMOS, supplies drinking "
                "water to Santiago, Chile.", source_type="GOVERNMENT")
    reg = registry([gov])
    current, emos = reg.find("Aguas Andinas"), reg.find("EMOS")
    assert current.status == "VERIFIED" and emos.status == "HISTORICAL"
    assert current.key in emos.predecessor_of and emos.key in current.successor_of
    assert "EMOS" not in current.aliases and emos.verified_roles == []

def test_now_inside_a_sentence_is_not_a_renaming():
    gov = chunk("https://www.siss.gob.cl/n", "Aguas Andinas now operates the Maipo Water "
                "Works (MWW) for Santiago, Chile.", source_type="GOVERNMENT")
    reg = registry([gov])
    for name in ("Aguas Andinas", "MWW", "Maipo Water Works"):
        cand = reg.find(name)
        assert cand is None or not (cand.predecessor_of or cand.successor_of), name

# --- Houston ------------------------------------------------------------------------------

H = ("Houston", "United States of America")

def test_houston_department_verified_by_the_city_site():
    gov = chunk("https://www.houstontx.gov/water/", "Houston Public Works provides drinking "
                "water to the City of Houston, Texas.", source_type="GOVERNMENT")
    reg = registry([gov], *H)
    assert status(reg, "Houston Public Works") == "VERIFIED"

def test_a_directory_page_and_another_houston_do_not_verify():
    directory = chunk("https://www.leadingutilities.org/members/houston-water",
                      "Houston Water. Population served: 2,100,000.", source_type="OTHER",
                      title="Houston Water | Leading Utilities of the World")
    mississippi = chunk("https://msdh.ms.gov/page/30,0,76.html",
                        "Houston Water Works supplies drinking water to Houston, Chickasaw "
                        "County.", source_type="GOVERNMENT",
                        title="Mississippi public water systems")
    reg = registry([directory, mississippi], *H)
    assert reg.verified() == []
    assert status(reg, "Houston Water Works") != "VERIFIED"

def test_claim_names_are_kept_even_without_evidence():
    reg = registry([], *H, claims=["Houston Water"])
    assert status(reg, "Houston Water") == "CANDIDATE"

# --- the graph hook: state, events, persistence --------------------------------------------

def test_refresh_records_status_changes_once_and_persists(tmp_path):
    import asyncio
    from procedure_helpers import make_engine
    from water_source_research.agents.nodes import refresh_utility_registry
    from water_source_research.services.evidence_store import EvidenceRepository
    store = EvidenceRepository(make_engine(tmp_path, "reg.db"))
    s = initialize_city_state("Santiago", "Chile")
    state = {"run_id": "run-r", "research_state": s.model_dump(mode="json"),
             "evidence_chunks": [NEWS.model_dump(mode="json")], "events": []}
    first = asyncio.run(refresh_utility_registry(state, store, 2, "discover"))
    kinds = [(e["kind"], e["attrs"]["utility"], e["attrs"]["to_status"])
             for e in first["events"]]
    assert ("UTILITY_STATUS", "Aguas Andinas", "CANDIDATE") in kinds
    state = {**state, **first, "evidence_chunks": [NEWS.model_dump(mode="json"),
                                                   OWN.model_dump(mode="json")]}
    second = asyncio.run(refresh_utility_registry(state, store, 3, "discover"))
    new = second["events"][len(first["events"]):]
    assert [(e["attrs"]["from_status"], e["attrs"]["to_status"]) for e in new
            if e["attrs"]["utility"] == "Aguas Andinas"] == [("CANDIDATE", "VERIFIED")]
    again = asyncio.run(refresh_utility_registry({**state, **second}, store, 3, "discover"))
    assert len(again["events"]) == len(second["events"])  # nothing changed, nothing recorded
    rows = store.utility_candidates("run-r")
    assert {(r["round"], r["status"]) for r in rows if r["name"] == "Aguas Andinas"} == {
        (2, "CANDIDATE"), (3, "VERIFIED")}
    assert all(r["identity"] != "catalog-only" for r in rows)

# --- precision on real stored text (Nairobi, Santiago, Houston runs) -----------------------

def test_water_descriptions_are_not_organisations():
    """'Surface Water' and 'Waste Water' were VERIFIED on the stored Nairobi run, through a
    'water.' subdomain: descriptions are not names, a generic host label binds nothing."""
    igad = chunk("https://water.igad.int/resources/x.pdf",
                 "Surface Water from the Tana Basin supplies drinking water to Nairobi, Kenya. "
                 "Waste Water is treated at Dandora for Nairobi, Kenya.", source_type="TECHNICAL")
    reg = registry([igad], "Nairobi", "Kenya")
    assert reg.find("Surface Water") is None and reg.find("Waste Water") is None
    assert reg.verified_hosts() == []

def test_headlines_and_fragments_are_not_names():
    news = chunk("https://www.riotimesonline.com/x", "About Houston Water and Africa's Water.",
                 title="Aguas Andinas Cuts Water in 4 Santiago Areas | The Rio Times")
    reg = registry([news])
    assert reg.find("Aguas Andinas Cuts Water") is None
    assert reg.find("Africa's Water") is None

def test_first_person_statements_on_the_utilitys_own_site_count():
    home = chunk("https://nairobiwater.co.ke/", "Welcome.", source_type="UTILITY",
                 title="Home - Nairobi Water Company", cid="h")
    supply = chunk("https://nairobiwater.co.ke/know-your-supply-day/", "We supply drinking "
                   "water to the different areas of Nairobi, Kenya.", source_type="UTILITY",
                   title="Know your Supply Day - Nairobi Water Company", cid="s")
    reg = registry([home, supply], "Nairobi", "Kenya")
    cand = reg.find("Nairobi Water Company")
    assert cand.status == "VERIFIED" and cand.bound_hosts == ["nairobiwater.co.ke"]
    assert any("first-person" in e.geo_reason for e in cand.evidence)

def test_a_statement_without_a_place_takes_the_documents_place():
    wqr = chunk("https://houstonpublicworks.org/wqr.pdf", "Houston Water Quality Report 2025 "
                "for Houston, Texas, United States. Houston Public Works produces and "
                "distributes drinking water through 7,000 miles of pipeline.",
                source_type="UTILITY", title="Houston Water Quality Report")
    reg = registry([wqr], *H)
    ev = [e for e in reg.find("Houston Public Works").evidence if e.relationship == "SERVES"]
    assert ev and ev[0].geo_status in ("CONFIRMED", "PROBABLE")

def test_a_utility_that_invests_or_contracts_is_not_a_contractor():
    """Seen on a fresh Santiago run: World Bank text on a plant built under a BOT
    arrangement with the city's utility made the utility NON_SERVING."""
    wb = chunk("https://documents1.worldbank.org/x.pdf", "The plant was implemented through a "
               "build-operate-transfer (BOT) arrangement between Aguas Andinas and Degremont. "
               "Aguas Andinas finances its part with corporate bonds and recovers its "
               "investment.", source_type="TECHNICAL")
    assert status(registry([wb]), "Aguas Andinas") in (None, "CANDIDATE")
    assert status(registry(ALMAR), "Almar Water Solutions") == "NON_SERVING"

def test_fresh_run_false_verifications_stay_unverified():
    """Seen on the fresh Houston and Nairobi runs (Milestone 3 new runs)."""
    nfbwa = chunk("https://www.nfbwa.com/our-projects", "Water is pumped several miles across "
                  "the City of Houston into Fort Bend County. We are currently planning the "
                  "construction of 30 miles of water lines to distribute water to additional "
                  "customers.", source_type="UTILITY",
                  title="Our Projects | North Fort Bend Water Authority (NFBWA)")
    reg = registry([nfbwa], *H)
    assert reg.verified() == [] and reg.verified_hosts() == []
    assembly = chunk("https://nairobiassembly.go.ke/report.pdf", "Nairobi City Water & "
                     "Sewerage Company (NCWSC) is mandated to provide clean water and sewerage "
                     "services within Nairobi, Kenya. Non-Revenue Water reduction improves water "
                     "supply reliability in Nairobi, Kenya.", source_type="GOVERNMENT")
    reg = registry([assembly], "Nairobi", "Kenya")
    assert reg.find("Non-Revenue Water") is None
    ncw = reg.find("Nairobi City Water & Sewerage Company")
    assert ncw is not None and ncw.status == "VERIFIED"  # the Assembly states its mandate
    assert "nairobiassembly.go.ke" not in ncw.bound_hosts  # but it isn't the utility's site

# --- Milestone 3.1 Phase 1: diagnosed rule bugs (F3, F4, F5) --------------------------------

def test_a_utility_that_commissions_design_is_the_client_not_a_contractor():
    """F4, seen in Santiago run b844390c: the noun "diseño" read as a building verb made the
    city's utility NON_SERVING (and stopped its verification searches)."""
    gov = chunk("https://planesynormas.mma.gob.cl/informe.pdf", "Aguas Andinas encargó a la "
                "empresa Hidrosan el diseño de la planta de tratamiento.",
                source_type="GOVERNMENT")
    assert status(registry([gov]), "Aguas Andinas") in (None, "CANDIDATE")
    contracted = chunk("https://x.example/news", "Aguas Andinas contracted Hidrosan Ingeniería "
                       "to design and build the new plant.", source_type="OTHER")
    assert status(registry([contracted]), "Aguas Andinas") in (None, "CANDIDATE")
    # the contractor itself, doing the work, is still a contractor
    builder = chunk("https://hidrosan.example/proyectos", "Hidrosan Water Services diseñó y "
                    "construyó la planta para la empresa sanitaria.", source_type="COMMERCIAL")
    cand = registry([builder]).find("Hidrosan Water Services")
    assert cand is not None and any(e.role == "CONTRACTOR" for e in cand.evidence)

def test_untreated_surface_water_to_cities_is_wholesale():
    """F3: "provides untreated surface water to the cities of ..." is bulk supply, not a
    drinking-water operator."""
    cwa = chunk("https://www.coastalwaterauthority.org/about", "The Coastal Water Authority "
                "provides untreated surface water to the cities of Houston, Baytown, and Deer "
                "Park, Texas, United States.", source_type="GOVERNMENT",
                title="About | Coastal Water Authority")
    cand = registry([cwa], *H).find("Coastal Water Authority")
    roles = {e.role for e in cand.evidence if e.relationship != "MENTION_ONLY"}
    assert roles == {"WHOLESALE_SUPPLIER"}
    assert "DRINKING_WATER_OPERATOR" not in cand.verified_roles

def test_descriptor_names_are_not_organisations_on_any_path():
    """F5: "Non-Revenue Water (NRW)" (acronym path, verified in Nairobi run 21b46e42) and a
    page heading "<City> Drinking Water Operations" are descriptions, not utilities."""
    nrw = chunk("https://nairobiassembly.go.ke/r.pdf", "Non-Revenue Water (NRW) reduction "
                "improves water supply in Nairobi, Kenya, where the company provides water "
                "services.", source_type="GOVERNMENT")
    assert registry([nrw], "Nairobi", "Kenya").find("Non-Revenue Water") is None
    ops = chunk("https://houstonpublicworks.org/drinking-water-operations", "Houston Drinking "
                "Water Operations supplies high quality drinking water to Houston, Texas.",
                source_type="OTHER", title="Houston Drinking Water Operations")
    assert registry([ops], *H).find("Houston Drinking Water Operations") is None
    # a name with its own acronym that names a body is still an organisation
    smapa = chunk("https://x.example/a", "El Servicio Municipal de Agua Potable y "
                  "Alcantarillado (SMAPA) abastece de agua potable a Maipú, Santiago, Chile.",
                  source_type="GOVERNMENT")
    assert registry([smapa]).find("SMAPA") is not None
    # "<City> Water" stays a name
    tw = chunk("https://www.toronto.ca/water", "Toronto Water supplies drinking water to "
               "Toronto, Ontario, Canada.", source_type="GOVERNMENT")
    assert registry([tw], "Toronto", "Canada").find("Toronto Water") is not None

def test_a_plant_name_fragment_is_not_an_organisation():
    """'East Water' from "the City's East Water Purification Plant" (a stored Houston run):
    a plant, not a supplier. Organisation words still make organisations."""
    text = ("The Lake Valley facilities transport water to the City's East Water Purification "
            "Plant and provide untreated water to industries. North Water Pumping Station "
            "lifts it. The Riverside Public Works department and the Riverside Waterworks "
            "Department supply drinking water to Riverside.")
    reg = registry([chunk("https://www.example.org/x", text)], city="Houston",
                   country="United States of America")
    names = {c.name for c in reg.candidates}
    assert not any("East Water" == n or "North Water" == n for n in names)
