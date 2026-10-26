"""Standardized CSV/XLSX export of a finalized run, plus the provenance (audit) export.

Standardized: the 32 workbook columns, one row per SupplyRecord, cells formatted per the
Standardization Rules (numbers only, percentages to 1/3 decimals, coordinates to 4, "Unknown" /
"N/A", never blank). Provenance: one row per exported value and supporting citation (claim,
quote, chunk, document, URL), or its calculation and inputs, plus review decision ids — what
doesn't fit in the standardized columns. XLSX holds both, the quality checks and the review log.

A run is exportable only when it SUCCEEDED and its completion isn't INCOMPLETE (no open
conflict or unreviewed field is ever dropped silently).
"""

import csv
import hashlib
import io
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from water_source_research.schemas.records import QualityCheck, RecordSet, RecordValue
from water_source_research.schemas.standardized import COLUMNS
from .supply_records import DECIMALS, typed_row

PROVENANCE_COLUMNS = [
    "row", "water_source", "column", "field_key", "scope", "export_value", "value_status",
    "value_origin", "resolution_status", "reference_year", "claim_uid", "claim_value",
    "claim_validation", "evidence_class", "citation_url", "citation_title", "page_number", "quote", "chunk_id",
    "document_id", "source_type", "source_date", "calculation_rule", "calculation_formula",
    "calculation_inputs", "review_decision_ids", "note",
]
MEDIA = {"csv": "text/csv; charset=utf-8",
         "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}

class ExportBlocked(Exception):
    """The run can't be exported (not finished, or scientifically incomplete)."""

@dataclass
class ExportFile:
    content: bytes
    filename: str
    media_type: str
    rows: int
    sha256: str

def check_exportable(status: str, completion: str | None) -> None:
    if status != "SUCCEEDED":
        raise ExportBlocked(f"run is {status}: only finished runs can be exported")
    if completion is None:
        raise ExportBlocked("run finished before records were assembled; reprocess it")
    if completion == "INCOMPLETE":
        raise ExportBlocked("run is INCOMPLETE (open fields or failed quality checks); "
                            "resolve them before exporting")

# --- cells ----------------------------------------------------------------------------------

def format_cell(key: str, value: Any) -> str:
    """By column, not by Python type (JSON storage may turn 100.0 into 100)."""
    if isinstance(value, str):
        return value
    if isinstance(value, bool) or value is None:
        return "Unknown"
    f = float(value)
    if key in DECIMALS:
        return f"{f:.{DECIMALS[key]}f}"
    if key.endswith("_year"):
        return str(int(f))
    if key.endswith("_m3_day"):
        return str(round(f)) if f >= 100 else f"{round(f, 3):g}"
    if key.endswith("_millions"):
        return f"{round(f, 3):g}"
    return f"{f:g}"

def standardized_rows(rs: RecordSet) -> list[dict[str, str]]:
    rows = []
    for rec in sorted(rs.records, key=lambda r: r.position):
        typed = typed_row(rs, rec.record_key)
        rows.append({header: format_cell(key, typed[key]) for key, header in COLUMNS})
    return rows

def provenance_rows(rs: RecordSet, claims: Mapping[str, Mapping[str, Any]]) -> list[dict]:
    """``claims``: claim_uid -> stored claim with citations (EvidenceRepository.claims)."""
    out: list[dict[str, Any]] = []
    for n, rec in enumerate(sorted(rs.records, key=lambda r: r.position), start=1):
        typed = typed_row(rs, rec.record_key)
        for key, header in COLUMNS:
            rv = rs.value(key, rec.record_key)
            if rv is None or (rv.scope == "CITY" and n > 1):
                continue  # city values once (they repeat on every row of the export)
            out += _cell_rows(n if rv.scope == "RECORD" else "city", rec.water_source, header,
                              rv, format_cell(key, typed[key]), claims)
    return out

def _cell_rows(row: Any, source: str | None, header: str, rv: RecordValue, export_value: str,
               claims: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    calc = rv.calculation
    base = {"row": row, "water_source": source or "Unknown", "column": header,
            "field_key": rv.field_key, "scope": rv.scope, "export_value": export_value,
            "value_status": rv.value_status.value,
            "value_origin": rv.value_origin.value if rv.value_origin else "",
            "resolution_status": rv.resolution_status, "reference_year": rv.reference_year or "",
            "calculation_rule": calc.rule if calc else "",
            "calculation_formula": calc.formula if calc else "",
            "calculation_inputs": json.dumps([i.model_dump(mode="json") for i in calc.inputs])
            if calc else "",
            "review_decision_ids": ",".join(map(str, rv.review_decision_ids)),
            "note": rv.note or ""}
    uids = list(rv.claim_uids) + [u for i in (calc.inputs if calc else []) for u in i.claim_uids]
    rows = []
    for uid in dict.fromkeys(uids):
        claim = claims.get(uid)
        if claim is None:
            rows.append({**base, "claim_uid": uid})
            continue
        for cite in claim.get("citations") or [{}]:
            rows.append({**base, "claim_uid": uid, "claim_value": json.dumps(claim["value"]),
                         "claim_validation": claim.get("validation_outcome") or "",
                         "evidence_class": (claim.get("qualifiers") or {}).get(
                             "evidence_class") or "",
                         "citation_url": cite.get("url", ""),
                         "citation_title": cite.get("title") or "",
                         "page_number": cite.get("page_number") or "",
                         "quote": cite.get("quote", ""), "chunk_id": cite.get("chunk_id") or "",
                         "document_id": cite.get("document_id") or "",
                         "source_type": cite.get("source_type") or "",
                         "source_date": cite.get("source_date") or ""})
    return rows or [base]

# --- files ----------------------------------------------------------------------------------

def to_csv(columns: list[str], rows: list[dict[str, Any]]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8-sig")  # BOM: Excel reads the UTF-8 names correctly

def to_xlsx(sheets: list[tuple[str, list[str], list[dict[str, Any]]]]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    wb.remove(wb.active)
    for title, columns, rows in sheets:
        ws = wb.create_sheet(title)
        ws.append(columns)
        for cell in ws[1]:
            cell.font = Font(bold=True)
        for row in rows:
            ws.append([_xlsx_value(row.get(c, "")) for c in columns])
        ws.freeze_panes = "A2"
    fixed = datetime(2000, 1, 1)  # noqa: DTZ001 - openpyxl document properties are naive
    wb.properties.created = wb.properties.modified = fixed
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()

def _xlsx_value(v: Any) -> Any:
    return v if isinstance(v, (int, float, str)) else json.dumps(v)

def build_export(rs: RecordSet, quality: list[QualityCheck],
                 claims: Mapping[str, Mapping[str, Any]], decisions: list[dict[str, Any]],
                 city: str, run_id: str, fmt: str, kind: str = "standardized") -> ExportFile:
    if fmt not in MEDIA:
        raise ValueError(f"unknown format {fmt!r} (csv or xlsx)")
    standard = standardized_rows(rs)
    headers = [h for _, h in COLUMNS]
    if kind == "provenance":
        rows = provenance_rows(rs, claims)
        content = to_csv(PROVENANCE_COLUMNS, rows) if fmt == "csv" else to_xlsx(
            [("Provenance", PROVENANCE_COLUMNS, rows)])
        count = len(rows)
    elif fmt == "csv":
        content, count = to_csv(headers, standard), len(standard)
    else:
        q_cols = ["code", "result", "record_key", "field_key", "detail"]
        d_cols = ["id", "round", "created_at", "reviewer", "field_key", "entity_key", "decision",
                  "original_value", "reviewed_value", "value_origin", "reason",
                  "correction_category", "evidence", "supersedes_id"]
        content = to_xlsx([
            ("Standardization", headers, standard),
            ("Provenance", PROVENANCE_COLUMNS, provenance_rows(rs, claims)),
            ("Quality control", q_cols, [c.model_dump(mode="json") for c in quality]),
            ("Review log", d_cols, decisions)])
        count = len(standard)
    slug = "".join(ch if ch.isalnum() else "_" for ch in city).strip("_") or "city"
    name = f"{slug}_{run_id[:8]}_{kind}.{fmt}"
    return ExportFile(content, name, MEDIA[fmt], count, hashlib.sha256(content).hexdigest())
