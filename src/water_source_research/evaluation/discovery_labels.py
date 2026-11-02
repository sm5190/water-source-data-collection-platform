"""Human-reviewed document labels for discovery evaluation (Milestone 3).

A stratified sample of acquired documents is exported as a CSV a person reviews. Each row
asks three separate questions, each with an "unknown/uncertain" option:
- relevant: is the document relevant to the target city's water supply? YES / NO / UNCERTAIN
- publisher_kind: who published it? (PUBLISHER_KINDS, or UNKNOWN)
- serving_utility: is it from a utility verified to serve the city? YES / NO / UNKNOWN / N_A

Proposed labels (drafted by the assistant from the quoted text) are marked UNCONFIRMED; only
rows whose ``confirmed_*`` columns a person filled count as validated. To limit confirmation
bias the sheet shows no system output: no source type, rank, gate outcome, model output or
which run/strategy found the document (that mapping is in a separate key file used only when
computing metrics). Rows are shuffled.

Isolation: these labels live under tests/evaluation/discovery/ and are read only by the
evaluation package (a test asserts no research module imports this module).
"""

import csv
import json
import random
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session
from water_source_research.db.evidence_models import EvidenceChunkRecord, SourceDocumentRecord
from water_source_research.db.research_models import ResearchJobRecord

PUBLISHER_KINDS = ("UTILITY_OPERATOR", "GOVERNMENT", "REGULATOR", "ACADEMIC", "TECHNICAL",
                   "INDEPENDENT_DATABASE", "NEWS", "DIRECTORY", "COMMERCIAL", "OTHER", "UNKNOWN")
RELEVANT = ("YES", "NO", "UNCERTAIN")
SERVING = ("YES", "NO", "UNKNOWN", "N_A")
COLUMNS = ["sample_id", "city", "region", "country", "url", "title",
           "quote_1", "chunk_1", "quote_2", "chunk_2", "quote_3", "chunk_3",
           "proposed_relevant", "proposed_publisher_kind", "proposed_serving_utility",
           "proposed_basis", "label_status",
           "confirmed_relevant", "confirmed_publisher_kind", "confirmed_serving_utility",
           "reviewer_notes", "reviewer", "reviewed_on"]
_WATER = re.compile(r"water|agua|água|wasser|eau|supply|abastec|potable|river|río|aquifer|"
                    r"reservoir|embalse|treatment|withdraw|production|utility|sanitar",
                    re.IGNORECASE)

def _host(url: str | None) -> str:
    return (urlsplit(url or "").hostname or "").casefold().removeprefix("www.")

