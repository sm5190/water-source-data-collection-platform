"""Import manually researched reference records (the standardized workbook, or CSV) into a
versioned reference dataset.

The workbook is a reference, not ground truth: values are kept exactly as stored (``raw``) next
to their ``normalized`` form, every normalization that changes meaning (e.g. a percentage
stored as a fraction) is recorded as an issue, and problems are reported instead of being
silently fixed or ignored. Same file + sheet + policy version = same dataset version.

One reference *record* per row (one natural water source, its utility); city-level values are
taken once per city and inconsistencies between that city's rows are reported.
"""

import csv
import hashlib
import io
import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from pydantic import BaseModel, Field
from water_source_research.schemas.research import FieldDefinition
from water_source_research.services.field_state import load_field_definitions
from water_source_research.services.normalization import close, coerce_value
from water_source_research.services.resolution import name_key
from .policy import (
    HEADER_ALIASES, LIST_FIELDS, MISSING_TOKENS, PERCENT_DEFINITIONS, POSITIONAL,
    REFERENCE_LABELS, REFERENCE_POLICY_VERSION, ComparePolicy, load_policy,
)

ValueStatus = Literal["KNOWN", "UNKNOWN", "NOT_APPLICABLE", "NONE_REPORTED", "PLACEHOLDER",
                      "BLANK"]
UNITS = {"m3/day": "m3/d", "million_people": "millions"}
_URL = re.compile(r"https?://[^\s\)\]\}>,;]+")

class ImportIssue(BaseModel):
    code: str  # MISSING_COLUMN, UNMAPPED_COLUMN, DUPLICATE, ENCODING, FRACTION_PERCENT, ...
    detail: str
    row: int | None = None  # 1-based spreadsheet row
    column: str | None = None
    field_key: str | None = None

class ReferenceValue(BaseModel):
    field_key: str
    scope: Literal["CITY", "RECORD"]
    record_key: str = ""  # "" for CITY values
    raw: Any = None  # exactly as stored
    normalized: Any = None
    value_status: ValueStatus
    unit: str | None = None
    reference_year: int | None = None
    source_urls: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    row: int | None = None

class ReferenceRecord(BaseModel):
    record_key: str  # name_key of the water source (+ "#n" for duplicates)
    water_source: str | None
    utility: str | None
    row: int
    references: dict[str, list[str]] = Field(default_factory=dict)  # info label -> urls

class ReferenceCity(BaseModel):
    city_key: str  # name_key(city)|name_key(country)
    city: str
    country: str | None
    records: list[ReferenceRecord] = Field(default_factory=list)
    values: list[ReferenceValue] = Field(default_factory=list)

    def value(self, field_key: str, record_key: str = "") -> ReferenceValue | None:
        """The record's value of a source-level field, or the city value."""
        record = next((v for v in self.values if v.field_key == field_key
                       and v.scope == "RECORD" and v.record_key == record_key), None)
        return record or next((v for v in self.values if v.field_key == field_key
                               and v.scope == "CITY"), None)

class ReferenceDataset(BaseModel):
    version: str  # sha256(file, sheet, policy version)[:24]
    name: str
    filename: str
    file_sha256: str
    sheet: str | None
    policy_version: str = REFERENCE_POLICY_VERSION
    imported_at: str
    column_map: dict[str, str] = Field(default_factory=dict)  # "column:header" -> field key
    cities: list[ReferenceCity] = Field(default_factory=list)
    issues: list[ImportIssue] = Field(default_factory=list)

    def city(self, city: str, country: str | None = None) -> ReferenceCity | None:
        key = city_key(city, country)
        exact = next((c for c in self.cities if c.city_key == key), None)
        if exact is not None or country:
            return exact
        return next((c for c in self.cities if c.city_key.split("|")[0] == name_key(city)),
                    None)

def city_key(city: str, country: str | None) -> str:
    return f"{name_key(city)}|{name_key(country or '')}"

# --- reading ----------------------------------------------------------------------------------

def read_table(path: Path, sheet: str | None = "Standardization") -> tuple[list[str],
                                                                            list[list[Any]]]:
    """(header, rows) from .xlsx (one sheet) or .csv; values as stored (data_only for xlsx)."""
    if path.suffix.lower() == ".csv":
        text = path.read_text(encoding="utf-8-sig")
        table = list(csv.reader(io.StringIO(text)))
    else:
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb[sheet] if sheet else wb.worksheets[0]
        table = [list(r) for r in ws.iter_rows(values_only=True)]
        wb.close()
    header = ["" if h is None else str(h) for h in table[0]]
    return header, table[1:]

