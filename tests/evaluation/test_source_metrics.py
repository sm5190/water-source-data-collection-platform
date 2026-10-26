"""Evaluation correctness for multi-source cities: one cell per city-wide fact (no fan-out),
no credit for sources the run never identified, one-to-one source pairing (judge only as a
provisional fallback), source precision / recall, the judge applied consistently to RAW
candidates, and the workbook kept out of research code."""

import ast
from pathlib import Path
import pytest
from procedure_helpers import make_engine
from water_source_research.db.models import Base
from water_source_research.evaluation.evaluate import (
    _cells, _pair, _unidentified, apply_judge, source_metrics,
)
from water_source_research.evaluation.judge import Judgment, JudgmentStore, SemanticJudge
from water_source_research.evaluation.policy import load_policy
from water_source_research.evaluation.reference import (
    ReferenceCity, ReferenceRecord, ReferenceValue,
)
from water_source_research.services.field_state import load_field_definitions

FIELDS = load_field_definitions()
SRC = Path(__file__).resolve().parents[2] / "src" / "water_source_research"

def value(field, record, raw, status="KNOWN"):
    return ReferenceValue(field_key=field, scope="RECORD", record_key=record, raw=raw,
                          normalized=raw, value_status=status)

def city(*rows):
    """rows: (water source, {field: raw})"""
    records, values = [], []
    for n, (source, cells) in enumerate(rows):
        key = source.casefold()
        records.append(ReferenceRecord(record_key=key, water_source=source, utility="U", row=n))
        values.append(value("water_source", key, source))
        values += [value(f, key, v) for f, v in cells.items()]
    return ReferenceCity(city_key="x|y", city="X", country="Y", records=records, values=values)

class Llm:
    provider, model = "fake", "fake-1"

    def __init__(self, verdicts):
        self.verdicts, self.calls = verdicts, []

    def structured(self, system, user, schema):
        self.calls.append(user)
        verdict = next((v for k, v in self.verdicts.items() if k in user), "DIFFERENT")
        return Judgment(verdict=verdict, same_meaning_of_measure="yes",
                        same_geographic_scope="yes", same_reporting_period="not_applicable",
                        justification="test")

@pytest.fixture
def engine(tmp_path):
    e = make_engine(tmp_path, "src.db")
    Base.metadata.create_all(e)
    return e

def test_a_fact_repeated_in_every_workbook_row_is_one_cell():
    c = city(("Thika River", {"utility_name": "Nairobi Water", "water_type": "Surface"}),
             ("Chania River", {"utility_name": "Nairobi Water", "water_type": "Surface"}),
             ("Kikuyu Springs", {"utility_name": "Nairobi Water", "water_type": "Groundwater"}))
    cells = [(scope, rec.record_key if rec else "", d.key, tags)
             for scope, rec, d, _, tags in _cells(c, FIELDS)
             if d.key in ("utility_name", "water_type", "water_source")]
    utility = [x for x in cells if x[2] == "utility_name"]
    assert utility == [("CITY", "", "utility_name", ["CITY_WIDE_IN_REFERENCE"])]
    # differing values stay per source; water_source is always per source
    assert len([x for x in cells if x[2] == "water_type"]) == 3
    assert len([x for x in cells if x[2] == "water_source"]) == 3

def test_a_source_the_run_never_identified_gets_no_credit():
    c = city(("Thika River", {"water_type": "Surface"}), ("Kikuyu Springs", {}))
    ref = c.value("water_type", "thika river")
    for stage in ("RAW", "PROPOSED", "AUTO", "FINAL"):
        r = _unidentified(stage, FIELDS["water_type"], ref, load_policy(), c)
        assert r["status"] == "MISSING_PREDICTION"
        assert "SOURCE_NOT_IDENTIFIED" in r["comparison"]["tags"]

