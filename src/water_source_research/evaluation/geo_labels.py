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

Metrics (validated = person-confirmed rows only; provisional = drafts; kept apart):
- geographic precision = (TARGET + SUPPLY_SOURCE + MIXED) / decided rows, INCLUDED stratum
- false exclusion rate = (TARGET + SUPPLY_SOURCE + MIXED) / decided rows, EXCLUDED stratum
  (UNCERTAIN rows are reported, not decided). Both are rates on the sample; the pool sizes are
  reported too, so a reader can scale them.

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
ABOUT_TARGET = {"TARGET", "SUPPLY_SOURCE", "MIXED"}
INCLUDED, EXCLUDED = "INCLUDED", "EXCLUDED"
COLUMNS = ["sample_id", "city", "region", "country", "url", "title",
           "quote_1", "chunk_1", "quote_2", "chunk_2", "quote_3", "chunk_3",
           "proposed_about_place", "proposed_basis", "label_status",
           "confirmed_about_place", "reviewer_notes", "reviewer", "reviewed_on"]

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
    scope = m.host_scope(doc["url"])
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
            out.append({"sample_id": row["sample_id"], "city": row["city"],
                        "url": row["url"], "confirmed": confirmed, "about_place": value or None})
    return out

def _rate(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    c = Counter(r["about_place"] or "UNLABELLED" for r in rows)
    decided = sum(c[p] for p in PLACES if p != "UNCERTAIN")
    hits = sum(c[p] for p in ABOUT_TARGET)
    return {"about_target": hits, "decided": decided, "uncertain": c["UNCERTAIN"],
            "unlabelled": c["UNLABELLED"], "counts": dict(c),
            "rate": round(hits / decided, 3) if decided else None}

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
        status = "validated" if row["confirmed"] else "provisional"
        for city in (k["city"], "ALL"):
            groups[(city, stratum, status)].append(row)
    out: dict[str, Any] = {}
    for (city, stratum, status), rows in sorted(groups.items()):
        metric = "precision" if stratum == INCLUDED else "false_exclusion"
        out.setdefault(city, {}).setdefault(metric, {})[status] = _rate(rows)
    out["_pool_sizes"] = key.get("pool_sizes", {})
    return out
