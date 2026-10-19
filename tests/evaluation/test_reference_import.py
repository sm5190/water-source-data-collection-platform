"""Reference dataset import: mapping, normalization policies and issue reporting."""

import pytest
from eval_helpers import TORONTO, WORKBOOK, row, write_workbook
from procedure_helpers import make_engine
from water_source_research.evaluation.reference import import_reference, parse_references
from water_source_research.evaluation.store import EvaluationStore

def codes(ds):
    return [i.code for i in ds.issues]

def test_standard_layout_maps_all_32_fields_with_its_quirks(tmp_path):
    ds = import_reference(write_workbook(tmp_path / "ref.xlsx", [TORONTO]))
    assert len(set(ds.column_map.values())) == 32
    assert ds.column_map["4:City Latitude "] == "city_latitude"
    assert ds.column_map["6:Population Served (millions)"] == "city_population_millions"
    assert "HEADER_BY_POSITION" in codes(ds)  # column 6 is the city population
    [city] = ds.cities
    assert city.value("city_population_millions").normalized == 3.273
    assert city.value("population_served_millions", "lake ontario").normalized == 3.273
    assert "MISSING_COLUMN" not in codes(ds) and "UNMAPPED_COLUMN" not in codes(ds)

def test_raw_values_are_kept_next_to_normalized_ones(tmp_path):
    ds = import_reference(write_workbook(tmp_path / "ref.xlsx", [TORONTO]))
    city = ds.cities[0]
    year = city.value("city_population_reference_year")
    assert (year.raw, year.normalized, year.unit) == (2024.0, 2024, "year")
    total = city.value("total_city_water_withdrawals_m3_day")
    assert total.unit == "m3/d" and total.reference_year == 2024  # paired date column
    pct = city.value("percentage_total_population", "lake ontario")
    assert pct.raw == 1.0 and pct.normalized == 100.0 and "FRACTION_PERCENT" in pct.issues
    assert city.value("source_withdrawal_percentage", "lake ontario").issues == []

def test_missing_value_conventions_and_placeholders(tmp_path):
    ds = import_reference(write_workbook(tmp_path / "ref.xlsx", [
        row(source_withdrawal_contribution_m3_day=0, water_quality_notes="Unknown")]))
    city = ds.cities[0]
    assert city.value("water_transfer_system", "lake ontario").value_status == "NOT_APPLICABLE"
    assert city.value("past_total_city_water_withdrawals").value_status == "UNKNOWN"
    assert city.value("water_system", "lake ontario").value_status == "PLACEHOLDER"
    zero = city.value("source_withdrawal_contribution_m3_day", "lake ontario")
    assert zero.value_status == "KNOWN" and zero.normalized == 0  # a real zero
    assert city.value("water_quality_notes", "lake ontario").value_status == "UNKNOWN"

def test_fraction_percent_needs_the_rows_own_inputs(tmp_path):
    ds = import_reference(write_workbook(tmp_path / "ref.xlsx", [
        row(percentage_total_population=0.5)]))  # 3.273/3.273 = 1, not 0.5
    pct = ds.cities[0].value("percentage_total_population", "lake ontario")
    assert pct.normalized == 0.5 and "PERCENT_INCONSISTENT" in pct.issues
    ds = import_reference(write_workbook(tmp_path / "ref2.xlsx", [
        row(percentage_total_population=0.5, population_served_millions="Unknown")]))
    assert "AMBIGUOUS_PERCENT" in ds.cities[0].value(
        "percentage_total_population", "lake ontario").issues

def test_multi_source_and_multi_utility_city(tmp_path):
    madrid = row(city_name="Madrid", country="Spain", utility_name="Canal de Isabel II",
                 water_source="Lozoya River")
    rows = [madrid, row(madrid, water_source="Jarama River"),
            row(madrid, water_source="Madrid Aquifer", utility_name="Private Wells")]
    ds = import_reference(write_workbook(tmp_path / "ref.xlsx", rows))
    [city] = ds.cities
    assert [(r.record_key, r.utility) for r in city.records] == [
        ("lozoya river", "Canal de Isabel II"), ("jarama river", "Canal de Isabel II"),
        ("madrid aquifer", "Private Wells")]
    assert len([v for v in city.values if v.field_key == "water_source"]) == 3
    assert len([v for v in city.values if v.field_key == "total_city_water_withdrawals_m3_day"
                ]) == 1  # city-level value once

def test_duplicates_inconsistencies_encoding_and_invalid_values(tmp_path):
    rows = [TORONTO, row(city_population_millions=3.0),
            row(water_source="Bogot� River", water_type="Lake")]
    ds = import_reference(write_workbook(tmp_path / "ref.xlsx", rows))
    found = codes(ds)
    assert "DUPLICATE" in found  # second Lake Ontario / Toronto Water row
    assert "CITY_VALUE_INCONSISTENT" in found
    assert "ENCODING" in found and "INVALID_VALUE" in found
    keys = [r.record_key for r in ds.cities[0].records]
    assert keys == ["lake ontario", "lake ontario#2", "bogot river"]

def test_city_filter_and_missing_city(tmp_path):
    ds = import_reference(write_workbook(tmp_path / "ref.xlsx", [TORONTO]),
                          cities=["Toronto", "Atlantis"])
    assert [c.city for c in ds.cities] == ["Toronto"]
    assert any(i.code == "CITY_NOT_FOUND" and i.detail == "atlantis" for i in ds.issues)

def test_references_give_source_urls_per_field():
    urls = parse_references(TORONTO["references"])
    assert urls["water sources"] == ["https://www.toronto.ca/summary-2024.pdf",
                                     "https://www.toronto.ca/tap-water/"]
    assert urls["seasonal usage"] == ["https://www.toronto.ca/tap-water/"]
    braces = parse_references("Water Sources: {Celio, M. (2007). Transfers. "
                              "https://doi.org/10.1007/x}; Seasonal Usage / Water "
                              "Contribution: {Times. https://toi.in/a}")
    assert braces["water contribution"] == ["https://toi.in/a"]
    assert braces["water sources"] == ["https://doi.org/10.1007/x"]

def test_version_is_reproducible_and_stored_once(tmp_path):
    path = write_workbook(tmp_path / "ref.xlsx", [TORONTO])
    a, b = import_reference(path), import_reference(path)
    assert a.version == b.version and a.file_sha256 == b.file_sha256
    store = EvaluationStore(make_engine(tmp_path, "eval.db"))
    assert store.save_dataset(a) and not store.save_dataset(b)
    loaded = store.load_dataset(a.version)
    assert loaded.cities[0].value("percentage_total_population", "lake ontario").raw == 1.0
    assert loaded.issues == a.issues

@pytest.mark.skipif(not WORKBOOK.exists(), reason="project workbook not in resources/")
def test_project_workbook_toronto_row():
    ds = import_reference(WORKBOOK, ["Toronto"])
    [city] = ds.cities
    assert (city.city, city.country) == ("Toronto", "Canada")
    assert [r.water_source for r in city.records] == ["Lake Ontario"]
    assert city.value("total_city_water_withdrawals_m3_day").normalized == 1159745.0
    assert city.value("percentage_total_population", "lake ontario").normalized == 100.0
    assert city.value("water_system", "lake ontario").value_status == "PLACEHOLDER"
    assert any("Summary-Report-2024" in u for u in
               city.value("water_source", "lake ontario").source_urls)
