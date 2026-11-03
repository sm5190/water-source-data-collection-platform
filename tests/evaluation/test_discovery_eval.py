"""Discovery evaluation tooling: the label sheet hides system output, only confirmed rows are
validated, utility accuracy matches names or aliases, and the labels never reach research."""

import ast
import csv
import json
from pathlib import Path
import pytest
from water_source_research.evaluation.discovery_labels import (
    COLUMNS, build_sample, fill_proposals, labels_by_document, read_labels, write_sheet,
)
from water_source_research.evaluation.discovery_metrics import utility_accuracy

SRC = Path(__file__).resolve().parents[2] / "src" / "water_source_research"

def docs():
    return [{"run_id": "r1", "document_id": i, "city": "Santiago", "country": "Chile",
             "url": f"https://example{i}.org/p", "title": f"t{i}", "round": 1,
             "texts": [(f"c{i}", f"Santiago water supply page {i}.")]} for i in range(5)]

def test_sheet_hides_system_output_and_starts_unconfirmed(tmp_path):
    sample = build_sample(docs(), per_city=3)
    write_sheet(sample, {}, tmp_path / "s.csv", tmp_path / "k.json")
    rows = list(csv.DictReader((tmp_path / "s.csv").open(encoding="utf-8")))
    assert len(rows) == 3 and list(rows[0]) == COLUMNS
    assert all(r["label_status"] == "UNCONFIRMED" for r in rows)
    assert not {"source_type", "stratum", "run_id", "rank", "score"} & set(rows[0])
    key = json.loads((tmp_path / "k.json").read_text(encoding="utf-8"))
    assert set(key) == {r["sample_id"] for r in rows} and "stratum" in next(iter(key.values()))

def test_only_confirmed_rows_count_as_validated(tmp_path):
    write_sheet(build_sample(docs(), per_city=2), {}, tmp_path / "s.csv", tmp_path / "k.json")
    ids = [r["sample_id"] for r in csv.DictReader((tmp_path / "s.csv").open(encoding="utf-8"))]
    fill_proposals(tmp_path / "s.csv", {i: {"relevant": "YES", "publisher_kind": "NEWS",
                                            "serving_utility": "N_A", "basis": "q"}
                                        for i in ids})
    rows = list(csv.DictReader((tmp_path / "s.csv").open(encoding="utf-8")))
    rows[0].update(label_status="CONFIRMED", confirmed_relevant="NO",
                   confirmed_publisher_kind="GOVERNMENT", confirmed_serving_utility="N_A")
    with (tmp_path / "s.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    labels = {r["sample_id"]: r for r in read_labels(tmp_path / "s.csv")}
    assert labels[ids[0]]["confirmed"] and labels[ids[0]]["relevant"] == "NO"
    assert not labels[ids[1]]["confirmed"] and labels[ids[1]]["relevant"] == "YES"
    key = json.loads((tmp_path / "k.json").read_text(encoding="utf-8"))
    assert len(labels_by_document(labels.values(), key)) == 2

def test_invalid_label_values_are_rejected(tmp_path):
    write_sheet(build_sample(docs(), per_city=1), {}, tmp_path / "s.csv", tmp_path / "k.json")
    [row] = list(csv.DictReader((tmp_path / "s.csv").open(encoding="utf-8")))
    fill_proposals(tmp_path / "s.csv", {row["sample_id"]: {"relevant": "MAYBE"}})
    with pytest.raises(ValueError):
        read_labels(tmp_path / "s.csv")

def test_utility_accuracy_matches_aliases_and_counts_wrong_and_missed():
    expected = {"status": "UNCONFIRMED", "serving": [
        {"name": "Empresa Pública de Águas de Luanda", "aliases": ["EPAL"], "hosts": ["epal.co.ao"]},
        {"name": "Second Utility", "hosts": []}], "not_serving": [{"name": "Almar Water"}]}
    registry = {"candidates": [
        {"name": "Luanda Water Company", "aliases": ["EPAL"], "status": "VERIFIED",
         "bound_hosts": ["epal.co.ao"]},
        {"name": "Almar Water", "aliases": [], "status": "VERIFIED", "bound_hosts": ["almar.com"]},
        {"name": "Third", "aliases": [], "status": "CANDIDATE", "bound_hosts": []}]}
    acc = utility_accuracy(registry, expected)
    assert acc["correct"] == ["Luanda Water Company"]
    assert acc["wrong"] == ["Almar Water"] and acc["wrong_known_non_serving"] == ["Almar Water"]
    assert acc["missed"] == ["Second Utility"] and acc["expected_serving"] == 2
    assert acc["verified_hosts_wrong"] == ["almar.com"]

def test_labels_and_expectations_never_reach_research_code():
    for path in SRC.rglob("*.py"):
        if "evaluation" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        assert not {m for m in mods if m.endswith(("discovery_labels", "discovery_metrics", "geo_labels"))}, \
            path
        assert "tests/evaluation" not in path.read_text(encoding="utf-8"), path
