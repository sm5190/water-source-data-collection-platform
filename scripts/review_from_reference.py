"""Show what the manually researched workbook says about a run's open review items.

    uv run python scripts/review_from_reference.py <run_id> [--dataset <version>]
        [--api http://localhost:8000]

Read-only: prints, per open item, the decision the workbook would imply. It never submits
decisions: the workbook is the evaluation reference and must stay independent of research
values (a run reviewed from it would agree with it by construction). Earlier workbook-applied
reviews (reviewer "Sarina workbook (applied by Claude)") are labelled reference-derived by the
evaluation and excluded from its aggregates.

Per item, the implied decision against the workbook value for that field (and source):
  workbook N/A -> Not applicable; workbook Unknown -> Unknown;
  blank / placeholder / invalid cell, or a source the workbook doesn't list -> Defer;
  a candidate that matches the workbook value -> Accept;
  otherwise -> Correct to the workbook value (or reject / defer without a workbook URL).
"""

import argparse
import json
import sys
from typing import Any
from water_source_research.db.session import get_engine
from water_source_research.evaluation.compare import Prediction, compare, entity_keys
from water_source_research.evaluation.policy import load_policy
from water_source_research.evaluation.reference import ReferenceCity, ReferenceValue
from water_source_research.evaluation.store import EvaluationStore
from water_source_research.services.field_state import load_field_definitions
from water_source_research.ui.client import ResearchApiClient

WORKBOOK = "Sarina Water Source.xlsx"

def prediction(c: dict[str, Any]) -> Prediction:
    q = c.get("qualifiers") or {}
    v = c.get("validation") or {}
    value = c.get("normalized_value")
    status = c.get("value_status") or "KNOWN"
    return Prediction(
        value=c.get("value") if value is None else value,
        value_status=status if status in ("KNOWN", "UNKNOWN", "NOT_APPLICABLE") else "KNOWN",
        reference_year=q.get("reference_year") if q.get("year_basis") in ("quote", "passage")
        else None,
        flags=[k["code"] for k in v.get("checks", []) if k["result"] in ("WARN", "FAIL")],
        claim_uid=c.get("claim_uid"))

def invalid(c: dict[str, Any]) -> bool:
    return (c.get("validation") or {}).get("outcome") == "INVALID"