def test_sources_pair_one_to_one_and_judge_pairs_are_provisional(engine):
    c = city(("Thika River", {}), ("Chania River", {}), ("Kikuyu Springs", {}))
    predicted = [("thika river", "Thika River"), ("thika", "Thika"),
                 ("reservoirs dams", "Reservoirs/Dams (Ndakaini)"),
                 ("chania", "River Chania")]
    plain = _pair(c.records, predicted, load_policy())
    assert plain["pairs"] == {"thika river": "thika river", "chania river": None,
                              "kikuyu springs": None}
    llm = Llm({"MANUAL: Chania River\nAUTOMATED: River Chania": "EQUIVALENT",
               "MANUAL: Chania River\nAUTOMATED: Thika": "EQUIVALENT"})
    judge = SemanticJudge(llm, JudgmentStore(engine), descriptions={})
    judged = _pair(c.records, predicted, load_policy(), judge)
    # "Thika" can't take a second workbook row: the first unused equivalent wins
    assert judged["pairs"]["chania river"] in ("chania", "thika")
    assert judged["method"]["chania river"].startswith("JUDGE:")
    assert len({v for v in judged["pairs"].values() if v}) == 2  # never the same twice
    metrics = {m["metric"]: m for m in source_metrics({"RAW": judged})}
    assert (metrics["source_precision"]["numerator"],
            metrics["source_precision"]["denominator"]) == (2, 4)
    assert (metrics["source_recall"]["numerator"],
            metrics["source_recall"]["denominator"]) == (2, 3)
    assert metrics["source_recall"]["detail"]["missing"] == ["Kikuyu Springs"]
    assert metrics["source_recall"]["detail"]["provisional"] == 1
    assert "Reservoirs/Dams (Ndakaini)" in metrics["source_precision"]["detail"]["extra"]

def test_raw_candidates_are_judged_like_the_proposed_value(engine):
    llm = Llm({"AUTOMATED: Nairobi Water Company": "EQUIVALENT"})
    judge = SemanticJudge(llm, JudgmentStore(engine), descriptions={})
    ref = {"raw": "Nairobi City Water and Sewerage Company",
           "normalized": "Nairobi City Water and Sewerage Company"}
    cand = {"claim_uid": "c1", "value": "Nairobi Water Company", "status": "PARTIAL_MATCH",
            "tags": ["NAME_CONTAINMENT"], "detail": "contains", "confidence": 0.9,
            "relative_error": None, "ratio": None, "distance_km": None, "coverage": None,
            "matched": [], "missing": [], "extra": []}
    rows = [
        {"stage": "RAW", "field_key": "utility_name", "status": "PARTIAL_MATCH",
         "reference": ref, "comparison": {"status": "PARTIAL_MATCH"},
         "predicted": {"candidates": [cand], "top_candidate": "c1",
                       "top_candidate_status": "PARTIAL_MATCH"}},
        {"stage": "PROPOSED", "field_key": "utility_name", "status": "PARTIAL_MATCH",
         "reference": ref, "comparison": {"status": "PARTIAL_MATCH", "tags": []},
         "predicted": {"value": "Nairobi Water Company", "selected_claim_uids": ["c1"]}},
    ]
    apply_judge(rows, judge, {"claims": []})
    raw, proposed = rows
    assert raw["status"] == proposed["status"] == "MATCH"
    assert raw["predicted"]["top_candidate_status"] == "MATCH"
    assert raw["comparison"]["judgment_id"] == proposed["comparison"]["judgment_id"]
    assert len(llm.calls) == 1  # the same question is asked once

def test_research_code_never_reads_the_reference_or_evaluation():
    research = ["agents", "acquisition", "context_manager", "discovery", "llm", "procedures",
                "research_queue", "services", "schemas"]
    offenders = []
    for package in research:
        for path in (SRC / package).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                elif isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                for name in names:
                    if ".evaluation" in name or name.endswith("evaluation_models"):
                        offenders.append(f"{path.relative_to(SRC)}: {name}")
    assert offenders == []
