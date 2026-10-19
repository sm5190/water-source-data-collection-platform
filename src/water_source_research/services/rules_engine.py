from water_source_research.schemas.common import ValueStatus

def percentage(numerator: float | None, denominator: float | None, decimals: int = 3) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return round((numerator / denominator) * 100, decimals)

def population_served(city_population_millions: float, water_percentage: float) -> float:
    return city_population_millions * (water_percentage / 100.0)

def days_in_year(year: int | None) -> float:
    """Days used to convert an annual volume (methodology: 365/366, or 365.25 if unknown)."""
    if year is None:
        return 365.25
    leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
    return 366.0 if leap else 365.0

def annual_to_daily(volume_m3_year: float, year_days: float = 365.25) -> float:
    if year_days <= 0:
        raise ValueError("year_days must be positive")
    return volume_m3_year / year_days

def export_missing(status: ValueStatus) -> str:
    if status is ValueStatus.UNKNOWN:
        return "Unknown"
    if status is ValueStatus.NOT_APPLICABLE:
        return "N/A"
    raise ValueError("KNOWN values should export their typed value")
