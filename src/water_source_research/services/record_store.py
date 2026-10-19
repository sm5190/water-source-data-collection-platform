"""Persist a run's assembled SupplyRecords and values (``db/record_models.py``), and the audit
trail of exports. The record snapshot is replaced at finalize (it is rebuilt from immutable
claims and append-only decisions, so a retried finalize writes the same rows)."""

from typing import Any
from sqlalchemy import Engine, delete, select
from sqlalchemy.orm import Session
from water_source_research.db.record_models import (
    RecordValueRecord, ResearchExportRecord, SupplyRecordRecord,
)
from water_source_research.schemas.records import RecordSet, RecordValue, SupplyRecord

class RecordRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def save(self, run_id: str, rs: RecordSet) -> int:
        """Replace the run's records and values. Returns the number of values stored."""
        values = list(rs.city_values.values()) + [
            v for row in rs.record_values.values() for v in row.values()]
        with Session(self.engine) as session, session.begin():
            session.execute(delete(RecordValueRecord).where(RecordValueRecord.run_id == run_id))
            session.execute(delete(SupplyRecordRecord).where(
                SupplyRecordRecord.run_id == run_id))
            session.add_all(SupplyRecordRecord(
                run_id=run_id, record_key=r.record_key, source_key=r.source_key[:255],
                water_source=r.water_source and r.water_source[:255],
                utility_name=r.utility_name and r.utility_name[:255], position=r.position,
                claim_uids=r.claim_uids) for r in rs.records)
            session.add_all(RecordValueRecord(
                run_id=run_id, scope=v.scope, record_key=v.record_key, field_key=v.field_key,
                value=v.value, value_status=v.value_status.value,
                value_origin=v.value_origin.value if v.value_origin else None,
                resolution_status=v.resolution_status, reference_year=v.reference_year,
                claim_uids=v.claim_uids, review_decision_ids=v.review_decision_ids,
                calculation=v.calculation.model_dump(mode="json") if v.calculation else None,
                note=v.note) for v in values)
        return len(values)

    def load(self, run_id: str) -> RecordSet:
        """Records and values as stored (quality checks/completion live on the run result)."""
        rs = RecordSet()
        with Session(self.engine) as session:
            for r in session.scalars(select(SupplyRecordRecord).where(
                    SupplyRecordRecord.run_id == run_id).order_by(SupplyRecordRecord.position)):
                rs.records.append(SupplyRecord(
                    record_key=r.record_key, source_key=r.source_key,
                    water_source=r.water_source, utility_name=r.utility_name,
                    position=r.position, claim_uids=r.claim_uids))
                rs.record_values[r.record_key] = {}
            for v in session.scalars(select(RecordValueRecord).where(
                    RecordValueRecord.run_id == run_id).order_by(RecordValueRecord.id)):
                rv = RecordValue(
                    field_key=v.field_key, scope=v.scope, record_key=v.record_key,
                    value=v.value, value_status=v.value_status, value_origin=v.value_origin,
                    resolution_status=v.resolution_status, reference_year=v.reference_year,
                    claim_uids=v.claim_uids, review_decision_ids=v.review_decision_ids,
                    calculation=v.calculation, note=v.note)
                if v.scope == "CITY":
                    rs.city_values[v.field_key] = rv
                else:
                    rs.record_values.setdefault(v.record_key, {})[v.field_key] = rv
        return rs

    def record_export(self, run_id: str, kind: str, fmt: str, sha256: str, rows: int,
                      byte_size: int, completion: str | None,
                      requested_by: str | None) -> dict[str, Any]:
        with Session(self.engine) as session, session.begin():
            row = ResearchExportRecord(run_id=run_id, kind=kind, format=fmt, sha256=sha256,
                                       rows=rows, byte_size=byte_size, completion=completion,
                                       requested_by=requested_by and requested_by[:128])
            session.add(row)
            session.flush()
            return _export_dict(row)

    def exports(self, run_id: str) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            return [_export_dict(r) for r in session.scalars(select(ResearchExportRecord).where(
                ResearchExportRecord.run_id == run_id).order_by(ResearchExportRecord.id))]

def _export_dict(r: ResearchExportRecord) -> dict[str, Any]:
    return {"id": r.id, "run_id": r.run_id, "kind": r.kind, "format": r.format,
            "sha256": r.sha256, "rows": r.rows, "byte_size": r.byte_size,
            "completion": r.completion, "requested_by": r.requested_by,
            "created_at": r.created_at.isoformat() if r.created_at else None}
