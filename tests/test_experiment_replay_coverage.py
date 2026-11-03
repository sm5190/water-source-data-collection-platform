"""The experiment runner checks replay coverage itself (so baseline code is covered too): a
search missing from the recording is NOT_RECORDED and makes the run not like-for-like."""

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "discovery_experiment.py"

def _script():
    spec = importlib.util.spec_from_file_location("discovery_experiment", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def _rounds():
    return [{"round": 1, "searches": [
        {"provider": "searxng", "query": "Houston  Texas water source"},
        {"provider": "searxng", "query": "Houston utility not recorded"},
        {"provider": "crossref", "query": None},
    ]}]

def _snap(tmp_path):
    path = tmp_path / "s.snapshots.jsonl"
    path.write_text(json.dumps({"provider": "searxng", "query": "houston texas WATER source",
                                "results": []}) + "\n", encoding="utf-8")
    return str(path)

def test_missing_query_is_not_recorded_and_breaks_like_for_like(tmp_path):
    rounds = _rounds()
    cov = _script().replay_coverage(rounds, [_snap(tmp_path)], live_on_miss=False)
    assert cov["counts"] == {"RECORDED": 1, "NOT_RECORDED": 1, "LIVE_ON_MISS": 0,
                             "NOT_REPLAYED": 1}
    assert cov["like_for_like"] is False
    assert [s["replay"] for s in rounds[0]["searches"]] == ["RECORDED", "NOT_RECORDED",
                                                            "NOT_REPLAYED"]

def test_live_on_miss_is_marked_and_kept_apart(tmp_path):
    cov = _script().replay_coverage(_rounds(), [_snap(tmp_path)], live_on_miss=True)
    assert cov["counts"]["LIVE_ON_MISS"] == 1 and cov["like_for_like"] is True
    assert cov["misses"] == [{"round": 1, "provider": "searxng",
                              "query": "Houston utility not recorded"}]
