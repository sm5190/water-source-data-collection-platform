"""The standardized water-source row: the project's 32-column export format.

Column order and headers follow the "Standardization" sheet of the project workbook (one row per
distinct natural water source). Every cell holds a typed value, "Unknown" (applies but no
reliable value) or "N/A" (does not apply) — never a blank (Water Source Standardization Rules).
"""

from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field

Missing = Literal["Unknown", "N/A"]
WaterType = Literal["Surface", "Groundwater", "Reclaimed", "Desalination"]
PodMethod = Literal["Utility Source", "Reported Database", "Visual Estimation",
                    "Pour Point Estimation", "City Epicenter"]
ContributionMethod = Literal["Reported Daily", "Reported Annually", "Remainder Allocated",
                             "Unknown"]
Text = Annotated[str, Field(min_length=1)]
Latitude = Annotated[float, Field(ge=-90, le=90)]
Longitude = Annotated[float, Field(ge=-180, le=180)]
NonNegative = Annotated[float, Field(ge=0)]
Percent = Annotated[float, Field(ge=0, le=100)]
Year = Annotated[int, Field(ge=1900, le=2100)]

# (field key, export header) in workbook order. The workbook's 6th header reads "Population
# Served (millions)" but holds the city population; the rules document's name is used here.
COLUMNS: list[tuple[str, str]] = [
    ("city_name", "City Name"),
    ("jurisdiction", "State/Province"),
    ("country", "Country"),
    ("city_latitude", "City Latitude"),
    ("city_longitude", "City Longitude"),
    ("city_population_millions", "City Population (millions)"),
    ("city_population_reference_year", "City Population Reference Date"),
    ("utility_reported_city_population_millions", "Utility Reported City Population"),
    ("utility_reported_city_population_reference_year", "Utility Reported Reference Date"),
    ("population_served_millions", "Population Served (millions)"),
    ("percentage_total_population", "Percentage of Total Population (%)"),
    ("utility_name", "Utility Name"),
    ("water_source", "Water Source"),
    ("water_system", "Water System"),
    ("water_type", "Water Type"),
    ("water_transfer_system", "Interbasin Transfers"),
    ("seasonal_uses", "Seasonal uses"),
    ("reservoir_names", "Reservoir Name(s)"),
    ("pod_latitude", "POD Latitude"),
    ("pod_longitude", "POD Longitude"),
    ("pod_coordinate_method", "POD Coordinate Method"),
    ("infrastructure_notes", "Infrastructure Notes"),
    ("total_city_water_withdrawals_m3_day", "Total City Water Withdrawals (m3/d)"),
    ("total_city_water_withdrawals_year", "Total City Water Withdrawals Date"),
    ("past_total_city_water_withdrawals", "Past Total City Water Withdrawals (m3/d)"),
    ("source_withdrawal_contribution_m3_day", "Source Water Withdrawal Contribution (m3/d)"),
    ("source_withdrawal_contribution_year", "Source Water Withdrawal Contribution Date"),
    ("source_withdrawal_contribution_method", "Source Water Withdrawal Contribution Method"),
    ("source_withdrawal_percentage", "Source Water Withdrawal Percentage"),
    ("water_quality_notes", "Water Quality Notes"),
    ("water_quality_year", "Water Quality Date"),
    ("references", "References"),
]
HEADERS = dict(COLUMNS)

class StandardizedWaterSourceRow(BaseModel):
    """One export row; validating it is the final schema check before export."""

    model_config = ConfigDict(extra="forbid")

    city_name: Text
    jurisdiction: Text | Missing
    country: Text
    city_latitude: Latitude | Missing
    city_longitude: Longitude | Missing
    city_population_millions: NonNegative | Missing
    city_population_reference_year: Year | Missing
    utility_reported_city_population_millions: NonNegative | Missing
    utility_reported_city_population_reference_year: Year | Missing
    population_served_millions: NonNegative | Missing
    percentage_total_population: Percent | Missing
    utility_name: Text | Missing
    water_source: Text | Missing
    water_system: Text | Missing
    water_type: WaterType | Missing
    water_transfer_system: Text | Missing
    seasonal_uses: Text | Missing
    reservoir_names: Text | Missing
    pod_latitude: Latitude | Missing
    pod_longitude: Longitude | Missing
    pod_coordinate_method: PodMethod | Missing
    infrastructure_notes: Text | Missing
    total_city_water_withdrawals_m3_day: NonNegative | Missing
    total_city_water_withdrawals_year: Year | Missing
    past_total_city_water_withdrawals: Text | Missing
    source_withdrawal_contribution_m3_day: NonNegative | Missing
    source_withdrawal_contribution_year: Year | Missing
    source_withdrawal_contribution_method: ContributionMethod | Missing
    source_withdrawal_percentage: Percent | Missing
    water_quality_notes: Text | Missing
    water_quality_year: Year | Missing
    references: Text | Missing
