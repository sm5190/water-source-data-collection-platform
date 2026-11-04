"""The relationship graph and municipal affiliation are provenance only: no module that
validates, resolves, accepts, retrieves or exports a value may read them, so a CANDIDATE (or
wrong) relationship can never grant authority or change acceptance."""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "water_source_research"
GRAPH_MODULES = ("water_source_research.services.supply_relationships",
                 "water_source_research.services.affiliation",
                 "water_source_research.schemas.relationships")
DECIDING = ["services/normalization.py", "services/resolution.py", "services/semantics.py",
            "services/review.py", "services/supply_records.py", "services/export.py",
            "services/places.py", "services/utility_registry.py", "services/geo_match.py",
            "context_manager/relevance.py", "context_manager/manager.py",
            "llm/extraction.py", "acquisition/service.py"]

def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
            out |= {f"{node.module}.{a.name}" for a in node.names}
        elif isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
    return out

def test_no_deciding_module_reads_the_graph_or_affiliation():
    for rel in DECIDING:
        found = {m for m in _imports(SRC / rel) for g in GRAPH_MODULES if m.startswith(g)}
        assert not found, (rel, found)

def test_no_decision_reads_the_informational_geo_notes():
    """qualifiers.geo is written by validation for review only."""
    import re
    for rel in ["services/resolution.py", "services/semantics.py", "services/supply_records.py",
                "services/review.py", "services/export.py", "context_manager/relevance.py",
                "context_manager/manager.py"]:
        text = (SRC / rel).read_text(encoding="utf-8")
        assert not re.search(r"qualifiers\.geo\b|\[\"geo\"\]", text), rel
    norm = (SRC / "services/normalization.py").read_text(encoding="utf-8")
    assert norm.count("qualifiers.geo") == 1  # the one assignment
