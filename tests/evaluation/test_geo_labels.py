"""Geographic labels: equal strata for inclusions and exclusions, a place label independent
of system output, and precision / false-exclusion rates with validated and provisional apart."""

import csv
import json
import pytest
from water_source_research.evaluation.geo_labels import (
    COLUMNS, EXCLUDED, INCLUDED, build_geo_sample, geo_decision, geo_metrics, read_geo_labels,
    write_geo_sheet,
)

def _doc(i, city="Houston", country="United States", texts=True):
    return {"run_id": "r", "document_id": i, "city": city, "country": country,
            "url": f"https://site{i}.example/p", "title": f"t{i}", "round": 1,
            "texts": [(f"c{i}", f"page {i}")] if texts else [], "decision": None,
            "reason": "x"}

def _pools(n_in, n_out):
    inc = [{**_doc(i), "decision": INCLUDED} for i in range(n_in)]
    exc = [{**_doc(100 + i), "decision": EXCLUDED} for i in range(n_out)]
    return {"Houston": {INCLUDED: inc, EXCLUDED: exc}}

def test_strata_get_the_same_size_capped_by_the_smaller_pool():
    sample = build_geo_sample(_pools(10, 3), per_stratum=5)
    assert sorted(d["decision"] for d in sample) == [EXCLUDED] * 3 + [INCLUDED] * 3

def test_sheet_hides_the_stratum(tmp_path):
    sample = build_geo_sample(_pools(2, 2), per_stratum=2)
    write_geo_sheet(sample, {}, tmp_path / "g.csv", tmp_path / "k.json",
                    {"Houston": {INCLUDED: 2, EXCLUDED: 2}})
    rows = list(csv.DictReader((tmp_path / "g.csv").open(encoding="utf-8")))
    assert list(rows[0]) == COLUMNS and not {"stratum", "reason", "decision"} & set(rows[0])
    key = json.loads((tmp_path / "k.json").read_text(encoding="utf-8"))
    assert key["pool_sizes"]["Houston"][EXCLUDED] == 2
    assert {v["stratum"] for v in key["samples"].values()} == {INCLUDED, EXCLUDED}

def _labelled(tmp_path, values, confirm=()):
    sample = build_geo_sample(_pools(2, 2), per_stratum=2)
    write_geo_sheet(sample, {}, tmp_path / "g.csv", tmp_path / "k.json", {})
    key = json.loads((tmp_path / "k.json").read_text(encoding="utf-8"))
    rows = list(csv.DictReader((tmp_path / "g.csv").open(encoding="utf-8")))
    for r in rows:
        r["proposed_about_place"] = values[key["samples"][r["sample_id"]]["stratum"]].pop()
        if r["sample_id"] in confirm:
            r.update(label_status="CONFIRMED", confirmed_about_place=r["proposed_about_place"])
    with (tmp_path / "g.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    return read_geo_labels(tmp_path / "g.csv"), key

def test_precision_and_false_exclusions_count_supply_sources_as_relevant(tmp_path):
    labels, key = _labelled(tmp_path, {INCLUDED: ["TARGET", "OTHER_PLACE"],
                                       EXCLUDED: ["SUPPLY_SOURCE", "UNCERTAIN"]})
    m = geo_metrics(labels, key)["ALL"]
    assert m["precision"]["provisional"]["about_target"] == 1
    assert m["precision"]["provisional"]["decided"] == 2
    fe = m["false_exclusion"]["provisional"]
    assert (fe["about_target"], fe["decided"], fe["uncertain"]) == (1, 1, 1)
    assert "validated" not in m["precision"]

def test_confirmed_rows_are_validated(tmp_path):
    labels, key = _labelled(tmp_path, {INCLUDED: ["TARGET", "TARGET"],
                                       EXCLUDED: ["OTHER_PLACE", "OTHER_PLACE"]},
                            confirm={"G001", "G002", "G003", "G004"})
    assert all(r["confirmed"] for r in labels)
    m = geo_metrics(labels, key)["Houston"]
    assert m["precision"]["validated"]["rate"] == 1.0
    assert m["false_exclusion"]["validated"]["rate"] == 0.0

def test_invalid_place_rejected(tmp_path):
    with pytest.raises(ValueError):
        _labelled(tmp_path, {INCLUDED: ["NEARBY", "TARGET"], EXCLUDED: ["TARGET", "TARGET"]})

def test_without_an_identity_nothing_is_excluded(monkeypatch):
    monkeypatch.delenv("CITY_IDENTITY_ALLOW_DRAFT", raising=False)
    from water_source_research.services import geo_match
    geo_match._matcher.cache_clear()
    assert geo_decision(_doc(1)) == (INCLUDED, "NAME_ONLY")
    geo_match._matcher.cache_clear()

def test_homonym_document_is_excluded(identity_approved):
    doc = {**_doc(1), "url": "https://msdh.ms.gov/page", "title": "Houston, Mississippi water",
           "texts": [("c1", "Houston, Mississippi public water system report.")]}
    decision, reason = geo_decision(doc)
    assert decision == EXCLUDED and "OTHER" in reason
