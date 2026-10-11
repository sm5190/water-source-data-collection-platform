import pytest
from water_source_research.schemas.common import ValueStatus
from water_source_research.services.rules_engine import (
    annual_to_daily, export_missing, percentage, population_served,
)

def test_percentage():
    assert percentage(250, 1000) == 25.0

def test_population_served():
    assert population_served(4.0, 25.0) == 1.0

def test_annual_to_daily():
    assert annual_to_daily(365.25) == pytest.approx(1.0)

def test_missing_export_semantics():
    assert export_missing(ValueStatus.UNKNOWN) == "Unknown"
    assert export_missing(ValueStatus.NOT_APPLICABLE) == "N/A"