def _quotes(texts: list[tuple[str, str]], city: str, n: int = 3, chars: int = 420
            ) -> list[tuple[str, str]]:
    """Up to ``n`` (quote, chunk_id): the passage around the city's name or water words,
    else the beginning. Verbatim excerpts, whitespace collapsed."""
    picked: list[tuple[str, str]] = []
    city_re = re.compile(re.escape(city), re.IGNORECASE)
    def excerpt(text: str, m: re.Match | None) -> str:
        t = " ".join(text.split())
        if m is None:
            return t[:chars]
        flat = " ".join(text[:m.start()].split())
        start = max(0, len(flat) - chars // 3)
        return ("…" if start else "") + t[start:start + chars]
    for pattern in (city_re, _WATER):
        for cid, text in texts:
            if len(picked) >= n:
                break
            if any(cid == c for _, c in picked):
                continue
            m = pattern.search(text)
            if m:
                picked.append((excerpt(text, m), cid))
    for cid, text in texts:
        if len(picked) >= n:
            break
        if not any(cid == c for _, c in picked):
            picked.append((excerpt(text, None), cid))
    return picked

def candidate_documents(engine: Engine, run_ids: Sequence[str]) -> list[dict[str, Any]]:
    """Every OK acquired document of the runs, with its chunks (for quotes and strata)."""
    out = []
    with Session(engine) as session:
        for run_id in run_ids:
            job = session.get(ResearchJobRecord, run_id)
            docs = session.scalars(select(SourceDocumentRecord).where(
                SourceDocumentRecord.run_id == run_id,
                SourceDocumentRecord.status == "OK")).all()
            for d in docs:
                chunks = session.scalars(select(EvidenceChunkRecord).where(
                    EvidenceChunkRecord.document_id == d.id).order_by(
                    EvidenceChunkRecord.ordinal)).all()
                if not chunks:
                    continue
                out.append({"run_id": run_id, "document_id": d.id, "city": job.city_name,
                            "country": job.country, "url": d.final_url or d.requested_url,
                            "title": d.title or "", "round": d.round,
                            "texts": [(c.chunk_id, c.text) for c in chunks]})
    return out

def stratum(doc: Mapping[str, Any]) -> str:
    """Internal stratum (never shown to the reviewer): current publisher kind and place."""
    from water_source_research.discovery.normalize import classify
    from water_source_research.services.geo_match import matcher_for
    kind = classify(doc["url"], doc["title"], city=doc["city"]).publisher_kind
    m = matcher_for(doc["city"], doc["country"])
    place = "?"
    if m is not None:
        scope = m.document_scope(doc["url"], doc["title"], [t for _, t in doc["texts"]])
        place = "ELSEWHERE" if scope.elsewhere else scope.label()
    return f"{kind}|{place}"

def build_sample(docs: list[dict[str, Any]], per_city: Mapping[str, int] | int = 25,
                 seed: int = 7) -> list[dict[str, Any]]:
    """A stratified sample per city: round-robin over strata (largest first) so every kind
    of document present is represented, up to the city's quota; one document per URL."""
    rng = random.Random(seed)
    by_city: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    seen: set[tuple[str, str]] = set()
    for d in docs:
        key = (d["city"], d["url"])
        if key in seen:
            continue
        seen.add(key)
        by_city[d["city"]][stratum(d)].append(d)
    picked = []
    for city, strata in sorted(by_city.items()):
        quota = per_city.get(city, 0) if isinstance(per_city, Mapping) else per_city
        pools = [list(v) for _, v in sorted(strata.items(), key=lambda kv: -len(kv[1]))]
        for pool in pools:
            rng.shuffle(pool)
        chosen: list[dict] = []
        while len(chosen) < quota and any(pools):
            for pool in pools:
                if pool and len(chosen) < quota:
                    chosen.append(pool.pop())
        picked += chosen
    rng.shuffle(picked)
    for n, d in enumerate(picked, 1):
        d["sample_id"] = f"D{n:03d}"
        d["stratum"] = stratum(d)
    return picked

def write_sheet(sample: list[dict[str, Any]], identities: Mapping[tuple[str, str], Any],
                csv_path: Path, key_path: Path) -> None:
    """The reviewer sheet (no system output) and the private key (sample -> run/document)."""
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for d in sample:
            ident = identities.get((d["city"], d["country"]))
            region = ident.region.name if ident is not None and ident.region else ""
            row = {k: "" for k in COLUMNS}
            row.update(sample_id=d["sample_id"], city=d["city"], region=region,
                       country=d["country"], url=d["url"], title=d["title"],
                       label_status="UNCONFIRMED")
            for i, (q, cid) in enumerate(_quotes(d["texts"], d["city"]), 1):
                row[f"quote_{i}"], row[f"chunk_{i}"] = q, cid
            w.writerow(row)
    key_path.write_text(json.dumps({d["sample_id"]: {
        "run_id": d["run_id"], "document_id": d["document_id"], "url": d["url"],
        "city": d["city"], "country": d["country"], "round": d["round"],
        "stratum": d["stratum"]} for d in sample}, indent=2), encoding="utf-8")

def read_labels(csv_path: Path) -> list[dict[str, Any]]:
    """Rows with their labels: ``confirmed`` labels where a person filled them (and the row
    is CONFIRMED), else the UNCONFIRMED proposal. Invalid values raise."""
    out = []
    with csv_path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            confirmed = row.get("label_status", "").strip().upper() == "CONFIRMED"
            rec = {"sample_id": row["sample_id"], "url": row["url"], "city": row["city"],
                   "confirmed": confirmed}
            for dim, allowed in (("relevant", RELEVANT), ("publisher_kind", PUBLISHER_KINDS),
                                 ("serving_utility", SERVING)):
                value = (row.get(f"confirmed_{dim}") if confirmed else
                         row.get(f"proposed_{dim}")) or ""
                value = value.strip().upper()
                if value and value not in allowed:
                    raise ValueError(f"{row['sample_id']}: {dim} {value!r} not in {allowed}")
                rec[dim] = value or None
            out.append(rec)
    return out

def fill_proposals(csv_path: Path, proposals: Mapping[str, Mapping[str, str]]) -> int:
    """Write drafted proposals (sample_id -> {relevant, publisher_kind, serving_utility,
    basis}) into the sheet; confirmed columns and status are left as they are."""
    with csv_path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    n = 0
    for row in rows:
        p = proposals.get(row["sample_id"])
        if not p:
            continue
        row["proposed_relevant"] = p.get("relevant", "")
        row["proposed_publisher_kind"] = p.get("publisher_kind", "")
        row["proposed_serving_utility"] = p.get("serving_utility", "")
        row["proposed_basis"] = p.get("basis", "")
        n += 1
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    return n

def labels_by_document(labels: Iterable[Mapping[str, Any]], key: Mapping[str, Mapping]
                       ) -> dict[tuple[str, str], Mapping[str, Any]]:
    """(city, url) -> label row, through the key file."""
    out = {}
    for row in labels:
        k = key.get(row["sample_id"])
        if k is not None:
            out[(k["city"], k["url"])] = row
    return out

def host_of(url: str | None) -> str:
    return _host(url)
