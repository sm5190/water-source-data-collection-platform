from typing import Literal
from pydantic import BaseModel, Field

WaterType = Literal["Surface", "Groundwater", "Reclaimed", "Desalination"]

class StandardizedWaterSourceRow(BaseModel):
    city_name: str
    jurisdiction: str | None = None
    country: str
    city_latitude: float | None = Field(default=None, ge=-90, le=90)
    city_longitude: float | None = Field(default=None, ge=-180, le=180)
    city_population_millions: float | None = None
    city_population_reference_year: int | None = None
    utility_reported_city_population_millions: float | None = None
    utility_reported_city_population_reference_year: int | None = None
    population_served_millions: float | None = None
    percentage_total_population: float | None = None
    utility_name: str
    water_source: str
    water_system: str | None = None
    water_type: WaterType
    water_transfer_system: str | None = None
    seasonal_uses: str | None = None
    reservoir_names: list[str] = Field(default_factory=list)
    pod_latitude: float | None = Field(default=None, ge=-90, le=90)
    pod_longitude: float | None = Field(default=None, ge=-180, le=180)
    pod_coordinate_method: str | None = None
    infrastructure_notes: list[str] = Field(default_factory=list)
    total_city_water_withdrawals_m3_day: float | None = None
    total_city_water_withdrawals_year: int | None = None
    past_total_city_water_withdrawals: list[tuple[int, float]] = Field(default_factory=list)
    source_withdrawal_contribution_m3_day: float | None = None
    source_withdrawal_contribution_year: int | None = None
    source_withdrawal_contribution_method: str | None = None
    source_withdrawal_percentage: float | None = None
    water_quality_notes: str | None = None
    water_quality_year: int | None = None
    references: list[str] = Field(default_factory=list)
