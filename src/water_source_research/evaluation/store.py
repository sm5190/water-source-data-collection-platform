"""Persistence of evaluation data (``db/evaluation_models.py``). Writes only eval_* tables."""

from typing import Any
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session
from water_source_research.db.evaluation_models import (
    EvalReferenceDatasetRecord, EvalReferenceValueRecord,
)
from .reference import ReferenceCity, ReferenceDataset, ReferenceRecord, ReferenceValue

class EvaluationStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    # --- reference datasets -----------------------------------------------------------------

    def save_dataset(self, ds: ReferenceDataset) -> bool:
        """Store a dataset version; an existing version (same file, sheet and policy) is kept
        as is (its cities are added if this import covers more). Returns True if new."""
        with Session(self.engine) as session, session.begin():
            row = session.get(EvalReferenceDatasetRecord, ds.version)
            created = row is None
            if row is None:
                row = EvalReferenceDatasetRecord(
                    id=ds.version, name=ds.name[:255], filename=ds.filename[:512],
                    file_sha256=ds.file_sha256, sheet=ds.sheet,
                    policy_version=ds.policy_version, cities=[], column_map=ds.column_map,
                    issues=[])
                session.add(row)
            known = {c["city_key"] for c in row.cities}
            new = [c for c in ds.cities if c.city_key not in known]
            row.cities = list(row.cities) + [
                {"city_key": c.city_key, "city": c.city, "country": c.country,
                 "records": [r.model_dump(mode="json") for r in c.records]} for c in new]
            seen_issues = {(i.get("code"), i.get("row"), i.get("field_key"))
                           for i in row.issues}
            row.issues = list(row.issues) + [
                i.model_dump(mode="json") for i in ds.issues
                if (i.code, i.row, i.field_key) not in seen_issues]
            for c in new:
                session.add_all(EvalReferenceValueRecord(
                    dataset_id=ds.version, city_key=c.city_key, scope=v.scope,
                    record_key=v.record_key[:255], field_key=v.field_key, raw=v.raw,
                    normalized=v.normalized, value_status=v.value_status, unit=v.unit,
                    reference_year=v.reference_year, source_urls=v.source_urls,
                    issues=v.issues, row=v.row) for v in c.values)
        return created

    def load_dataset(self, version: str) -> ReferenceDataset | None:
        with Session(self.engine) as session:
            row = session.get(EvalReferenceDatasetRecord, version)
            if row is None:
                return None
            values = session.scalars(select(EvalReferenceValueRecord).where(
                EvalReferenceValueRecord.dataset_id == version)
                .order_by(EvalReferenceValueRecord.id)).all()
            cities = []
            for c in row.cities:
                cities.append(ReferenceCity(
                    city_key=c["city_key"], city=c["city"], country=c.get("country"),
                    records=[ReferenceRecord.model_validate(r) for r in c["records"]],
                    values=[_value(v) for v in values if v.city_key == c["city_key"]]))
            return ReferenceDataset(
                version=row.id, name=row.name, filename=row.filename,
                file_sha256=row.file_sha256, sheet=row.sheet,
                policy_version=row.policy_version,
                imported_at=row.imported_at.isoformat() if row.imported_at else "",
                column_map=row.column_map, cities=cities, issues=row.issues)

    def datasets(self) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            rows = session.scalars(select(EvalReferenceDatasetRecord)
                                   .order_by(EvalReferenceDatasetRecord.imported_at.desc()))
            return [{"id": r.id, "name": r.name, "filename": r.filename,
                     "file_sha256": r.file_sha256, "sheet": r.sheet,
                     "policy_version": r.policy_version,
                     "cities": [c["city"] for c in r.cities], "issues": len(r.issues),
                     "imported_at": r.imported_at.isoformat() if r.imported_at else None}
                    for r in rows]

    def latest_dataset_with(self, city: str, country: str | None) -> str | None:
        """Most recent dataset version containing ``city``."""
        from .reference import city_key
        key = city_key(city, country)
        for d in self._dataset_rows():
            if any(c["city_key"] == key for c in d.cities):
                return d.id
        return None

    def _dataset_rows(self) -> list[EvalReferenceDatasetRecord]:
        with Session(self.engine) as session:
            return list(session.scalars(select(EvalReferenceDatasetRecord)
                                        .order_by(EvalReferenceDatasetRecord.imported_at.desc())))

def _value(v: EvalReferenceValueRecord) -> ReferenceValue:
    return ReferenceValue(field_key=v.field_key, scope=v.scope, record_key=v.record_key,
                          raw=v.raw, normalized=v.normalized, value_status=v.value_status,
                          unit=v.unit, reference_year=v.reference_year,
                          source_urls=v.source_urls, issues=v.issues, row=v.row)
