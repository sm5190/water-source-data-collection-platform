"""Build a city's SupplyRecords from its resolved fields, apply the methodology's derived-field
rules, and run the final quality checks (Water Source Standardization Rules).

assemble:  one record per accepted natural water source (``water_source`` entities), its
           utility, and the value of every source-level field for that source; city-level
           values once. Unresolved / not-researched values are explicit, never blank.
derive:    total-withdrawal year and history, source contributions (reported, single-source
           total, remainder allocation, the methodology's 0 when nothing can be estimated),
           contribution method/year, withdrawal and population percentages, population
           served, References. Each calculated value keeps its formula and input claims.
quality:   open fields, one source per row, schema, percentage consistency, contributions vs
           total, explained estimates, cited URLs in References -> completion status.
"""

import hashlib
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit
from pydantic import ValidationError
from water_source_research.schemas.common import (
    FINAL_STATUSES, FieldResolutionStatus, ValueOrigin, ValueStatus,
)
from water_source_research.schemas.records import (
    Calculation, CalculationInput, QualityCheck, RecordSet, RecordValue, SupplyRecord,
)
from water_source_research.schemas.research import (
    CityResearchState, FieldClaim, FieldDefinition, FieldState,
)
from water_source_research.schemas.standardized import (
    COLUMNS, HEADERS, StandardizedWaterSourceRow,
)
from .resolution import ALL_RECORDS, CITY, ENTITY_FIELD, name_key
from .rules_engine import percentage, population_served

S = FieldResolutionStatus
OPEN = {S.CONFLICT.value, S.HITL_REQUIRED.value, S.UNRESOLVED.value, S.CANDIDATE.value}
UNKNOWN_SOURCE = "unknown"
TOTAL = "total_city_water_withdrawals_m3_day"
TOTAL_YEAR = "total_city_water_withdrawals_year"
CONTRIB = "source_withdrawal_contribution_m3_day"
CONTRIB_YEAR = "source_withdrawal_contribution_year"
CONTRIB_METHOD = "source_withdrawal_contribution_method"
# Sources common to every row are not listed in References (rules, field 32).
NOT_IN_REFERENCES = frozenset({"city_name", "jurisdiction", "country", "city_latitude",
                               "city_longitude", "city_population_millions",
                               "city_population_reference_year"})
DECIMALS = {"percentage_total_population": 1, "source_withdrawal_percentage": 3,
            "city_latitude": 4, "city_longitude": 4, "pod_latitude": 4, "pod_longitude": 4}

def build_record_set(research: CityResearchState, fields: Mapping[str, FieldDefinition],
                     researched: list[str] | None = None) -> RecordSet:
    rs = assemble(research, fields, researched)
    derive(rs, research, fields, researched)
    check_quality(rs, research, fields)
    return rs

def record_key(city: str, country: str | None, source_key: str) -> str:
    raw = "\x1f".join([name_key(city), name_key(country or ""), source_key])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]

# --- assemble -------------------------------------------------------------------------------

def assemble(research: CityResearchState, fields: Mapping[str, FieldDefinition],
             researched: list[str] | None = None) -> RecordSet:
    scope = set(researched) if researched is not None else None
    rs = RecordSet()
    ws = research.fields.get(ENTITY_FIELD)
    sources = _accepted_sources(ws)
    for n, (source_key, label, uids) in enumerate(sources or [(UNKNOWN_SOURCE, None, [])]):
        rs.records.append(SupplyRecord(
            record_key=record_key(research.city_name, research.country, source_key),
            source_key=source_key, water_source=label, utility_name=None, position=n,
            claim_uids=uids))

    for key, d in fields.items():
        if d.derived or d.generated:
            continue
        fs = research.fields.get(key) or FieldState(field_key=key)
        if d.scope in ("city", "time_series"):
            rs.city_values[key] = _value(fs, d, [CITY, ALL_RECORDS], "CITY", "", scope)
            continue
        for rec in rs.records:
            rv = _value(fs, d, [rec.source_key, ALL_RECORDS], "RECORD", rec.record_key, scope)
            rs.record_values.setdefault(rec.record_key, {})[key] = rv
    for rec in rs.records:
        utility = rs.record_values[rec.record_key].get("utility_name")
        if utility is not None and utility.known:
            rec.utility_name = str(utility.value)
    # The run's own city/country name stands in when not researched (the city catalog name).
    for key, name in (("city_name", research.city_name), ("country", research.country)):
        rv = rs.city_values.get(key)
        if name and rv is not None and not rv.known:
            rs.city_values[key] = rv.model_copy(update={
                "value": name, "value_status": ValueStatus.KNOWN,
                "value_origin": ValueOrigin.REPORTED, "resolution_status": "FROM_CITY_CATALOG",
                "note": "from the city catalog the run was started with"})
    return rs

