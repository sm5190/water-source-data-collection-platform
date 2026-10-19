"""Persistence of evaluation data (``db/evaluation_models.py``). Writes only eval_* tables."""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session
from water_source_research.db.evaluation_models import (
    EvalAdjudicationRecord, EvalDiagnosisRecord, EvalEvidenceCheckRecord, EvalFieldResultRecord,
    EvalMetricRecord, EvalReferenceDatasetRecord, EvalReferenceValueRecord, EvalRunRecord,
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

    # --- evaluation runs (immutable once COMPLETED) --------------------------------------------

    def save_run(self, research_run_id: str, origin_run_id: str, dataset_id: str,
                 city_key: str, snapshot: dict[str, Any], snapshot_sha256: str,
                 evaluation: dict[str, Any], policy_version: str,
                 config: dict[str, Any] | None = None) -> str:
        """Store one complete evaluation as a new run (never overwrites earlier ones)."""
        run_id = str(uuid4())
        with Session(self.engine) as session, session.begin():
            session.add(EvalRunRecord(
                id=run_id, research_run_id=research_run_id, origin_run_id=origin_run_id,
                dataset_id=dataset_id, city_key=city_key[:512],
                compare_policy_version=policy_version, config=config or {},
                snapshot_sha256=snapshot_sha256, snapshot=snapshot,
                stages=evaluation["stages"], status="COMPLETED", gaps=evaluation["gaps"],
                completed_at=datetime.now(UTC)))
            session.flush()
            session.add_all(EvalFieldResultRecord(
                eval_run_id=run_id, stage=r["stage"], scope=r["scope"],
                record_key=r["record_key"][:255], field_key=r["field_key"], status=r["status"],
                reference=r["reference"], predicted=r["predicted"],
                comparison=r["comparison"], links=r.get("links") or {})
                for r in evaluation["results"])
            session.add_all(EvalEvidenceCheckRecord(
                eval_run_id=run_id, claim_uid=e["claim_uid"], field_key=e["field_key"],
                citation_index=e["citation_index"], check=e["check"], result=e["result"],
                detail=e["detail"], links=e["links"]) for e in evaluation["evidence"])
            session.add_all(EvalDiagnosisRecord(
                eval_run_id=run_id, stage=d["stage"], record_key=d["record_key"][:255],
                field_key=d["field_key"], root_cause=d["root_cause"],
                earliest_stage=d["earliest_stage"], confidence=d["confidence"],
                symptoms=d["symptoms"], probes=d["probes"], explanation=d["explanation"])
                for d in evaluation["diagnoses"])
            session.add_all(EvalMetricRecord(
                eval_run_id=run_id, scope=m["scope"], scope_key=m["scope_key"][:128],
                metric=m["metric"], numerator=m["numerator"], denominator=m["denominator"],
                value=m["value"], detail=m["detail"]) for m in evaluation["metrics"])
        return run_id

    def runs(self, research_run_id: str | None = None) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            q = select(EvalRunRecord).order_by(EvalRunRecord.created_at.desc())
            if research_run_id:
                q = q.where(EvalRunRecord.research_run_id == research_run_id)
            return [_run_dict(r) for r in session.scalars(q)]

    def run(self, eval_run_id: str, with_snapshot: bool = False) -> dict[str, Any] | None:
        with Session(self.engine) as session:
            r = session.get(EvalRunRecord, eval_run_id)
            if r is None:
                return None
            out = _run_dict(r)
            if with_snapshot:
                out["snapshot"] = r.snapshot
            return out

    def field_results(self, eval_run_id: str, stage: str | None = None,
                      field_key: str | None = None) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            q = select(EvalFieldResultRecord).where(
                EvalFieldResultRecord.eval_run_id == eval_run_id)
            if stage:
                q = q.where(EvalFieldResultRecord.stage == stage)
            if field_key:
                q = q.where(EvalFieldResultRecord.field_key == field_key)
            rows = session.scalars(q.order_by(EvalFieldResultRecord.id)).all()
            return [{"id": r.id, "stage": r.stage, "scope": r.scope,
                     "record_key": r.record_key, "field_key": r.field_key,
                     "status": r.status, "reference": r.reference, "predicted": r.predicted,
                     "comparison": r.comparison, "links": r.links} for r in rows]

    def evidence_checks(self, eval_run_id: str) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            rows = session.scalars(select(EvalEvidenceCheckRecord).where(
                EvalEvidenceCheckRecord.eval_run_id == eval_run_id)
                .order_by(EvalEvidenceCheckRecord.id)).all()
            return [{"id": r.id, "claim_uid": r.claim_uid, "field_key": r.field_key,
                     "citation_index": r.citation_index, "check": r.check,
                     "result": r.result, "detail": r.detail, "links": r.links} for r in rows]

    def diagnoses(self, eval_run_id: str) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            rows = session.scalars(select(EvalDiagnosisRecord).where(
                EvalDiagnosisRecord.eval_run_id == eval_run_id)
                .order_by(EvalDiagnosisRecord.id)).all()
            return [{"id": r.id, "stage": r.stage, "record_key": r.record_key,
                     "field_key": r.field_key, "root_cause": r.root_cause,
                     "earliest_stage": r.earliest_stage, "confidence": r.confidence,
                     "symptoms": r.symptoms, "probes": r.probes,
                     "explanation": r.explanation} for r in rows]

    def metrics(self, eval_run_id: str) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            rows = session.scalars(select(EvalMetricRecord).where(
                EvalMetricRecord.eval_run_id == eval_run_id).order_by(EvalMetricRecord.id)).all()
            return [{"scope": r.scope, "scope_key": r.scope_key, "metric": r.metric,
                     "numerator": r.numerator, "denominator": r.denominator,
                     "value": r.value, "detail": r.detail} for r in rows]

    # --- adjudication (append-only; separate from research review decisions) ----------------

    def add_adjudication(self, eval_run_id: str, target: str, target_id: int, decision: str,
                         reviewer: str, status: str | None = None,
                         root_cause: str | None = None, note: str | None = None
                         ) -> dict[str, Any]:
        with Session(self.engine) as session, session.begin():
            row = EvalAdjudicationRecord(eval_run_id=eval_run_id, target=target,
                                         target_id=target_id, decision=decision,
                                         status=status, root_cause=root_cause, note=note,
                                         reviewer=reviewer[:128])
            session.add(row)
            session.flush()
            return _adjudication_dict(row)

    def adjudications(self, eval_run_id: str) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            rows = session.scalars(select(EvalAdjudicationRecord).where(
                EvalAdjudicationRecord.eval_run_id == eval_run_id)
                .order_by(EvalAdjudicationRecord.id)).all()
            return [_adjudication_dict(r) for r in rows]

def _run_dict(r: EvalRunRecord) -> dict[str, Any]:
    return {"id": r.id, "research_run_id": r.research_run_id, "origin_run_id": r.origin_run_id,
            "dataset_id": r.dataset_id, "city_key": r.city_key,
            "compare_policy_version": r.compare_policy_version, "config": r.config,
            "snapshot_sha256": r.snapshot_sha256, "stages": r.stages, "status": r.status,
            "error": r.error, "gaps": r.gaps,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "completed_at": r.completed_at.isoformat() if r.completed_at else None}

def _adjudication_dict(r: EvalAdjudicationRecord) -> dict[str, Any]:
    return {"id": r.id, "eval_run_id": r.eval_run_id, "target": r.target,
            "target_id": r.target_id, "decision": r.decision, "status": r.status,
            "root_cause": r.root_cause, "note": r.note, "reviewer": r.reviewer,
            "created_at": r.created_at.isoformat() if r.created_at else None}

def _value(v: EvalReferenceValueRecord) -> ReferenceValue:
    return ReferenceValue(field_key=v.field_key, scope=v.scope, record_key=v.record_key,
                          raw=v.raw, normalized=v.normalized, value_status=v.value_status,
                          unit=v.unit, reference_year=v.reference_year,
                          source_urls=v.source_urls, issues=v.issues, row=v.row)
