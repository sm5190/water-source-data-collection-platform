"""City catalog (config/cities.yaml), generated from the project workbook by
scripts/build_city_catalog.py. Used by the UI dropdown and the API."""

from functools import lru_cache
from pathlib import Path
import yaml
from water_source_research.core.paths import CONFIG_DIR
from pydantic import BaseModel, Field

DEFAULT_CATALOG_PATH = CONFIG_DIR / "cities.yaml"

class CatalogCity(BaseModel):
    city: str
    country: str
    jurisdiction: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    aliases: list[str] = Field(default_factory=list)
    source: str | None = None

    @property
    def label(self) -> str:
        return f"{self.city}, {self.country}"

@lru_cache
def load_city_catalog(path: Path = DEFAULT_CATALOG_PATH) -> tuple[CatalogCity, ...]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return tuple(CatalogCity.model_validate(c) for c in data["cities"])