def _accepted_sources(ws: FieldState | None) -> list[tuple[str, str, list[str]]]:
    """(source key, label, establishing claims) of every accepted water source."""
    if ws is None:
        return []
    out = []
    if ws.resolution is not None:
        for e in ws.resolution.entities:
            if e.status is S.RESOLVED and e.entity_key not in ("unattributed", ALL_RECORDS):
                claims = [c for c in (ws.claim(u) for u in e.selected_claim_uids) if c]
                label = str(claims[0].comparable_value) if claims else (e.label or e.entity_key)
                out.append((name_key(label), label, e.selected_claim_uids))
    elif ws.status is S.RESOLVED and ws.selected_claim is not None:
        label = str(ws.selected_claim.comparable_value)
        out.append((name_key(label), label, [ws.selected_claim.claim_uid or ""]))
    return sorted(out, key=lambda s: s[1].casefold())

def _value(fs: FieldState, d: FieldDefinition, entity_keys: list[str], scope: str,
           rkey: str, researched: set[str] | None) -> RecordValue:
    base: dict[str, Any] = {"field_key": d.key, "scope": scope, "record_key": rkey}
    if d.key == ENTITY_FIELD and rkey and entity_keys[0] != UNKNOWN_SOURCE:
        entity_keys = [entity_keys[0]]
    if researched is not None and d.key not in researched and not fs.candidate_claims \
            and fs.status not in FINAL_STATUSES:
        return RecordValue(**base, resolution_status="NOT_RESEARCHED")
    entities = fs.resolution.entities if fs.resolution else []
    if entities:
        entity = next((e for k in entity_keys for e in entities if e.entity_key == k), None)
        if entity is None:
            status = fs.status.value if fs.status.value in OPEN else "NO_VALUE_FOR_RECORD"
            return RecordValue(**base, resolution_status=status)
        ids = [entity.review_decision_id] if entity.review_decision_id is not None else []
        if entity.status is S.RESOLVED:
            claims = [c for c in (fs.claim(u) for u in entity.selected_claim_uids) if c]
            return _from_claims(base, d, claims, ids, entity.note)
        return _missing(base, entity.status, ids, entity.note)
    if fs.status is S.RESOLVED and fs.selected_claim is not None:
        return _from_claims(base, d, [fs.selected_claim], [], None)
    if fs.status is S.UNRESOLVED and not fs.candidate_claims:
        # Researched, nothing found: a gap ("Unknown"), not a decision waiting to be made.
        return RecordValue(**base, resolution_status="NO_EVIDENCE")
    return _missing(base, fs.status, [], None)

def _from_claims(base: dict[str, Any], d: FieldDefinition, claims: list[FieldClaim],
                 ids: list[int], note: str | None) -> RecordValue:
    if not claims:
        return RecordValue(**base, resolution_status="RESOLVED_WITHOUT_CLAIM")
    first = claims[0]
    if d.multi_valued:
        value: Any = ", ".join(dict.fromkeys(str(c.comparable_value) for c in claims))
    else:
        value = first.comparable_value
    return RecordValue(**base, value=value, value_status=ValueStatus.KNOWN,
                       value_origin=first.value_origin, resolution_status=S.RESOLVED.value,
                       reference_year=first.qualifiers.reference_year if first.qualifiers
                       else None, claim_uids=[c.claim_uid or "" for c in claims],
                       review_decision_ids=ids, note=note)

