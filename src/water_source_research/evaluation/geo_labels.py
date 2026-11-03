"""Geographic labels: wrong-place inclusions and false geographic exclusions (Milestone 3.1).

One question per document, independent of what the system decided:
"which place is this document about, for the target city's water supply?"
- TARGET: about the target city (or its utility / service area).
- SUPPLY_SOURCE: about a source, basin or infrastructure that supplies the target city,
  even when it lies outside the city (upstream, regional, transboundary).
- MIXED: several places, with a part about the target or its supply.
- OTHER_PLACE: about another place only (a homonym, another city or country).
- UNCERTAIN: can't tell from the document.

Two strata are sampled at the same size per city, so false exclusions are measured as
carefully as wrong inclusions:
- INCLUDED: documents the current geography rules treat as possibly about the target
  (acquired OK and not "elsewhere").
- EXCLUDED: documents the rules treat as elsewhere: acquired OK but scoped OTHER_COUNTRY /
  OTHER_REGION, or returned by search but deprioritized as a homonym / other place (title and
  host only; those were never fetched, so the reviewer opens the link).

Document relevance and passage relevance are kept apart (Milestone 3.1 Phase 2). Each quoted
passage gets its own label (passage_<i>_place_*: TARGET = about the target city's water,
utility or service area; SUPPLY_SOURCE = about a source or infrastructure supplying it, even far
away; OTHER_PLACE = about another place; NOT_ABOUT_PLACE = names the city without being about
it (an address, a company's headquarters, an event venue, boilerplate); UNCERTAIN). A document counts as relevant when its label is TARGET or
SUPPLY_SOURCE, or when it is MIXED with at least one passage labelled TARGET or SUPPLY_SOURCE;
a MIXED document whose passages aren't labelled relevant (or that has no passages: search
results never fetched) is UNDETERMINED, never relevant. Validated relevance needs the
document label and that passage confirmed by a person.

Metrics (validated = person-confirmed only; provisional = drafts; kept apart):
- geographic precision = relevant / decided documents, INCLUDED stratum
- false exclusion rate = relevant / decided documents, EXCLUDED stratum
  (UNCERTAIN and UNDETERMINED are reported, not decided). Rates on the sample; pool sizes are
  reported so a reader can scale them.
- passages (``passage_metrics``): per labelled passage against the system's passage decision
  (INCLUDED / EXCLUDED / UNCERTAIN): correctly included, false exclusions, relevant passages
  left uncertain, wrong inclusions, correctly excluded.

The sheet hides the stratum and every system output (key file only). Evaluation only: the
research package never imports this module.
"""

import csv
import json
import random
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session
from water_source_research.db.evidence_models import DiscoveryResultRecord
from water_source_research.db.research_models import ResearchJobRecord
from water_source_research.discovery.normalize import canonical_url
from .discovery_labels import _quotes, candidate_documents

PLACES = ("TARGET", "SUPPLY_SOURCE", "MIXED", "OTHER_PLACE", "UNCERTAIN")
RELEVANT_PLACES = {"TARGET", "SUPPLY_SOURCE"}
PASSAGE_PLACES = ("TARGET", "SUPPLY_SOURCE", "OTHER_PLACE", "NOT_ABOUT_PLACE", "UNCERTAIN")
MAX_PASSAGES = 3
INCLUDED, EXCLUDED = "INCLUDED", "EXCLUDED"
COLUMNS = ["sample_id", "city", "region", "country", "url", "title",
           "quote_1", "chunk_1", "quote_2", "chunk_2", "quote_3", "chunk_3",
           "proposed_about_place", "proposed_basis", "label_status",
           "confirmed_about_place", "reviewer_notes", "reviewer", "reviewed_on",
           *(f"passage_{i}_place_{k}" for i in range(1, MAX_PASSAGES + 1)
             for k in ("proposed", "confirmed"))]

def geo_decision(doc: Mapping[str, Any]) -> tuple[str, str]:
    """(INCLUDED|EXCLUDED, reason) under the current rules. Uses the city identity the
    environment allows (evaluation runs with the draft identity, labelled as such); with no
    identity every document is INCLUDED ("name-only: no geography")."""
    from water_source_research.services.geo_match import matcher_for
    m = matcher_for(doc["city"], doc["country"])
    if m is None:
        return INCLUDED, "NAME_ONLY"
    if doc.get("texts"):
        scope = m.document_scope(doc["url"], doc.get("title"), [t for _, t in doc["texts"]])
        return (EXCLUDED, f"DOCUMENT_{scope.label()}") if scope.elsewhere else (
            INCLUDED, f"DOCUMENT_{scope.label()}")
    # The scope discovery ranking and acquisition use for a search result (title as the
    # subject, publisher apart); older code without it judged by the host.
    scope = (m.search_scope(doc["url"], doc.get("title")) if hasattr(m, "search_scope")
             else m.host_scope(doc["url"]))
    status = m.match_place(doc.get("title") or "", scope).status
    if scope.elsewhere or status in ("HOMONYM", "OTHER_PLACE"):
        return EXCLUDED, f"SEARCH_{scope.label()}_{status}"
    return INCLUDED, f"SEARCH_{scope.label()}_{status}"

