import pytest
from pydantic import ValidationError
from water_source_research.schemas.standardized import (
    COLUMNS, HEADERS, StandardizedWaterSourceRow,
)
from water_source_research.services.field_state import load_field_definitions

def full_row(**overrides):
    row = {key: "Unknown" for key, _ in COLUMNS}
    row.update(city_name="Toronto", country="Canada", utility_name="Toronto Water",
               water_source="Lake Ontario", water_type="Surface")
    row.update(overrides)
    return row

def test_columns_follow_the_field_definitions():
    assert [k for k, _ in COLUMNS] == list(load_field_definitions())
    assert len(HEADERS) == 32
    assert HEADERS["city_population_millions"] == "City Population (millions)"

def test_minimal_row_uses_unknown_never_blank():
    row = StandardizedWaterSourceRow(**full_row(water_transfer_system="N/A"))
    assert row.water_source == "Lake Ontario" and row.pod_latitude == "Unknown"
    with pytest.raises(ValidationError):
        StandardizedWaterSourceRow(**full_row(water_system=""))
    with pytest.raises(ValidationError):
        StandardizedWaterSourceRow(**{k: v for k, v in full_row().items() if k != "references"})

@pytest.mark.parametrize("field, bad", [
    ("water_type", "Lake"), ("pod_latitude", 91.0), ("source_withdrawal_percentage", 120.0),
    ("source_withdrawal_contribution_method", "Guessed"), ("water_quality_year", 1850),
    ("total_city_water_withdrawals_m3_day", -1.0),
])
def test_values_outside_the_rules_are_rejected(field, bad):
    with pytest.raises(ValidationError):
        StandardizedWaterSourceRow(**full_row(**{field: bad}))