def _missing(base: dict[str, Any], status: FieldResolutionStatus, ids: list[int],
             note: str | None) -> RecordValue:
    value_status = ValueStatus.NOT_APPLICABLE if status is S.NOT_APPLICABLE \
        else ValueStatus.UNKNOWN
    return RecordValue(**base, value_status=value_status, resolution_status=status.value,
                       review_decision_ids=ids, note=note)

# --- derive ---------------------------------------------------------------------------------

def derive(rs: RecordSet, research: CityResearchState, fields: Mapping[str, FieldDefinition],
           researched: list[str] | None = None) -> None:
    claims = {c.claim_uid: c for fs in research.fields.values() for c in fs.candidate_claims
              if c.claim_uid}
    _total_year_and_history(rs, research, claims)
    for rec in rs.records:
        for key in ("population_served_millions", "percentage_total_population",
                    "source_withdrawal_percentage", "references"):
            rs.record_values[rec.record_key].setdefault(key, RecordValue(
                field_key=key, scope="RECORD", record_key=rec.record_key,
                resolution_status="DERIVED"))
    _contributions(rs, claims, researched)
    for rec in rs.records:
        _percentages(rs, rec.record_key)
        _seasonal_default(rs, rec.record_key)
        _references(rs, rec.record_key, claims)

def _set(rs: RecordSet, key: str, rkey: str, value: Any, origin: ValueOrigin, rule: str,
         formula: str, inputs: list[RecordValue], year: int | None = None,
         note: str | None = None) -> RecordValue:
    rv = RecordValue(
        field_key=key, scope="CITY" if not rkey else "RECORD", record_key=rkey, value=value,
        value_status=ValueStatus.KNOWN, value_origin=origin, resolution_status="DERIVED",
        reference_year=year, note=note,
        calculation=Calculation(rule=rule, formula=formula, inputs=[
            CalculationInput(field_key=i.field_key, record_key=i.record_key, value=i.value,
                             claim_uids=i.claim_uids) for i in inputs]))
    if rkey:
        rs.record_values[rkey][key] = rv
    else:
        rs.city_values[key] = rv
    return rv

def _open_for_derivation(rv: RecordValue | None) -> bool:
    """Derived values only fill gaps: never over a value, an N/A or an open field. (They may
    fill a field the run didn't research: they come from evidence of the fields it did.)"""
    return rv is None or (not rv.known and rv.value_status is not ValueStatus.NOT_APPLICABLE
                          and rv.resolution_status not in OPEN)

def _total_year_and_history(rs: RecordSet, research: CityResearchState,
                            claims: dict[str, FieldClaim]) -> None:
    total = rs.city_values.get(TOTAL)
    if total is None or not total.known:
        return
    claim = claims.get(total.claim_uids[0]) if total.claim_uids else None
    q = claim.qualifiers if claim else None
    if _open_for_derivation(rs.city_values.get(TOTAL_YEAR)) and q and q.reference_year \
            and q.year_basis in ("quote", "passage"):
        _set(rs, TOTAL_YEAR, "", q.reference_year, ValueOrigin.REPORTED,
             "REFERENCE_YEAR_OF_TOTAL", "reference year stated with the total withdrawal",
             [total], q.reference_year)
    past = rs.city_values.get("past_total_city_water_withdrawals")
    fs = research.fields.get(TOTAL)
    if fs is None or fs.resolution is None or not _open_for_derivation(past):
        return
    history = [claims[u] for e in fs.resolution.entities for u in e.historical_claim_uids
               if u in claims]
    pairs = sorted({(c.qualifiers.reference_year, c.comparable_value) for c in history
                    if c.qualifiers and c.qualifiers.reference_year}, reverse=True)
    if pairs:
        inputs = [RecordValue(field_key=TOTAL, scope="CITY", value=c.comparable_value,
                              resolution_status="HISTORY", claim_uids=[c.claim_uid or ""])
                  for c in history]
        _set(rs, "past_total_city_water_withdrawals", "",
             "; ".join(f"{y}: {_number(v)}" for y, v in pairs), ValueOrigin.REPORTED,
             "SERIES_FROM_EARLIER_YEARS", "earlier-year total withdrawals, most recent first",
             inputs)