class Reviewer:
    def __init__(self, city: ReferenceCity) -> None:
        self.city = city
        self.fields = load_field_definitions()
        self.policy = load_policy()

    def record_for(self, label: str) -> str | None:
        keys = entity_keys(label, self.policy)
        return next((r.record_key for r in self.city.records
                     if r.water_source and entity_keys(r.water_source, self.policy) & keys),
                    None)

    def row_urls(self, record_key: str | None) -> list[str]:
        recs = [r for r in self.city.records if r.record_key == record_key] or self.city.records
        return list(dict.fromkeys(u for r in recs for urls in r.references.values()
                                  for u in urls))

    def decide(self, item: dict[str, Any]) -> tuple[dict[str, Any], str]:
        """(decision body without keys, one-line explanation)."""
        p = item["payload"]
        fk = item["field_key"]
        d = self.fields[fk]
        entity = item["entity_key"]
        cands = p.get("candidates") or []
        sources = {s["key"]: s["label"] for s in p.get("sources") or []}
        if d.scope in ("city", "time_series") or entity == "city":
            return self._with_ref(item, self.city.value(fk, ""), None)
        if entity == "unattributed" and len(sources) > 1 and len(self.city.records) > 1:
            for c in cands:
                for key, label in sources.items():
                    rk = self.record_for(label)
                    ref = self.city.value(fk, rk) if rk else None
                    if ref and ref.value_status == "KNOWN" and compare(
                            d, ref, prediction(c), self.policy).status == "MATCH":
                        return ({"decision": "ASSIGN_SOURCE", "claim_uids": [c["claim_uid"]],
                                 "entity_key": key},
                                f"matches the workbook value for {label}: assign it there")
            return {"decision": "DEFER"}, "can't tell which source this value belongs to"
        if entity in ("*", "unattributed"):
            recs = self.city.records
            refs = [self.city.value(fk, r.record_key) for r in recs]
            shown = {(r.value_status, json.dumps(r.normalized, default=str)) if r else None
                     for r in refs}
            if len(shown) == 1:
                return self._with_ref(item, refs[0], recs[0].record_key if recs else None)
            return {"decision": "DEFER"}, "the workbook gives a different value per source"
        rk = self.record_for(item.get("label") or sources.get(entity) or entity)
        if rk is None:
            if fk == "water_source" and cands:
                return ({"decision": "REJECT", "claim_uids": [c["claim_uid"] for c in cands]},
                        "the workbook doesn't list this water source")
            return {"decision": "DEFER"}, "the workbook doesn't list this water source"
        return self._with_ref(item, self.city.value(fk, rk), rk)

    def _with_ref(self, item: dict[str, Any], ref: ReferenceValue | None,
                  record_key: str | None) -> tuple[dict[str, Any], str]:
        d = self.fields[item["field_key"]]
        cands = item["payload"].get("candidates") or []
        if ref is None or ref.value_status == "BLANK":
            return {"decision": "DEFER"}, "the workbook cell is blank"
        if ref.value_status == "UNKNOWN":
            return {"decision": "MARK_UNKNOWN"}, "the workbook says Unknown"
        if ref.value_status == "NOT_APPLICABLE":
            return {"decision": "MARK_NA"}, "the workbook says N/A"
        if ref.value_status == "PLACEHOLDER" or any(i.startswith("INVALID") for i in ref.issues):
            return {"decision": "DEFER"}, f"the workbook cell isn't a usable value ({ref.raw!r})"
        if ref.value_status == "NONE_REPORTED":
            value: Any = "None Reported"
        elif d.type in ("number", "percent", "coordinate", "integer"):
            value = ref.normalized
            if d.type == "integer" and isinstance(value, float) and value.is_integer():
                value = int(value)
        else:
            value = str(ref.raw).strip()  # text exactly as the workbook has it
        if ref.value_status == "KNOWN":
            matching = [c for c in cands
                        if compare(d, ref, prediction(c), self.policy).status == "MATCH"]
            if matching:
                best = min(matching, key=invalid)
                body: dict[str, Any] = {"decision": "APPROVE", "claim_uids": [best["claim_uid"]]}
                if invalid(best):
                    body["override_validation"] = True
                return body, f"matches the workbook ({ref.raw!r})"
        urls = ref.source_urls or self.row_urls(record_key)
        if urls:
            return ({"decision": "CORRECT", "value": value,
                     "claim_uids": [cands[0]["claim_uid"]] if cands else [],
                     "evidence": {"url": urls[0], "title": f"Source cited in {WORKBOOK}",
                                  # the year in the note lets the year derivations find it
                                  "note": f"{WORKBOOK} row {ref.row}: {str(ref.raw)[:300]}"
                                          + (f" (reported for {ref.reference_year})"
                                             if ref.reference_year else "")}},
                    f"workbook value {ref.raw!r}")
        if cands:
            return ({"decision": "REJECT", "claim_uids": [c["claim_uid"] for c in cands]},
                    "differs from the workbook, which cites no source for it")
        return {"decision": "DEFER"}, "the workbook cites no source for its value"

def show_round(client: ResearchApiClient, run_id: str, reviewer: Reviewer) -> None:
    """Print what the workbook implies for each open item (nothing is submitted)."""
    pending = client.review_pending(run_id)
    items = [i for i in pending["items"] if not i.get("decision")]
    print(f"round {pending['round']}: {len(items)} open item(s) (read-only comparison)")
    for item in items:
        body, why = reviewer.decide(item)
        label = item["field_key"] + (f" [{item.get('label') or item['entity_key']}]"
                                     if item["entity_key"] not in ("city", "*") else "")
        print(f"  {body['decision']:<14} {label:<60} {why}"[:200])

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id")
    parser.add_argument("--dataset", default=None)
    parser.add_argument("--api", default="http://localhost:8000")
    args = parser.parse_args()
    client = ResearchApiClient(args.api, timeout=120)
    run = next((r for r in client.runs(limit=500) if r["run_id"] == args.run_id), None)
    if run is None:
        print("unknown run")
        return 2
    store = EvaluationStore(get_engine())
    dataset = args.dataset or store.latest_dataset_with(run["city_name"], run.get("country"))
    ds = store.load_dataset(dataset) if dataset else None
    city = ds.city(run["city_name"], run.get("country")) if ds else None
    if city is None:
        print(f"no workbook row for {run['city_name']}")
        return 2
    reviewer = Reviewer(city)
    if run["status"] != "AWAITING_REVIEW":
        print(f"run is {run['status']}: nothing waits for review")
        return 0
    show_round(client, args.run_id, reviewer)
    return 0

if __name__ == "__main__":
    sys.exit(main())
