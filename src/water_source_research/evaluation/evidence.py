"""Evidence and provenance checks on a research snapshot, each recorded separately.

Per claim citation: CITATION_PRESENT, DOCUMENT_ACQUIRED, QUOTE_VERIFIED (re-checked against the
stored chunk text, not trusted from the run), CITATION_LINKED, METADATA, AUTHORITY,
SEMANTIC_SUPPORT. Per final value: TRACEABLE (claims or calculation inputs exist and resolve).
Per reviewer correction: CORRECTION_DOCUMENTED.

A verified quote shows the claim is grounded in what was acquired; it never shows the value is
right. Semantic support is only PASS when the value itself is in the quote (the run's own
VALUE_IN_QUOTE check, re-verified here); otherwise it needs a person.
"""

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit
from water_source_research.llm.extraction import normalize_quote
from water_source_research.schemas.research import FieldDefinition
from water_source_research.services.normalization import OFFICIAL_SOURCE_TYPES, quote_in
from .snapshot import raw_claims

# Hosts whose TLD makes an official (government/utility/regulator) label doubtful.
SUSPICIOUS_TLDS = frozenset({"name", "info", "biz", "blog", "wiki", "site", "online", "xyz",
                             "top", "club"})
SUSPICIOUS_HOST_WORDS = ("blog", "wiki", "news", "forum", "medium", "substack")

def check_evidence(snap: dict[str, Any], fields: Mapping[str, FieldDefinition]
                   ) -> list[dict[str, Any]]:
    chunks = {c["chunk_id"]: c for c in snap["chunks"]}
    chunk_norm = {cid: normalize_quote(c["text"]) for cid, c in chunks.items()}
    by_url: dict[str, list[str]] = {}
    for cid, c in chunks.items():
        by_url.setdefault(c["url"], []).append(cid)
    docs = {d["document_id"]: d for d in snap["documents"]}
    docs_by_url = {}
    for d in snap["documents"]:
        for u in (d.get("final_url"), d.get("requested_url"), d.get("canonical_url")):
            if u:
                docs_by_url.setdefault(u, d)
    out: list[dict[str, Any]] = []

    def add(claim, idx, check, result, detail=None, **links):
        out.append({"claim_uid": claim["claim_uid"], "field_key": claim["field_key"],
                    "citation_index": idx, "check": check, "result": result,
                    "detail": detail, "links": links})

    for claim in raw_claims(snap) + [c for c in snap["claims"]
                                     if c.get("review_status") == "CORRECTED"]:
        d = fields.get(claim["field_key"])
        cites = claim.get("citations") or []
        add(claim, None, "CITATION_PRESENT", "PASS" if cites else "FAIL",
            f"{len(cites)} citation(s)")
        validation = claim.get("validation") or {}
        value_in_quote = next((k["result"] for k in validation.get("checks", [])
                               if k["code"] == "VALUE_IN_QUOTE"), None)
        for i, cite in enumerate(cites):
            doc = docs.get(cite.get("document_id")) or docs_by_url.get(cite["url"])
            links = {"url": cite["url"], "chunk_id": cite.get("chunk_id"),
                     "document_id": doc["document_id"] if doc else None}
            if claim.get("review_status") == "CORRECTED" and doc is None:
                add(claim, i, "DOCUMENT_ACQUIRED", "N/A",
                    "reviewer-supplied source (not acquired by the run)", **links)
            else:
                ok = doc is not None and doc.get("status") == "OK"
                add(claim, i, "DOCUMENT_ACQUIRED", "PASS" if ok else "FAIL",
                    doc.get("status") if doc else "no document record", **links)
            candidates = [cite["chunk_id"]] if cite.get("chunk_id") in chunk_norm \
                else by_url.get(cite["url"], [])
            found = next((cid for cid in candidates
                          if quote_in(chunk_norm[cid], cite.get("quote") or "")), None)
            if not candidates:
                add(claim, i, "QUOTE_VERIFIED", "FAIL", "no stored text for this source",
                    **links)
            else:
                add(claim, i, "QUOTE_VERIFIED", "PASS" if found else "FAIL",
                    None if found else "quote not found in the stored chunks",
                    **{**links, "chunk_id": found or links["chunk_id"]})
            add(claim, i, "CITATION_LINKED", "PASS" if cite.get("chunk_id") else "FAIL",
                None if cite.get("chunk_id") else "citation not linked to a chunk", **links)
            missing = [k for k in ("title", "retrieved_at", "source_date")
                       if not cite.get(k) and not (doc or {}).get(k)]
            add(claim, i, "METADATA", "PASS" if not missing else "WARN",
                f"missing {', '.join(missing)}" if missing else None, **links)
            stype = cite.get("source_type") or (doc or {}).get("source_type")
            official = set((d.official_sources if d else []) or OFFICIAL_SOURCE_TYPES)
            add(claim, i, "AUTHORITY", "PASS" if stype in official else "WARN",
                f"source type {stype}", **links)
            classification = suspicious_official(cite["url"], stype)
            if classification:
                add(claim, i, "SOURCE_CLASSIFICATION", "NEEDS_REVIEW", classification,
                    **links)
        add(claim, None, "SEMANTIC_SUPPORT",
            "PASS" if value_in_quote == "PASS" else "NEEDS_REVIEW",
            "the value appears in the quote" if value_in_quote == "PASS"
            else "whether the quote supports this value for this field needs a person")
    out += _final_traceability(snap)
    out += _corrections(snap, chunk_norm)
    return out