def import_reference(path: Path, cities: Iterable[str] | None = None,
                     sheet: str | None = "Standardization", name: str | None = None,
                     fields: Mapping[str, FieldDefinition] | None = None,
                     policy: ComparePolicy | None = None) -> ReferenceDataset:
    """Import ``path``; ``cities`` limits the import to those city names (all if None)."""
    fields = fields or load_field_definitions()
    policy = policy or load_policy()
    content = path.read_bytes()
    file_sha = hashlib.sha256(content).hexdigest()
    sheet_used = None if path.suffix.lower() == ".csv" else sheet
    version = hashlib.sha256(f"{file_sha}|{sheet_used}|{REFERENCE_POLICY_VERSION}"
                             .encode()).hexdigest()[:24]
    header, rows = read_table(path, sheet_used)
    ds = ReferenceDataset(version=version, name=name or path.stem, filename=path.name,
                          file_sha256=file_sha, sheet=sheet_used,
                          imported_at=datetime.now(UTC).isoformat())
    columns = _map_columns(header, fields, ds)
    wanted = {name_key(c) for c in cities} if cities is not None else None
    by_city: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for n, row in enumerate(rows, start=2):
        values = {key: row[i] if i < len(row) else None for i, key in columns.items()}
        city = values.get("city_name")
        if city is None or str(city).strip() == "":
            continue
        if wanted is not None and name_key(str(city)) not in wanted:
            continue
        key = city_key(str(city), str(values.get("country") or ""))
        by_city.setdefault(key, []).append((n, values))
    if wanted is not None:
        found = {k.split("|")[0] for k in by_city}
        for missing in sorted(wanted - found):
            ds.issues.append(ImportIssue(code="CITY_NOT_FOUND", detail=missing))
    for key, city_rows in by_city.items():
        ds.cities.append(_import_city(key, city_rows, fields, policy, ds))
    return ds

def _map_columns(header: list[str], fields: Mapping[str, FieldDefinition],
                 ds: ReferenceDataset) -> dict[int, str]:
    columns: dict[int, str] = {}
    for i, h in enumerate(header):
        clean = " ".join(h.split()).casefold()
        if not clean:
            continue
        alias = HEADER_ALIASES.get(clean)
        key = alias
        # The population headers are ambiguous in the workbook: the standard layout decides.
        if i in POSITIONAL and alias in ("population_served_millions",
                                         "city_population_millions"):
            key = POSITIONAL[i]
            if alias != key:
                ds.issues.append(ImportIssue(
                    code="HEADER_BY_POSITION", column=h, field_key=key,
                    detail=f"column {i + 1} header {h!r} read as {key} (standard layout)"))
        if key is None or key not in fields:
            ds.issues.append(ImportIssue(code="UNMAPPED_COLUMN", column=h,
                                         detail=f"column {i + 1} has no field"))
            continue
        if key in columns.values():
            ds.issues.append(ImportIssue(code="DUPLICATE_COLUMN", column=h, field_key=key,
                                         detail=f"column {i + 1} maps to {key} again; ignored"))
            continue
        columns[i] = key
        ds.column_map[f"{i + 1}:{h}"] = key  # by position: headers may repeat
    for key in fields:
        if key not in columns.values() and not fields[key].derived:
            ds.issues.append(ImportIssue(code="MISSING_COLUMN", field_key=key,
                                         detail=f"no column for {key}"))
    return columns

# --- one city ---------------------------------------------------------------------------------

