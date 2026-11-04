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

def test_without_an_identity_nothing_is_excluded(no_identity):
    assert geo_decision(_doc(1)) == (INCLUDED, "NAME_ONLY")

def test_homonym_document_is_excluded(identity_approved):
    doc = {**_doc(1), "url": "https://msdh.ms.gov/page", "title": "Houston, Mississippi water",
           "texts": [("c1", "Houston, Mississippi public water system report.")]}
    decision, reason = geo_decision(doc)
    assert decision == EXCLUDED and "OTHER" in reason

# --- document vs passage relevance (Milestone 3.1 Phase 2) ---------------------------------

from water_source_research.evaluation.geo_labels import (
    document_relevance, passage_metrics, passage_outcome,
)

def _row(place, passages=(), confirmed=False):
    return {"sample_id": "G1", "city": "Luanda", "url": "u", "confirmed": confirmed,
            "about_place": place,
            "passages": [{"index": i, "quote": "q", "chunk_id": "c", "place": p,
                          "confirmed": c} for i, (p, c) in enumerate(passages, 1)]}

def test_a_mixed_document_is_relevant_only_with_a_relevant_passage():
    assert document_relevance(_row("MIXED")) == ("UNDETERMINED", False)
    assert document_relevance(_row("MIXED", [("OTHER_PLACE", False)]))[0] == "UNDETERMINED"
    assert document_relevance(_row("MIXED", [("NOT_ABOUT_PLACE", False),
                                             ("TARGET", False)])) == ("RELEVANT", False)
    # validated needs the document label and the relevant passage confirmed
    assert document_relevance(_row("MIXED", [("TARGET", False)], confirmed=True)) == \
        ("RELEVANT", False)
    assert document_relevance(_row("MIXED", [("SUPPLY_SOURCE", True)], confirmed=True)) == \
        ("RELEVANT", True)
    assert document_relevance(_row("TARGET"))[0] == "RELEVANT"
    assert document_relevance(_row("OTHER_PLACE"))[0] == "NOT_RELEVANT"

def test_passage_outcomes():
    assert passage_outcome("TARGET", "INCLUDED") == "correctly_included"
    assert passage_outcome("SUPPLY_SOURCE", "EXCLUDED") == "false_exclusion"
    assert passage_outcome("TARGET", "UNCERTAIN") == "relevant_uncertain"
    assert passage_outcome("OTHER_PLACE", "INCLUDED") == "wrong_inclusion"
    assert passage_outcome("NOT_ABOUT_PLACE", "EXCLUDED") == "correctly_excluded"
    assert passage_outcome("UNCERTAIN", "INCLUDED") is None

def test_passage_metrics_keep_validated_and_provisional_apart():
    key = {"samples": {"G1": {"city": "Luanda", "stratum": EXCLUDED}}}
    row = _row("MIXED", [("TARGET", True), ("OTHER_PLACE", False)], confirmed=True)
    m = passage_metrics([row], key, lambda k, p: "EXCLUDED")
    assert m["Luanda"]["validated"] == {"false_exclusion": 1}
    assert m["ALL"]["provisional"] == {"correctly_excluded": 1}