def _contributions(rs: RecordSet, claims: dict[str, FieldClaim],
                   researched: list[str] | None) -> None:
    total = rs.city_values.get(TOTAL)
    total_year = rs.city_values.get(TOTAL_YEAR)
    year = total_year.value if total_year is not None and total_year.known else (
        total.reference_year if total is not None else None)
    values = {r.record_key: rs.record_values[r.record_key] for r in rs.records}
    known = {k: v[CONTRIB] for k, v in values.items() if v.get(CONTRIB) and v[CONTRIB].known}
    gaps = [k for k, v in values.items() if _open_for_derivation(v.get(CONTRIB))]
    annual = False
    if total is not None and total.known and total.claim_uids:
        c = claims.get(total.claim_uids[0])
        annual = bool(c and c.qualifiers and c.qualifiers.measurement_basis == "annual")
    total_method = "Reported Annually" if annual else "Reported Daily"

    if total is not None and total.known and gaps:
        if len(values) == 1:
            [k] = gaps
            _set(rs, CONTRIB, k, total.value, ValueOrigin.ALLOCATED, "SINGLE_SOURCE_TOTAL",
                 "single source: contribution = total city withdrawal", [total], year)
            _set(rs, CONTRIB_METHOD, k, total_method, ValueOrigin.ALLOCATED,
                 "METHOD_OF_TOTAL", "method of the total withdrawal it equals", [total])
            gaps = []
        elif len(gaps) == 1 and known:
            [k] = gaps
            remainder = float(total.value) - sum(float(v.value) for v in known.values())
            if remainder >= 0:
                _set(rs, CONTRIB, k, round(remainder, 3), ValueOrigin.ALLOCATED,
                     "REMAINDER_ALLOCATED", "total withdrawal - sum of reported contributions",
                     [total, *known.values()], year)
                _set(rs, CONTRIB_METHOD, k, "Remainder Allocated", ValueOrigin.ALLOCATED,
                     "REMAINDER_ALLOCATED", "the contribution was allocated", [total])
                gaps = []
    # Nothing left to estimate from: the rules report 0 with method "Unknown" (and the
    # percentage stays Unknown) — only once the contribution was actually researched.
    researched_contrib = researched is None or CONTRIB in researched
    for k in gaps:
        if not researched_contrib or values[k][CONTRIB].resolution_status == "NOT_RESEARCHED":
            continue
        _set(rs, CONTRIB, k, 0, ValueOrigin.ESTIMATED, "NOT_ESTIMABLE_ZERO",
             "contribution unknown and not estimable: reported as 0 (standardization rule 26)",
             [], note="0 by rule, not a measurement; percentage left Unknown")
        if _open_for_derivation(values[k].get(CONTRIB_METHOD)):
            _set(rs, CONTRIB_METHOD, k, "Unknown", ValueOrigin.ESTIMATED, "NOT_ESTIMABLE_ZERO",
                 "contribution could not be estimated", [])

    for k, v in values.items():
        contrib = v.get(CONTRIB)
        if contrib is None or not contrib.known:
            continue
        calculated = contrib.calculation is not None
        if _open_for_derivation(v.get(CONTRIB_METHOD)) and not calculated:
            c = claims.get(contrib.claim_uids[0]) if contrib.claim_uids else None
            basis = c.qualifiers.measurement_basis if c and c.qualifiers else None
            _set(rs, CONTRIB_METHOD, k,
                 "Reported Annually" if basis == "annual" else "Reported Daily",
                 ValueOrigin.REPORTED, "METHOD_FROM_CLAIM",
                 "annual volume converted to daily" if basis == "annual"
                 else "daily volume as reported", [contrib])
        if _open_for_derivation(v.get(CONTRIB_YEAR)):
            if calculated and contrib.calculation.rule != "NOT_ESTIMABLE_ZERO" and year:
                _set(rs, CONTRIB_YEAR, k, year, ValueOrigin.ALLOCATED, "YEAR_OF_TOTAL",
                     "calculated contribution: year of the total withdrawal (rule 27)",
                     [total] if total else [])
            elif not calculated and contrib.reference_year:
                _set(rs, CONTRIB_YEAR, k, contrib.reference_year, ValueOrigin.REPORTED,
                     "REFERENCE_YEAR_OF_CONTRIBUTION", "reference year stated with the value",
                     [contrib])