def search_only_documents(engine: Engine, run_ids: Sequence[str],
                          acquired: set[tuple[str, str]]) -> list[dict[str, Any]]:
    """Search results the runs never acquired (one per city and canonical URL)."""
    out, seen = [], set()
    with Session(engine) as session:
        for run_id in run_ids:
            job = session.get(ResearchJobRecord, run_id)
            for r in session.scalars(select(DiscoveryResultRecord).where(
                    DiscoveryResultRecord.run_id == run_id)).all():
                key = (job.city_name, canonical_url(r.url) or r.url)
                if key in acquired or key in seen or r.provider in ("openalex",
                                                                   "semantic_scholar"):
                    continue
                seen.add(key)
                out.append({"run_id": run_id, "document_id": None, "city": job.city_name,
                            "country": job.country, "url": r.url, "title": r.title or "",
                            "round": r.round, "texts": []})
    return out

def pools(engine: Engine, run_ids: Sequence[str]) -> dict[str, dict[str, list[dict]]]:
    """city -> stratum -> documents (each with its decision and reason)."""
    docs = candidate_documents(engine, run_ids)
    acquired = {(d["city"], canonical_url(d["url"]) or d["url"]) for d in docs}
    docs += search_only_documents(engine, run_ids, acquired)
    out: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    seen: set[tuple[str, str]] = set()
    for d in docs:
        key = (d["city"], canonical_url(d["url"]) or d["url"])
        if key in seen:
            continue
        seen.add(key)
        d["decision"], d["reason"] = geo_decision(d)
        out[d["city"]][d["decision"]].append(d)
    return out

def build_geo_sample(city_pools: Mapping[str, Mapping[str, list[dict]]], per_stratum: int,
                     seed: int = 11, prefix: str = "G") -> list[dict[str, Any]]:
    """The same number per stratum and city (the smaller pool caps both strata)."""
    rng = random.Random(seed)
    picked = []
    for city in sorted(city_pools):
        strata = city_pools[city]
        n = min(per_stratum, *(len(strata.get(s, [])) for s in (INCLUDED, EXCLUDED)))
        for s in (INCLUDED, EXCLUDED):
            pool = list(strata.get(s, []))
            rng.shuffle(pool)
            picked += pool[:n]
    rng.shuffle(picked)
    for i, d in enumerate(picked, 1):
        d["sample_id"] = f"{prefix}{i:03d}"
    return picked

