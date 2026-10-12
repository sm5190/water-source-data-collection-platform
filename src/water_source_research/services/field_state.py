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

def select_fields(keys: list[str] | None,
                  fields: dict[str, FieldDefinition] | None = None) -> list[str] | None:
    """Validate a field subset (order kept, duplicates dropped). None/empty = every field.

    The global schema is unchanged: a subset only narrows what a run researches."""
    if not keys:
        return None
    fields = fields if fields is not None else load_field_definitions()
    unknown = [k for k in keys if k not in fields]
    if unknown:
        raise ValueError(f"Unknown field key(s): {', '.join(unknown)}")
    return list(dict.fromkeys(keys))

def unresolved_fields(state: CityResearchState,
                      only: list[str] | None = None) -> list[str]:
    return [
        key for key, field in state.fields.items()
        if (only is None or key in only) and field.status in {
            FieldResolutionStatus.UNRESOLVED,
            FieldResolutionStatus.CONFLICT,
            FieldResolutionStatus.HITL_REQUIRED,
        }
    ]
