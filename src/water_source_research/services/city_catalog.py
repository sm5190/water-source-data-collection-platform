"""City catalog (config/cities.yaml), generated from the project workbook by
scripts/build_city_catalog.py: which cities are studied (city, country, catalog aliases). It
holds no region or coordinates: those are manual research answers in the workbook. The city's
region and other names come from config/city_identity.yaml (services/city_identity.py)."""

from functools import lru_cache
from pathlib import Path
import yaml
from water_source_research.core.paths import CONFIG_DIR
from pydantic import BaseModel, Field

DEFAULT_CATALOG_PATH = CONFIG_DIR / "cities.yaml"

class CatalogCity(BaseModel):
    model_config = {"extra": "forbid"}  # identity only: no workbook answers can creep in

    city: str
    country: str
    aliases: list[str] = Field(default_factory=list)
    source: str | None = None

    @property
    def label(self) -> str:
        return f"{self.city}, {self.country}"

@lru_cache
def load_city_catalog(path: Path = DEFAULT_CATALOG_PATH) -> tuple[CatalogCity, ...]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return tuple(CatalogCity.model_validate(c) for c in data["cities"])

def catalog_entry(city: str | None, country: str | None,
                  catalog: tuple[CatalogCity, ...] | None = None) -> CatalogCity | None:
    """The approved catalog entry for a run's city (name or alias, and country, ignoring case
    and surrounding whitespace), or None when the city isn't in the catalog. Only the identity
    (city and country) is taken from it; its other columns are never used as research data."""
    if not city or not country:
        return None
    want_city, want_country = city.strip().casefold(), country.strip().casefold()
    for c in catalog if catalog is not None else load_city_catalog():
        names = {c.city.casefold(), *(a.casefold() for a in c.aliases)}
        if want_city in names and c.country.casefold() == want_country:
            return c
    return None
