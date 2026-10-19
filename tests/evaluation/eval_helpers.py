"""Synthetic reference workbooks in the standard layout (the project workbook's
"Standardization" header, including its quirks: the 6th column is headed "Population Served
(millions)" but holds the city population, and "City Latitude " has a trailing space)."""

from pathlib import Path
from typing import Any
from water_source_research.schemas.standardized import COLUMNS

WORKBOOK = Path(__file__).resolve().parents[2] / "resources" / "Sarina Water Source.xlsx"

def workbook_header() -> list[str]:
    header = [h for _, h in COLUMNS]
    header[3] = "City Latitude "
    header[5] = "Population Served (millions)"
    header[7] = "Utility Reported City Population"
    header[15] = "Interbasin Transfers"
    return header

TORONTO: dict[str, Any] = {
    "city_name": "Toronto", "jurisdiction": "Ontario", "country": "Canada",
    "city_latitude": 43.5632, "city_longitude": -79.3832, "city_population_millions": 3.273,
    "city_population_reference_year": 2024.0,
    "utility_reported_city_population_millions": 3.273,
    "utility_reported_city_population_reference_year": 2025.0,
    "population_served_millions": 3.273, "percentage_total_population": 1.0,
    "utility_name": "Toronto Water", "water_source": "Lake Ontario", "water_system": "HYBAS ID",
    "water_type": "Surface", "water_transfer_system": "N/A",
    "seasonal_uses": "Peak Demand Periods", "reservoir_names": "N/A",
    "pod_latitude": 43.6678, "pod_longitude": -79.274,
    "pod_coordinate_method": "Visual Estimation",
    "infrastructure_notes": "lake intake, intake pipe, treatment plant, pumping station",
    "total_city_water_withdrawals_m3_day": 1159745.0,
    "total_city_water_withdrawals_year": 2024.0,
    "past_total_city_water_withdrawals": "Unknown",
    "source_withdrawal_contribution_m3_day": 1159745.0,
    "source_withdrawal_contribution_year": 2024.0,
    "source_withdrawal_contribution_method": "Reported Daily",
    "source_withdrawal_percentage": 100.0,
    "water_quality_notes": "freshwater lake source, offshore intakes, turbidity monitoring",
    "water_quality_year": 2024.0,
    "references": "Water Sources / Infrastructure / Water Quality: Toronto Government. (2024). "
                  "Summary Report 2024. [https://www.toronto.ca/summary-2024.pdf]"
                  "(https://www.toronto.ca/summary-2024.pdf); Water Sources / Seasonal Usage: "
                  "Toronto Government. (2026). Tap Water in Toronto. "
                  "https://www.toronto.ca/tap-water/",
}

def row(base: dict[str, Any] | None = None, **changes: Any) -> dict[str, Any]:
    return {**(base or TORONTO), **changes}

def write_workbook(path: Path, rows: list[dict[str, Any]], sheet: str = "Standardization",
                   header: list[str] | None = None) -> Path:
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet
    ws.append(header or workbook_header())
    for r in rows:
        ws.append([r.get(key) for key, _ in COLUMNS])
    wb.save(path)
    return path