def _import_city(key: str, rows: list[tuple[int, dict[str, Any]]],
                 fields: Mapping[str, FieldDefinition], policy: ComparePolicy,
                 ds: ReferenceDataset) -> ReferenceCity:
    first_row, first = rows[0]
    city = ReferenceCity(city_key=key, city=_text(first.get("city_name")),
                         country=_text(first.get("country")) or None)
    seen: dict[str, int] = {}
    for n, values in rows:
        source = values.get("water_source")
        source_key = name_key(_text(source)) if _status(source, policy) == "KNOWN" \
            else "unknown"
        utility = _text(values.get("utility_name")) or None
        seen[source_key] = seen.get(source_key, 0) + 1
        record_key = source_key if seen[source_key] == 1 else f"{source_key}#{seen[source_key]}"
        duplicate = seen[source_key] > 1 and any(
            r.water_source and name_key(r.water_source) == source_key
            and (r.utility or "") == (utility or "") for r in city.records)
        if duplicate:
            ds.issues.append(ImportIssue(code="DUPLICATE", row=n,
                                         detail=f"{city.city}: a second row for source "
                                                f"{_text(source)!r} and utility {utility!r}"))
        elif seen[source_key] > 1:
            ds.issues.append(ImportIssue(code="AMBIGUOUS_RECORD", row=n,
                                         detail=f"{city.city}: source {_text(source)!r} appears "
                                                f"again with utility {utility!r}"))
        urls = parse_references(values.get("references"), n, ds)
        city.records.append(ReferenceRecord(record_key=record_key,
                                            water_source=_text(source) or None,
                                            utility=utility, row=n, references=urls))
        row_values = _row_values(values, fields, policy, n, ds, urls)
        for v in row_values:
            d = fields[v.field_key]
            if d.scope in ("city", "time_series"):
                if n == first_row:
                    city.values.append(v.model_copy(update={"scope": "CITY", "record_key": ""}))
                else:
                    other = next(x for x in city.values if x.field_key == v.field_key
                                 and x.scope == "CITY")
                    if other.raw != v.raw and _text(other.raw) != _text(v.raw):
                        ds.issues.append(ImportIssue(
                            code="CITY_VALUE_INCONSISTENT", row=n, field_key=v.field_key,
                            detail=f"{city.city}: {v.raw!r} vs {other.raw!r} (row "
                                   f"{first_row} used)"))
            else:
                city.values.append(v.model_copy(update={"scope": "RECORD",
                                                        "record_key": record_key}))
    return city

def _row_values(values: dict[str, Any], fields: Mapping[str, FieldDefinition],
                policy: ComparePolicy, row: int, ds: ReferenceDataset,
                urls: dict[str, list[str]]) -> list[ReferenceValue]:
    out: dict[str, ReferenceValue] = {}
    for key, raw in values.items():
        d = fields[key]
        status = _status(raw, policy)
        issues: list[str] = []
        if isinstance(raw, str) and "�" in raw:
            issues.append("ENCODING")
            ds.issues.append(ImportIssue(code="ENCODING", row=row, field_key=key,
                                         detail=f"replacement character in {raw[:60]!r}"))
        normalized: Any = None
        if status == "KNOWN":
            normalized, problems = _normalize(raw, d)
            if problems:
                issues += [f"INVALID:{p}" for p in problems]
                ds.issues.append(ImportIssue(code="INVALID_VALUE", row=row, field_key=key,
                                             detail="; ".join(problems)))
        elif status in ("UNKNOWN", "NOT_APPLICABLE", "NONE_REPORTED"):
            normalized = {"UNKNOWN": "Unknown", "NOT_APPLICABLE": "N/A",
                          "NONE_REPORTED": "None Reported"}[status]
        elif status == "PLACEHOLDER":
            issues.append("PLACEHOLDER")
        out[key] = ReferenceValue(
            field_key=key, scope="RECORD", raw=raw, normalized=normalized,
            value_status=status, unit=_unit(d), issues=issues, row=row,
            source_urls=sorted({u for label_fields, u_list in urls.items()
                                for u in u_list if key in REFERENCE_LABELS.get(
                                    label_fields, [])}))
    _attach_years(out, fields)
    _percent_policy(out, row, ds)
    return list(out.values())

def _status(raw: Any, policy: ComparePolicy) -> ValueStatus:
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return "BLANK"
    if isinstance(raw, str):
        clean = " ".join(raw.split())
        if clean.casefold() in MISSING_TOKENS:
            return MISSING_TOKENS[clean.casefold()]  # type: ignore[return-value]
        if clean.casefold() in {p.casefold() for p in policy.placeholders}:
            return "PLACEHOLDER"
    return "KNOWN"

