"""Versioned evaluation policies: how reference values are normalized and how predictions are
compared. Evaluation results always name the policy versions they used, so re-evaluating a
research run under new rules creates new results instead of rewriting old ones.

Defaults live here; ``config/evaluation.yaml`` may override tolerances and thresholds per field.
"""

from functools import lru_cache
from pathlib import Path
from typing import Any
import yaml
from pydantic import BaseModel, Field
from water_source_research.core.paths import CONFIG_DIR

# Bump when reference import/normalization behaviour changes (part of the dataset version).
REFERENCE_POLICY_VERSION = "reference-1"
# Bump when comparison, evidence, diagnosis or metric rules change.
COMPARE_POLICY_VERSION = "compare-5"

EVALUATION_CONFIG = CONFIG_DIR / "evaluation.yaml"
ALIASES_CONFIG = CONFIG_DIR / "evaluation_aliases.yaml"

class ComparePolicy(BaseModel):
    version: str = COMPARE_POLICY_VERSION
    numeric_tolerance: float = 0.02  # relative
    field_tolerance: dict[str, float] = Field(default_factory=dict)  # per field override
    city_point_km: float = 25.0  # city centre coordinates
    pod_point_km: float = 2.0  # point of diversion
    # Reference cells that are labels, not values (e.g. the workbook's "HYBAS ID").
    placeholders: list[str] = Field(default_factory=lambda: ["HYBAS ID"])
    # Claim validation codes that mean "a different measurement/scope", not a wrong number.
    scope_flags: list[str] = Field(default_factory=lambda: [
        "GEOGRAPHIC_SCOPE", "MEASUREMENT_BASIS", "APPROXIMATE_VALUE"])
    aliases: dict[str, list[str]] = Field(default_factory=dict)  # canonical -> aliases

    def tolerance(self, field_key: str) -> float:
        return self.field_tolerance.get(field_key, self.numeric_tolerance)

    def point_km(self, field_key: str) -> float:
        return self.pod_point_km if field_key.startswith("pod_") else self.city_point_km

@lru_cache
def load_policy(path: Path = EVALUATION_CONFIG,
                aliases_path: Path = ALIASES_CONFIG) -> ComparePolicy:
    data: dict[str, Any] = {}
    if path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if aliases_path.exists():
        data["aliases"] = (yaml.safe_load(aliases_path.read_text(encoding="utf-8"))
                           or {}).get("aliases", {})
    return ComparePolicy(**data)

# --- reference workbook ---------------------------------------------------------------------

# Header (stripped, casefolded) -> field key. Positions resolve the workbook's duplicated
# "Population Served (millions)" header: column 6 holds the city population (the rules'
# "City Population (millions)"), column 10 the population served by the source.
HEADER_ALIASES: dict[str, str] = {
    "city name": "city_name",
    "state/province": "jurisdiction",
    "state, province, or most relevant sub-national jurisdiction": "jurisdiction",
    "country": "country",
    "city latitude": "city_latitude",
    "city longitude": "city_longitude",
    "city population (millions)": "city_population_millions",
    "city population reference date": "city_population_reference_year",
    "utility reported city population": "utility_reported_city_population_millions",
    "utility reported city population (millions)": "utility_reported_city_population_millions",
    "utility reported reference date": "utility_reported_city_population_reference_year",
    "utility reported city population reference date":
        "utility_reported_city_population_reference_year",
    "population served (millions)": "population_served_millions",
    "percentage of total population (%)": "percentage_total_population",
    "utility name": "utility_name",
    "water source": "water_source",
    "water system": "water_system",
    "water type": "water_type",
    "interbasin transfers": "water_transfer_system",
    "water transfer system": "water_transfer_system",
    "seasonal uses": "seasonal_uses",
    "reservoir name(s)": "reservoir_names",
    "pod latitude": "pod_latitude",
    "pod longitude": "pod_longitude",
    "pod coordinate method": "pod_coordinate_method",
    "infrastructure notes": "infrastructure_notes",
    "total city water withdrawals (m3/d)": "total_city_water_withdrawals_m3_day",
    "total city water withdrawals date": "total_city_water_withdrawals_year",
    "past total city water withdrawals (m3/d)": "past_total_city_water_withdrawals",
    "source water withdrawal contribution (m3/d)": "source_withdrawal_contribution_m3_day",
    "source water withdrawal contribution date": "source_withdrawal_contribution_year",
    "source water withdrawal contribution method": "source_withdrawal_contribution_method",
    "source water withdrawal percentage": "source_withdrawal_percentage",
    "source water withdrawal percentage (%)": "source_withdrawal_percentage",
    "water quality notes": "water_quality_notes",
    "water quality date": "water_quality_year",
    "references": "references",
}
# (0-based column, header) pairs whose meaning is fixed by position in the standard layout.
POSITIONAL: dict[int, str] = {5: "city_population_millions", 9: "population_served_millions"}

MISSING_TOKENS = {"unknown": "UNKNOWN", "n/a": "NOT_APPLICABLE", "na": "NOT_APPLICABLE",
                  "none reported": "NONE_REPORTED"}

# References column: "<info label>: <citation>" -> the fields that information supports.
REFERENCE_LABELS: dict[str, list[str]] = {
    "water source": ["water_source", "water_type", "water_system", "utility_name"],
    "water sources": ["water_source", "water_type", "water_system", "utility_name"],
    "water supply": ["water_source", "utility_name", "population_served_millions"],
    "infrastructure": ["infrastructure_notes", "pod_latitude", "pod_longitude",
                       "reservoir_names"],
    "water infrastructure": ["infrastructure_notes", "pod_latitude", "pod_longitude",
                             "reservoir_names"],
    "water quality": ["water_quality_notes", "water_quality_year"],
    "seasonal usage": ["seasonal_uses"],
    "seasonal water usage": ["seasonal_uses"],
    "seasonal uses": ["seasonal_uses"],
    "water contribution": ["source_withdrawal_contribution_m3_day",
                           "source_withdrawal_contribution_year",
                           "source_withdrawal_contribution_method",
                           "source_withdrawal_percentage"],
    "contribution": ["source_withdrawal_contribution_m3_day"],
    "water withdrawal": ["total_city_water_withdrawals_m3_day",
                         "total_city_water_withdrawals_year",
                         "past_total_city_water_withdrawals"],
    "water withdrawals": ["total_city_water_withdrawals_m3_day",
                          "total_city_water_withdrawals_year",
                          "past_total_city_water_withdrawals"],
    "interbasin transfers": ["water_transfer_system"],
    "reservoirs": ["reservoir_names"],
}
# Comma-separated lists in the standard (rules: "separate entries with commas").
LIST_FIELDS = frozenset({"reservoir_names", "infrastructure_notes"})
# Free text compared by concepts, never by exact wording.
DESCRIPTIVE_FIELDS = frozenset({"seasonal_uses", "infrastructure_notes", "water_quality_notes",
                                "past_total_city_water_withdrawals"})
# Named things compared by normalized name and aliases.
ENTITY_FIELDS = frozenset({"city_name", "jurisdiction", "country", "utility_name",
                           "water_source", "water_system", "water_transfer_system"})
# Percent fields and the (numerator, denominator) that define them in the same row.
PERCENT_DEFINITIONS: dict[str, tuple[str, str]] = {
    "percentage_total_population": ("population_served_millions", "city_population_millions"),
    "source_withdrawal_percentage": ("source_withdrawal_contribution_m3_day",
                                     "total_city_water_withdrawals_m3_day"),
}