def _percentages(rs: RecordSet, k: str) -> None:
    row = rs.record_values[k]
    total = rs.city_values.get(TOTAL)
    contrib = row.get(CONTRIB)
    if contrib is not None and contrib.known and total is not None and total.known \
            and float(total.value) > 0 and _open_for_derivation(row.get(
                "source_withdrawal_percentage")) and not (
            contrib.calculation and contrib.calculation.rule == "NOT_ESTIMABLE_ZERO"):
        cy = _year(rs, CONTRIB_YEAR, k) or contrib.reference_year
        ty = _year(rs, TOTAL_YEAR, "") or total.reference_year
        if cy and ty and cy != ty:
            row["source_withdrawal_percentage"].note = f"years differ ({cy} vs {ty})"
        else:
            _set(rs, "source_withdrawal_percentage", k,
                 percentage(float(contrib.value), float(total.value), 3),
                 ValueOrigin.CALCULATED, "PERCENT_OF_TOTAL_WITHDRAWAL",
                 "contribution / total city withdrawal x 100 (3 decimals)", [contrib, total],
                 cy or ty)
    pop = rs.city_values.get("city_population_millions")
    share = row.get("source_withdrawal_percentage")
    served = row.get("population_served_millions")
    if _open_for_derivation(served) and pop is not None and pop.known and share is not None \
            and share.known:
        served = _set(rs, "population_served_millions", k,
                      round(population_served(float(pop.value), float(share.value)), 3),
                      ValueOrigin.ESTIMATED, "POPULATION_FROM_WITHDRAWAL_SHARE",
                      "city population x source withdrawal percentage / 100 (rule 10)",
                      [pop, share])
    if served is not None and served.known and pop is not None and pop.known \
            and float(pop.value) > 0 and _open_for_derivation(
                row.get("percentage_total_population")):
        _set(rs, "percentage_total_population", k,
             percentage(float(served.value), float(pop.value), 1), ValueOrigin.CALCULATED,
             "PERCENT_OF_CITY_POPULATION", "population served / city population x 100",
             [served, pop])

def _seasonal_default(rs: RecordSet, k: str) -> None:
    rv = rs.record_values[k].get("seasonal_uses")
    if rv is not None and rv.resolution_status == S.UNAVAILABLE.value:
        _set(rs, "seasonal_uses", k, "None Reported", ValueOrigin.REPORTED,
             "NONE_REPORTED", "no seasonal pattern reported (rule 18)", [rv],
             note=rv.note)

