from water_source_research.schemas.standardized import StandardizedWaterSourceRow

def test_minimal_row():
    row = StandardizedWaterSourceRow(
        city_name="Toronto",
        country="Canada",
        utility_name="Toronto Water",
        water_source="Lake Ontario",
        water_type="Surface",
    )
    assert row.water_source == "Lake Ontario"