def _normalize(raw: Any, d: FieldDefinition) -> tuple[Any, list[str]]:
    if d.key in LIST_FIELDS and isinstance(raw, str):
        items = [" ".join(p.split()) for p in raw.split(",") if p.strip()]
        return items, []
    if d.type in ("number", "percent", "coordinate", "integer") or d.allowed:
        value, problems = coerce_value(raw, d)
        return value, problems
    return " ".join(str(raw).split()), []

def _unit(d: FieldDefinition) -> str | None:
    if d.type == "percent":
        return "%"
    if d.type == "coordinate":
        return "decimal degrees"
    if d.type == "integer" and d.key.endswith("_year"):
        return "year"
    return UNITS.get(d.unit or "")

def _attach_years(out: dict[str, ReferenceValue], fields: Mapping[str, FieldDefinition]) -> None:
    """Each measured value gets the year from its paired date column."""
    for key, d in fields.items():
        if not (d.type == "integer" and key.endswith("_year")) or key not in out:
            continue
        year = out[key]
        if year.value_status != "KNOWN" or not isinstance(year.normalized, int):
            continue
        for parent in d.depends_on:
            if parent in out:
                out[parent].reference_year = year.normalized

def _percent_policy(out: dict[str, ReferenceValue], row: int, ds: ReferenceDataset) -> None:
    """A percentage stored as a fraction (e.g. 1.0 for 100%) is read as a percentage only when
    the row's own numerator and denominator confirm it; the raw value is never changed."""
    for key, (num, den) in PERCENT_DEFINITIONS.items():
        pct, n, d = out.get(key), out.get(num), out.get(den)
        if pct is None or pct.value_status != "KNOWN" or not isinstance(pct.normalized, float):
            continue
        ratio = None
        if n is not None and d is not None and n.value_status == d.value_status == "KNOWN" \
                and isinstance(n.normalized, float) and isinstance(d.normalized, float) \
                and d.normalized > 0:
            ratio = n.normalized / d.normalized
        value = pct.normalized
        if ratio is not None and close(value, ratio * 100, 0.01):
            continue  # a percentage, consistent with its inputs
        if value <= 1.0 and ratio is not None and close(value, ratio, 0.01):
            pct.normalized = round(value * 100, 6)
            pct.issues.append("FRACTION_PERCENT")
            ds.issues.append(ImportIssue(code="FRACTION_PERCENT", row=row, field_key=key,
                                         detail=f"{value} read as {pct.normalized}% "
                                                f"({num}/{den} = {ratio:.4f})"))
        elif value <= 1.0 and ratio is None:
            pct.issues.append("AMBIGUOUS_PERCENT")
            ds.issues.append(ImportIssue(code="AMBIGUOUS_PERCENT", row=row, field_key=key,
                                         detail=f"{value} may be a fraction; inputs unknown"))
        elif ratio is not None:
            pct.issues.append("PERCENT_INCONSISTENT")
            ds.issues.append(ImportIssue(code="PERCENT_INCONSISTENT", row=row, field_key=key,
                                         detail=f"{value}% vs {num}/{den} = {ratio * 100:.3f}%"))

def parse_references(raw: Any, row: int | None = None,
                     ds: ReferenceDataset | None = None) -> dict[str, list[str]]:
    """References cell -> {info label: [urls]}. Entries are '<label>: <citation>' separated
    by ';'; an entry without a known label belongs to the previous label."""
    if not isinstance(raw, str) or not raw.strip():
        return {}
    out: dict[str, list[str]] = {}
    current: list[str] = ["unlabelled"]
    for part in re.split(r";\s*(?=[A-Z{])|\n", raw):
        part = part.strip()
        if not part:
            continue
        head, sep, _ = part.partition(":")
        labels = [" ".join(x.split()).casefold() for x in head.split("/")] if sep else []
        if labels and all(lab in REFERENCE_LABELS for lab in labels) and len(head) < 80:
            current = labels
        elif labels and len(head) < 40 and not _URL.match(part) and ds is not None \
                and not head.strip().startswith(("http", "{")):
            ds.issues.append(ImportIssue(code="UNMAPPED_REFERENCE_LABEL", row=row,
                                         detail=head.strip()[:80]))
        for url in _URL.findall(part):
            for lab in current:
                urls = out.setdefault(lab, [])
                if url not in urls:
                    urls.append(url)
    return out

def _text(v: Any) -> str:
    return "" if v is None else " ".join(str(v).split())
