"""SupplyRecords: one city receives water from one natural water source through one utility.

Entity identity (``SupplyRecord``) is kept apart from observations (``RecordValue``). City-level
values (population, total withdrawals, ...) are stored once with scope CITY and only repeated on
every row at export, so nothing is double-counted; source-level values have scope RECORD.
Every value names the claims it came from, or, if calculated, its formula and inputs.
"""

from typing import Any, Literal
from pydantic import BaseModel, Field
from .common import ValueOrigin, ValueStatus

Completion = Literal["FINALIZED", "FINALIZED_WITH_GAPS", "INCOMPLETE"]

class SupplyRecord(BaseModel):
    record_key: str  # stable: hash(city, source key, utility key)
    source_key: str  # normalized water-source name (shared sources match across cities)
    water_source: str | None  # label; None when the city's source is unknown
    utility_name: str | None
    position: int  # row order in the export
    claim_uids: list[str] = Field(default_factory=list)  # claims that establish the record

class CalculationInput(BaseModel):
    field_key: str
    record_key: str = ""
    value: Any = None
    claim_uids: list[str] = Field(default_factory=list)

class Calculation(BaseModel):
    rule: str  # e.g. PERCENT_OF_TOTAL_WITHDRAWAL, REMAINDER_ALLOCATED
    formula: str
    inputs: list[CalculationInput] = Field(default_factory=list)

class RecordValue(BaseModel):
    field_key: str
    scope: Literal["CITY", "RECORD"]
    record_key: str = ""  # "" for CITY values
    value: Any = None
    value_status: ValueStatus = ValueStatus.UNKNOWN
    value_origin: ValueOrigin | None = None
    # Field/entity resolution status, or DERIVED / NOT_RESEARCHED / GENERATED.
    resolution_status: str
    reference_year: int | None = None
    claim_uids: list[str] = Field(default_factory=list)
    review_decision_ids: list[int] = Field(default_factory=list)
    calculation: Calculation | None = None
    note: str | None = None

    @property
    def known(self) -> bool:
        return self.value_status is ValueStatus.KNOWN and self.value is not None

class QualityCheck(BaseModel):
    code: str
    result: Literal["PASS", "WARN", "FAIL"]
    detail: str | None = None
    record_key: str | None = None
    field_key: str | None = None

class RecordSet(BaseModel):
    records: list[SupplyRecord] = Field(default_factory=list)
    city_values: dict[str, RecordValue] = Field(default_factory=dict)
    # record_key -> field_key -> value
    record_values: dict[str, dict[str, RecordValue]] = Field(default_factory=dict)
    quality: list[QualityCheck] = Field(default_factory=list)
    completion: Completion = "INCOMPLETE"

    def value(self, field_key: str, record_key: str) -> RecordValue | None:
        """The value of ``field_key`` on a row (city values apply to every row)."""
        return self.record_values.get(record_key, {}).get(field_key) \
            or self.city_values.get(field_key)
