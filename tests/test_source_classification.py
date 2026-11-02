"""Source classification v2: who published a page (publisher_kind) apart from its tier,
news/directory/aggregator/commercial pages recognised before any utility rule, signals and
version recorded, and copies keeping the classification they were stored with. Cases are the
saved runs' misclassifications (Santiago, Houston, Luanda) plus counter-cases."""

import pytest
from discovery_helpers import web
from procedure_helpers import make_engine
from water_source_research.core.versions import CLASSIFICATION_VERSION
from water_source_research.discovery.normalize import classify, reclassified_columns
from water_source_research.discovery.ranking import merge_and_rank
from water_source_research.services.evidence_store import EvidenceRepository

@pytest.mark.parametrize(("url", "title", "snippet", "tier", "kind"), [
    # a news site whose headline names a "water utility" was UTILITY under classify-1
    ("https://www.riotimesonline.com/chile-may-sell-the-water-utility-stake/",
     "Chile may sell the water utility stake", None, "OTHER", "NEWS"),
    # a water company's own pages: commercial when its name or snippet says so
    ("https://almarwater.com/water-infrastructure/", ("Water Infrastructure - Almar Water "
     "Solutions"), None, "OTHER", "COMMERCIAL"),
    ("https://almarwater.com/new-water-innovation/", ("New water innovation to address water "
     "resource scarcity"), ("Almar Water Solutions develops desalination projects and "
     "solutions for industry."), "OTHER", "COMMERCIAL"),
    # directories, member pages and aggregators
    ("https://www.leadingutilities.org/members/houston-water", ("Houston Water | Leading "
     "Utilities of the World"), None, "OTHER", "DIRECTORY"),
    ("https://www.waterutility.ai/utilities/texas", "Texas water utilities", None, "OTHER",
     "DIRECTORY"),
    ("https://unglobalcompact.org.au/our-members/sydneywater/", ("Sydney Water - UN Global "
     "Compact Network Australia"), None, "OTHER", "DIRECTORY"),
    ("https://www.worldwater.org/water-conflict/", "Water Conflict Chronology", None, "OTHER",
     "DIRECTORY"),
    # counter-cases: real utilities stay utilities, government stays government
    ("https://www.thameswater.co.uk/", "Home", None, "UTILITY", "UTILITY_OPERATOR"),
    ("https://www.denverwater.org/", "Denver Water | About", None, "UTILITY",
     "UTILITY_OPERATOR"),
    ("https://www.aguasandinas.cl/", "Inicio", None, "UTILITY", "UTILITY_OPERATOR"),
    ("https://www.sydneywater.com.au/water-sources.html", "Water sources - Sydney Water",
     "Sydney Water supplies drinking water to five million people.", "UTILITY",
     "UTILITY_OPERATOR"),
    ("https://www.siss.gob.cl/586/w3-channel.html", ("Superintendencia de Servicios "
     "Sanitarios"), None, "GOVERNMENT", "GOVERNMENT"),
    ("https://www.ofwat.gov.uk/", "Ofwat regulator", None, "REGULATORY", "REGULATOR"),
    ("https://www.tapwaterdata.com/zip/60654", "Chicago tap water quality", None, "TECHNICAL",
     "INDEPENDENT_DATABASE"),
    ("https://www.eni.com/annual-report-2023.pdf", "Annual Report 2023", None, "OTHER",
     "COMMERCIAL"),
])
def test_classification_v2(url, title, snippet, tier, kind):
    c = classify(url, title, "searxng", snippet=snippet)
    assert (c.source_type, c.publisher_kind) == (tier, kind)
    assert c.version == CLASSIFICATION_VERSION

def test_a_company_page_named_like_a_utility_still_looks_like_one():
    """Known limit of classifying by title and URL: the utility registry (who serves the
    city, from evidence) and the authority rules decide what such a page can establish."""
    c = classify("https://almarwater.com/sustainability/", "Sustainability - Almar Water",
                 "searxng")
    assert c.publisher_kind == "UTILITY_OPERATOR"

def test_signals_say_why():
    c = classify("https://www.denverwater.org/", "Denver Water | About", "searxng")
    assert c.signals == ("named utility on its own host",)

def test_discovery_classifies_every_result_with_city_and_snippet():
    r = web("Water Infrastructure", "https://almarwater.com/a", 1,
            snippet="Almar Water Solutions builds desalination projects.",
            source_type="UTILITY")
    toronto = web("Water", "https://www.toronto.ca/water", 2)
    out = {x.url: x for x in merge_and_rank([r, toronto], "Toronto")}
    almar = out["https://almarwater.com/a"]
    assert (almar.source_type, almar.publisher_kind) == ("OTHER", "COMMERCIAL")
    assert almar.classification_version == CLASSIFICATION_VERSION
    city = out["https://www.toronto.ca/water"]
    assert (city.source_type, city.publisher_kind) == ("GOVERNMENT", "GOVERNMENT")

def test_a_copy_keeps_its_original_classification():
    stored = {"final_url": "https://www.riotimesonline.com/x", "title": "Water utility stake",
              "source_type": "UTILITY", "publisher_kind": None, "classification_version": None}
    cols = reclassified_columns(stored, "Santiago")
    assert (cols["source_type"], cols["publisher_kind"]) == ("OTHER", "NEWS")
    assert cols["classification_signals"]["original"] == {
        "source_type": "UTILITY", "publisher_kind": None, "classification_version": "classify-1"}

def test_documents_and_hits_store_the_classification(tmp_path):
    repo = EvidenceRepository(make_engine(tmp_path, "cls.db"))
    rec = {"round": 1, "requested_url": "https://almarwater.com/a",
           "canonical_url": "https://almarwater.com/a", "final_url": "https://almarwater.com/a",
           "title": "t", "source_type": "OTHER", "publisher_kind": "COMMERCIAL",
           "classification_version": CLASSIFICATION_VERSION,
           "classification_signals": {"signals": ["commercial water company"]},
           "status": "FAILED", "chunks": 0}
    repo.save_documents("run-c", [rec], [])
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from water_source_research.db.evidence_models import SourceDocumentRecord
    with Session(repo.engine) as s:
        row = s.scalars(select(SourceDocumentRecord)).one()
        assert (row.publisher_kind, row.classification_version) == ("COMMERCIAL",
                                                                    CLASSIFICATION_VERSION)
        assert row.classification_signals == {"signals": ["commercial water company"]}
    repo.save_discovery_results("run-c", 1, [{
        "call_index": 0, "result_index": 0, "provider": "searxng", "strategy": "web_search",
        "url": "https://almarwater.com/a", "publisher_kind": "COMMERCIAL",
        "classification_version": CLASSIFICATION_VERSION}])
    from water_source_research.db.evidence_models import DiscoveryResultRecord
    with Session(repo.engine) as s:
        hit = s.scalars(select(DiscoveryResultRecord)).one()
        assert hit.publisher_kind == "COMMERCIAL"
