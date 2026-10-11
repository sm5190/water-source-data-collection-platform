from pathlib import Path
import yaml
from water_source_research.core.paths import CONFIG_DIR
from water_source_research.schemas.common import FieldResolutionStatus
from water_source_research.schemas.research import CityResearchState, FieldDefinition, FieldState

DEFAULT_FIELDS_PATH = CONFIG_DIR / "field_definitions.yaml"

def load_field_definitions(path: Path = DEFAULT_FIELDS_PATH) -> dict[str, FieldDefinition]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {f["key"]: FieldDefinition.model_validate(f) for f in data["fields"]}

def load_field_keys(path: Path = DEFAULT_FIELDS_PATH) -> list[str]:
    return list(load_field_definitions(path))

def initialize_city_state(city_name: str, country: str | None = None) -> CityResearchState:
    return CityResearchState(
        city_name=city_name,
        country=country,
        fields={
            key: FieldState(field_key=key, status=FieldResolutionStatus.UNRESOLVED)
            for key in load_field_keys()
        },
    )

def unresolved_fields(state: CityResearchState) -> list[str]:
    return [
        key for key, field in state.fields.items()
        if field.status in {
            FieldResolutionStatus.UNRESOLVED,
            FieldResolutionStatus.CONFLICT,
            FieldResolutionStatus.HITL_REQUIRED,
        }
    ]