def suspicious_official(url: str, source_type: str | None) -> str | None:
    """Why an official source label looks doubtful (or None). A heuristic: flagged for a
    person, never treated as established."""
    if source_type not in OFFICIAL_SOURCE_TYPES:
        return None
    host = (urlsplit(url).hostname or "").casefold()
    tld = host.rsplit(".", 1)[-1] if "." in host else ""
    if tld in SUSPICIOUS_TLDS:
        return f"{host} labelled {source_type} but its .{tld} domain is not typically official"
    if any(w in host for w in SUSPICIOUS_HOST_WORDS):
        return f"{host} labelled {source_type} but looks like a blog/news/wiki host"
    return None

def _final_traceability(snap: dict[str, Any]) -> list[dict[str, Any]]:
    """Every known final value names claims that exist, or a calculation whose inputs do."""
    records = snap["records"]
    if not records.get("records"):
        return []
    claim_uids = {c["claim_uid"] for c in snap["claims"] if c.get("claim_uid")}
    out = []
    values = list(records.get("city_values", {}).values()) + [
        v for row in records.get("record_values", {}).values() for v in row.values()]
    for v in values:
        if v.get("value_status") != "KNOWN" or v["field_key"] == "references":
            continue
        uids = list(v.get("claim_uids") or [])
        calc = v.get("calculation") or {}
        inputs = [u for i in calc.get("inputs", []) for u in i.get("claim_uids", [])]
        if v.get("resolution_status") == "FROM_CITY_CATALOG":
            result, detail = "PASS", "from the city catalog the run started with"
        elif uids or inputs:
            unknown = [u for u in uids + inputs if u not in claim_uids]
            result = "FAIL" if unknown else "PASS"
            detail = f"unknown claims {unknown}" if unknown else (
                f"{calc.get('rule')} from {len(inputs)} input claim(s)" if calc
                else f"{len(uids)} claim(s)")
        elif calc:
            result, detail = "WARN", f"{calc.get('rule')} without input claims"
        else:
            result, detail = "FAIL", "no claims and no calculation"
        out.append({"claim_uid": None, "field_key": v["field_key"], "citation_index": None,
                    "check": "TRACEABLE", "result": result, "detail": detail,
                    "links": {"record_key": v.get("record_key"), "claim_uids": uids,
                              "input_claim_uids": inputs}})
    return out

def _corrections(snap: dict[str, Any], chunk_norm: dict[str, str]) -> list[dict[str, Any]]:
    """Reviewer corrections: a reason and evidence (verified quote, or a supplied source)."""
    out = []
    for d in snap["review_decisions"]:
        if d["decision"] != "CORRECT":
            continue
        ev = d.get("evidence") or {}
        if not d.get("reason"):
            result, detail = "FAIL", "no reason recorded"
        elif ev.get("chunk_id"):
            ok = quote_in(chunk_norm.get(ev["chunk_id"], ""), ev.get("quote") or "")
            result = "PASS" if ok else "FAIL"
            detail = "quote verified in the run's evidence" if ok else "quote not in chunk"
        elif ev.get("url"):
            result, detail = "WARN", "reviewer-supplied source (not machine-verified)"
        else:
            result, detail = "FAIL", "no supporting evidence"
        out.append({"claim_uid": None, "field_key": d["field_key"], "citation_index": None,
                    "check": "CORRECTION_DOCUMENTED", "result": result, "detail": detail,
                    "links": {"decision_id": d["id"], "reviewer": d["reviewer"],
                              "evidence": ev}})
    return out