def _references(rs: RecordSet, k: str, claims: dict[str, FieldClaim]) -> None:
    """References column: every source used for the row, labelled with what it provided."""
    rv = rs.record_values[k].get("references")
    if rv is not None and (rv.known or rv.resolution_status in OPEN):
        return
    by_url: dict[str, dict[str, Any]] = {}
    uids: list[str] = []
    for key, _ in COLUMNS:
        value = rs.value(key, k)
        if value is None or key in NOT_IN_REFERENCES or key == "references":
            continue
        for field_key, uid in _sources_of(value):
            claim = claims.get(uid)
            for ref in claim.evidence if claim else []:
                entry = by_url.setdefault(ref.url, {"labels": [], "ref": ref, "claim": claim})
                if HEADERS[field_key] not in entry["labels"]:
                    entry["labels"].append(HEADERS[field_key])
            if uid not in uids:
                uids.append(uid)
    if not by_url:
        return
    parts = []
    for url, entry in by_url.items():
        ref, claim = entry["ref"], entry["claim"]
        dates = claim.qualifiers.source_dates if claim.qualifiers else []
        year = dates[0][:4] if dates and dates[0][:4].isdigit() else "n.d."
        title = ref.title or urlsplit(url).hostname or url
        retrieved = f" Retrieved {ref.retrieved_at[:10]}, from" if ref.retrieved_at else ""
        parts.append(f"{' / '.join(entry['labels'])}: {title}. ({year}).{retrieved} {url}")
    rs.record_values[k]["references"] = RecordValue(
        field_key="references", scope="RECORD", record_key=k, value="; ".join(parts),
        value_status=ValueStatus.KNOWN, value_origin=ValueOrigin.REPORTED,
        resolution_status="GENERATED", claim_uids=uids,
        calculation=Calculation(rule="REFERENCES_FROM_CITATIONS",
                                formula="citations of every value on the row, by source"),
        note="generated from document metadata (title, date, URL); authors are not known, "
             "so entries are not full APA citations")

def _sources_of(rv: RecordValue) -> list[tuple[str, str]]:
    """(field the source provided, claim uid) behind a value: its own claims, and for a
    calculated value its inputs' claims — except sources common to every row."""
    out = [(rv.field_key, u) for u in rv.claim_uids]
    for i in rv.calculation.inputs if rv.calculation else []:
        if i.field_key not in NOT_IN_REFERENCES:
            out += [(i.field_key, u) for u in i.claim_uids]
    return out

def _year(rs: RecordSet, key: str, k: str) -> int | None:
    rv = rs.value(key, k) if k else rs.city_values.get(key)
    return int(rv.value) if rv is not None and rv.known else None

def _number(v: Any) -> str:
    f = float(v)
    return str(round(f)) if f >= 100 else f"{f:g}"

# --- export rows and quality checks ---------------------------------------------------------

def typed_row(rs: RecordSet, rkey: str) -> dict[str, Any]:
    """The row's typed values: value, "Unknown" or "N/A" (numbers rounded per the rules)."""
    row: dict[str, Any] = {}
    for key, _ in COLUMNS:
        rv = rs.value(key, rkey)
        if rv is None or not rv.known:
            row[key] = "N/A" if rv is not None and \
                rv.value_status is ValueStatus.NOT_APPLICABLE else "Unknown"
            continue
        value = rv.value
        if isinstance(value, float) and key in DECIMALS:
            value = round(value, DECIMALS[key])
        if key.endswith("_year") and isinstance(value, float) and value.is_integer():
            value = int(value)
        row[key] = value
    return row

