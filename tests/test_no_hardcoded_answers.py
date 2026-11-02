"""Research code and configuration name no evaluated city's utility or utility host: those
appear only as test expectations (and in evaluation-only configuration). The registry and
discovery must find them from evidence."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Utilities and hosts of the saved runs' cities (expectations in tests only).
ANSWERS = re.compile(r"aguas\s*andinas|almar\s*water|almarwater|\bEMOS\b|houston\s*public\s*"
                     r"works|houstonpublicworks|houston\s*water|nairobi\s*city\s*water|"
                     r"nairobiwater|wiener\s*wasser|wienwasser|empresa\s+p[uú]blica\s+de\s+"
                     r"[aá]guas\s+de\s+luanda|\bepal\b", re.IGNORECASE)
EVALUATION_ONLY = {"evaluation.yaml", "evaluation_aliases.yaml", "field_descriptions.yaml"}

def test_no_research_file_names_an_evaluated_citys_utility():
    hits = []
    for path in [*(ROOT / "src").rglob("*.py"), *(ROOT / "config").rglob("*.yaml")]:
        if path.name in EVALUATION_ONLY or "evaluation" in path.parts:
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if ANSWERS.search(line):
                hits.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()[:80]}")
    assert hits == []