def write_geo_sheet(sample: Sequence[Mapping[str, Any]], regions: Mapping[tuple[str, str], str],
                    csv_path: Path, key_path: Path,
                    pool_sizes: Mapping[str, Mapping[str, int]]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for d in sample:
            row = {k: "" for k in COLUMNS}
            row.update(sample_id=d["sample_id"], city=d["city"], country=d["country"],
                       region=regions.get((d["city"], d["country"]), ""), url=d["url"],
                       title=d["title"], label_status="UNCONFIRMED")
            for i, (q, cid) in enumerate(_quotes(d["texts"], d["city"]) if d["texts"]
                                         else [], 1):
                row[f"quote_{i}"], row[f"chunk_{i}"] = q, cid
            w.writerow(row)
    key_path.write_text(json.dumps({
        "pool_sizes": pool_sizes,
        "samples": {d["sample_id"]: {"run_id": d["run_id"], "document_id": d["document_id"],
                                     "url": d["url"], "city": d["city"],
                                     "country": d["country"], "round": d["round"],
                                     "stratum": d["decision"], "reason": d["reason"]}
                    for d in sample}}, indent=2), encoding="utf-8")

def read_geo_labels(csv_path: Path) -> list[dict[str, Any]]:
    out = []
    with csv_path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            confirmed = row.get("label_status", "").strip().upper() == "CONFIRMED"
            value = ((row.get("confirmed_about_place") if confirmed
                      else row.get("proposed_about_place")) or "").strip().upper()
            if value and value not in PLACES:
                raise ValueError(f"{row['sample_id']}: about_place {value!r} not in {PLACES}")
            passages = []
            for i in range(1, MAX_PASSAGES + 1):
                quote = (row.get(f"quote_{i}") or "").strip()
                if not quote:
                    continue
                conf = (row.get(f"passage_{i}_place_confirmed") or "").strip().upper()
                prop = (row.get(f"passage_{i}_place_proposed") or "").strip().upper()
                place = (conf if confirmed and conf else prop) or None
                if place and place not in PASSAGE_PLACES:
                    raise ValueError(f"{row['sample_id']} passage {i}: {place!r} not in "
                                     f"{PASSAGE_PLACES}")
                passages.append({"index": i, "quote": quote, "chunk_id": row.get(f"chunk_{i}"),
                                 "place": place, "confirmed": bool(confirmed and conf)})
            out.append({"sample_id": row["sample_id"], "city": row["city"],
                        "url": row["url"], "confirmed": confirmed, "about_place": value or None,
                        "passages": passages})
    return out

def document_relevance(row: Mapping[str, Any]) -> tuple[str, bool]:
    """(RELEVANT | NOT_RELEVANT | UNCERTAIN | UNDETERMINED | UNLABELLED, validated)."""
    place = row.get("about_place")
    if not place:
        return "UNLABELLED", False
    if place in RELEVANT_PLACES:
        return "RELEVANT", row["confirmed"]
    if place == "OTHER_PLACE":
        return "NOT_RELEVANT", row["confirmed"]
    if place == "UNCERTAIN":
        return "UNCERTAIN", row["confirmed"]
    relevant = [p for p in row.get("passages", []) if p["place"] in RELEVANT_PLACES]
    if relevant:  # MIXED with an independently labelled relevant passage
        return "RELEVANT", row["confirmed"] and any(p["confirmed"] for p in relevant)
    return "UNDETERMINED", row["confirmed"]

def _rate(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    c = Counter(document_relevance(r)[0] for r in rows)
    places = Counter(r["about_place"] or "UNLABELLED" for r in rows)
    decided = c["RELEVANT"] + c["NOT_RELEVANT"]
    hits = c["RELEVANT"]
    return {"about_target": hits, "decided": decided, "uncertain": c["UNCERTAIN"],
            "undetermined": c["UNDETERMINED"], "unlabelled": c["UNLABELLED"],
            "counts": dict(places), "rate": round(hits / decided, 3) if decided else None}

def geo_metrics(labels: Sequence[Mapping[str, Any]], key: Mapping[str, Any],
                decide=None) -> dict[str, Any]:
    """Per city and overall: precision (INCLUDED) and false exclusions (EXCLUDED), validated
    and provisional apart. ``decide(sample) -> stratum`` re-decides each labelled document
    under other rules (comparing rule versions on the same labels); default = the stratum
    recorded when the sheet was drawn."""
    samples = key["samples"]
    groups: dict[tuple[str, str, str], list] = defaultdict(list)
    for row in labels:
        k = samples.get(row["sample_id"])
        if k is None:
            continue
        stratum = decide(k) if decide else k["stratum"]
        validated = document_relevance(row)[1] if row["about_place"] in (
            "MIXED",) else row["confirmed"]
        status = "validated" if validated else "provisional"
        for city in (k["city"], "ALL"):
            groups[(city, stratum, status)].append(row)
    out: dict[str, Any] = {}
    for (city, stratum, status), rows in sorted(groups.items()):
        metric = "precision" if stratum == INCLUDED else "false_exclusion"
        out.setdefault(city, {}).setdefault(metric, {})[status] = _rate(rows)
    out["_pool_sizes"] = key.get("pool_sizes", {})
    return out

PASSAGE_OUTCOMES = ("correctly_included", "false_exclusion", "relevant_uncertain",
                    "wrong_inclusion", "correctly_excluded", "other_uncertain")

def passage_outcome(label: str | None, decision: str) -> str | None:
    """How the system's passage decision (INCLUDED / EXCLUDED / UNCERTAIN) compares with a
    passage label; None when the label is missing or UNCERTAIN."""
    if label in RELEVANT_PLACES:
        return {"INCLUDED": "correctly_included", "EXCLUDED": "false_exclusion"}.get(
            decision, "relevant_uncertain")
    if label in ("OTHER_PLACE", "NOT_ABOUT_PLACE"):
        return {"INCLUDED": "wrong_inclusion", "EXCLUDED": "correctly_excluded"}.get(
            decision, "other_uncertain")
    return None

def passage_metrics(labels: Sequence[Mapping[str, Any]], key: Mapping[str, Any],
                    decide_passage) -> dict[str, Any]:
    """Per city and overall, validated and provisional apart: passage outcomes.
    ``decide_passage(sample, passage) -> INCLUDED | EXCLUDED | UNCERTAIN``."""
    samples = key["samples"]
    out: dict[str, Any] = {}
    for row in labels:
        k = samples.get(row["sample_id"])
        if k is None:
            continue
        for p in row.get("passages", []):
            outcome = passage_outcome(p["place"], decide_passage(k, p))
            if outcome is None:
                continue
            status = "validated" if p["confirmed"] else "provisional"
            for city in (k["city"], "ALL"):
                bucket = out.setdefault(city, {}).setdefault(status, Counter())
                bucket[outcome] += 1
    return {c: {s: dict(v) for s, v in by.items()} for c, by in out.items()}
