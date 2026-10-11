from water_source_research.services.field_state import initialize_city_state, unresolved_fields

def test_32_fields_start_unresolved():
    state = initialize_city_state("Toronto", "Canada")
    fields = unresolved_fields(state)
    assert len(fields) == 32
    assert "water_source" in fields
    assert "references" in fields