def check_quality(rs: RecordSet, research: CityResearchState,
                  fields: Mapping[str, FieldDefinition]) -> None:
    q: list[QualityCheck] = []
    claims = {c.claim_uid: c for fs in research.fields.values() for c in fs.candidate_claims
              if c.claim_uid}
    values = [(rv, rec.record_key) for rec in rs.records
              for rv in rs.record_values[rec.record_key].values()]
    values += [(rv, "") for rv in rs.city_values.values()]

    open_values = [(rv, k) for rv, k in values if rv.resolution_status in OPEN]
    for rv, k in open_values:
        q.append(QualityCheck(code="OPEN_FIELD", result="FAIL", record_key=k or None,
                              field_key=rv.field_key,
                              detail=f"{rv.field_key} is {rv.resolution_status}"))
    if not open_values:
        q.append(QualityCheck(code="OPEN_FIELD", result="PASS"))

    keys = [r.source_key for r in rs.records]
    dupes = sorted({k for k in keys if keys.count(k) > 1})
    q.append(QualityCheck(code="ONE_SOURCE_PER_ROW", result="FAIL" if dupes else "PASS",
                          detail=", ".join(dupes) or None))

    for rec in rs.records:
        try:
            StandardizedWaterSourceRow(**typed_row(rs, rec.record_key))
            q.append(QualityCheck(code="SCHEMA", result="PASS", record_key=rec.record_key))
        except ValidationError as exc:
            for err in exc.errors():
                q.append(QualityCheck(code="SCHEMA", result="FAIL", record_key=rec.record_key,
                                      field_key=str(err["loc"][0]) if err["loc"] else None,
                                      detail=err["msg"]))
        q += _row_checks(rs, rec.record_key, claims)

    total = rs.city_values.get(TOTAL)
    contribs = [rs.record_values[r.record_key].get(CONTRIB) for r in rs.records]
    measured = [float(c.value) for c in contribs if c is not None and c.known and not (
        c.calculation and c.calculation.rule == "NOT_ESTIMABLE_ZERO")]
    if total is not None and total.known and measured:
        over = sum(measured) > float(total.value) * 1.01
        q.append(QualityCheck(code="CONTRIBUTIONS_WITHIN_TOTAL", result="WARN" if over else "PASS",
                              detail=f"sum {sum(measured):g} vs total {float(total.value):g}"))

    not_researched = sorted({rv.field_key for rv, _ in values
                             if rv.resolution_status == "NOT_RESEARCHED"})
    if not_researched:
        q.append(QualityCheck(code="NOT_RESEARCHED", result="WARN",
                              detail="exported as Unknown: " + ", ".join(not_researched)))
    rs.quality = q
    if any(c.result == "FAIL" for c in q):
        rs.completion = "INCOMPLETE"
    elif any(not rv.known and rv.value_status is ValueStatus.UNKNOWN for rv, _ in values):
        rs.completion = "FINALIZED_WITH_GAPS"
    else:
        rs.completion = "FINALIZED"

def _row_checks(rs: RecordSet, k: str, claims: dict[str, FieldClaim]) -> list[QualityCheck]:
    out: list[QualityCheck] = []
    pairs = [("source_withdrawal_percentage", CONTRIB, TOTAL, 3),
             ("percentage_total_population", "population_served_millions",
              "city_population_millions", 1)]
    for key, num, den, decimals in pairs:
        pct, n, d = rs.value(key, k), rs.value(num, k), rs.value(den, k)
        if pct is None or not pct.known or n is None or d is None or not (n.known and d.known):
            continue
        expected = percentage(float(n.value), float(d.value), decimals)
        ok = expected is not None and abs(float(pct.value) - expected) <= 10 ** -decimals
        out.append(QualityCheck(code="PERCENT_CONSISTENT", result="PASS" if ok else "FAIL",
                                record_key=k, field_key=key,
                                detail=None if ok else f"{pct.value} != {expected}"))
    for key, _ in COLUMNS:
        rv = rs.value(key, k)
        if rv is None or not rv.known:
            continue
        estimated = rv.value_origin in (ValueOrigin.ESTIMATED, ValueOrigin.ALLOCATED,
                                        ValueOrigin.CALCULATED)
        if estimated and not (rv.calculation or rv.claim_uids or rv.review_decision_ids):
            out.append(QualityCheck(code="ESTIMATE_EXPLAINED", result="FAIL", record_key=k,
                                    field_key=key, detail="estimated value without a basis"))
    references = rs.value("references", k)
    text = str(references.value) if references is not None and references.known else ""
    missing = sorted({url for key, _ in COLUMNS if key not in NOT_IN_REFERENCES
                      for url in _cited_urls(rs.value(key, k), claims) if url not in text})
    out.append(QualityCheck(code="CITED_URLS_IN_REFERENCES", result="FAIL" if missing else "PASS",
                            record_key=k, detail=", ".join(missing) or None))
    return out

def _cited_urls(rv: RecordValue | None, claims: dict[str, FieldClaim]) -> list[str]:
    if rv is None or not rv.known:
        return []
    return [ref.url for _, u in _sources_of(rv) if u in claims for ref in claims[u].evidence]
