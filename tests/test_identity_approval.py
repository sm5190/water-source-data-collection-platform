"""The seven pilot identities are APPROVED with approval metadata; every other entry stays
DRAFT; approved entries pass the approval checks; government websites stay apart from the
geographic identity (a regional/provincial or unverified municipal site never gives authority)."""

import importlib.util
from pathlib import Path
import yaml
from water_source_research.core.paths import CONFIG_DIR
from water_source_research.services.city_identity import CityIdentity
from water_source_research.services.geo_match import GeoMatcher

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "approve_identity.py"
spec = importlib.util.spec_from_file_location("approve_identity", SCRIPT)
approve = importlib.util.module_from_spec(spec)
spec.loader.exec_module(approve)
PILOTS = {"Houston", "Santiago", "Vienna", "Hyderabad", "Nairobi", "Luanda", "Toronto"}
DOC = yaml.safe_load((CONFIG_DIR / "city_identity.yaml").read_text(encoding="utf-8"))
IDS = {e["city"]: CityIdentity.model_validate(e) for e in DOC["cities"]}

def test_exactly_the_pilots_are_approved_with_metadata():
    approved = {c for c, i in IDS.items() if i.status == "APPROVED"}
    assert approved == PILOTS
    for c in PILOTS:
        a = IDS[c].approval
        assert a and a.approved_by and a.approved_at and a.basis
        assert approve.problems(IDS[c]) == []
    assert all(i.status != "APPROVED" for c, i in IDS.items() if c not in PILOTS)

def test_government_websites_are_kept_apart_from_identity():
    santiago = GeoMatcher(IDS["Santiago"])
    assert santiago.official_host("https://www.gobiernosantiago.cl/") is None  # regional
    assert santiago.official_host("https://www.munistgo.cl/") is None  # not yet verified
    luanda = GeoMatcher(IDS["Luanda"])
    assert luanda.official_host("https://luanda.gov.ao/") is None  # provincial
    assert GeoMatcher(IDS["Houston"]).official_host("https://www.houstontx.gov/water")

def test_approve_refuses_an_entry_failing_the_checks(tmp_path):
    entry = {"city": "Testville", "country": "Aland", "status": "DRAFT"}
    path = tmp_path / "ids.yaml"
    path.write_text(yaml.safe_dump({"approval": {}, "cities": [entry]}), encoding="utf-8")
    assert approve.approve(["Testville"], "tester", "x", path) == 1  # no region, no country
    assert yaml.safe_load(path.read_text())["cities"][0]["status"] == "DRAFT"
