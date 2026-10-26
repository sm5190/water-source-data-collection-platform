"""Discovery aimed at authoritative utility documents: source-oriented topics (not
"reservoirs"), site-restricted queries on the city's own utility host, local-language topics,
utility hosts first in acquisition, and classification of directories and non-water corporate
reports. Cases come from saved runs' misclassifications plus generic counter-cases."""

import pytest
from water_source_research.acquisition.config import AcquisitionSettings
from water_source_research.acquisition.service import select_candidates
from water_source_research.agents.nodes import utility_hosts
from water_source_research.discovery.normalize import classify_source_type
from water_source_research.discovery.queries import build_queries
from water_source_research.services.field_state import load_field_definitions

FIELDS = load_field_definitions()

def queries(city, country, keys, **kw):
    return [q.query for q in build_queries(city=city, country=country, utility=None,
                                           field_keys=keys, fields=FIELDS, max_per_kind=12,
                                           **kw) if q.kind == "web"]

def test_water_source_topic_asks_for_sources_not_reservoirs():
    qs = queries("Luanda", "Angola", ["water_source"])
    assert not any("reservoir" in q.casefold() for q in qs)
    assert any("rivers springs groundwater" in q for q in qs)

def test_local_language_topic_only_where_the_country_has_one():
    assert any("abastecimento de água" in q for q in queries("Luanda", "Angola",
                                                           ["water_source"]))
    assert any("Wasserversorgung" in q for q in queries("Vienna", "Austria", ["water_source"]))
    english = queries("Toronto", "Canada", ["water_source"])
    assert len(english) == 1  # no local-language query for an English-speaking country

def test_site_query_on_the_known_utility_host():
    qs = queries("Nairobi", "Kenya", ["water_source", "utility_name"],
                 utility_hosts=["nairobiwater.co.ke"])
    assert "site:nairobiwater.co.ke water sources" in qs
    # only for fields that live on utility websites
    assert not any(q.startswith("site:") and "utility" in q for q in qs)

def test_utility_hosts_come_from_acquired_utility_documents():
    state = {"acquired_documents": [
        {"status": "OK", "source_type": "UTILITY", "final_url": "https://nairobiwater.co.ke/a"},
        {"status": "OK", "source_type": "UTILITY", "final_url": "https://nairobiwater.co.ke/b"},
        {"status": "FAILED", "source_type": "UTILITY", "final_url": "https://down.example/"},
        {"status": "OK", "source_type": "GOVERNMENT", "final_url": "https://wra.go.ke/x"}]}
    assert utility_hosts(state) == ["nairobiwater.co.ke"]

def test_acquisition_takes_the_utility_host_first():
    candidates = [{"url": "https://gov.example/a", "source_type": "GOVERNMENT"},
                  {"url": "https://www.nairobiwater.co.ke/sources", "source_type": "OTHER"},
                  {"url": "https://uni.example/p", "source_type": "ACADEMIC"}]
    chosen = select_candidates(candidates, set(), AcquisitionSettings(), 2,
                               priority_hosts=["nairobiwater.co.ke"])
    assert chosen[0]["url"] == "https://www.nairobiwater.co.ke/sources"

@pytest.mark.parametrize("url, title, expected", [
    # seen in saved runs, wrongly counted as utilities or technical sources
    ("https://www.worldwater.org/conflict/list/", "Water Conflict Chronology", "OTHER"),
    ("https://www.leadingutilities.org/members/vienna-water",
     "Vienna Water | Leading Utilities of the World", "OTHER"),
    ("https://www.waterutility.ai/utilities/texas", "Texas water utilities", "OTHER"),
    ("https://www.eni.com/content/dam/enicom/documents/eng/reports/2025/Annual-Report-2025.pdf",
     "Annual Report 2025", "OTHER"),
    ("https://www.verbund.com/media/x/verbund-annual-financial-report-2025.pdf", "", "OTHER"),
    # counter-cases: unchanged
    ("https://nairobiwater.co.ke/reservoirs-dams/", "Reservoirs/Dams - Nairobi Water Company",
     "UTILITY"),
    ("https://www.sydneywater.com.au/about-us/annual-report.html", "Annual report | Sydney Water",
     "UTILITY"),
    ("https://documents1.worldbank.org/curated/en/x/pdf/Assessment-of-groundwater.pdf",
     "Assessment of groundwater challenges", "TECHNICAL"),
    ("https://www.citywater.example/annual-report-2024.pdf", "City Water annual report 2024",
     "UTILITY"),
])
def test_classification_of_directories_and_corporate_reports(url, title, expected):
    assert classify_source_type(url, title) == expected
