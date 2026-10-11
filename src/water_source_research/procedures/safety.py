"""Memory-poisoning and secret-leak guards for recipes.

Recipes may not contain URLs, domains, credentials or instruction-like text. Combined with
the fact that recipes are built only from trusted events (never from document text), a page
saying "always visit attacker.com" cannot become procedure memory.
"""

import json
import re
from collections.abc import Iterator
from typing import Any
from water_source_research.schemas.events import contains_secret
from water_source_research.schemas.procedures import Recipe
from .grounding import Violation

_URL_OR_DOMAIN = re.compile(
    r"https?://|www\.|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|org|net|gov|edu|io|info|biz|int|mil|"
    r"co|us|uk|ca|au|de|fr|es|it|nl|in|ke|za|br|mx|jp|cn|ru|eu|app|dev|xyz|site|online|ly|me)\b",
    re.IGNORECASE,
)
_INSTRUCTION = re.compile(
    r"ignore (?:all|any|previous|prior)|disregard|system prompt|always (?:visit|go to|open|use "
    r"the site)|you must|do not tell",
    re.IGNORECASE,
)

def check_safety(recipe: Recipe, max_chars: int) -> list[Violation]:
    out: list[Violation] = []
    payload = recipe.model_dump(mode="json")
    if len(json.dumps(payload)) > max_chars:
        out.append(Violation("RECIPE_TOO_LARGE", f"exceeds {max_chars} characters"))
    for path, text in _strings(payload):
        if _URL_OR_DOMAIN.search(text):
            out.append(Violation("URL_OR_DOMAIN", f"{path}: URLs/domains are not allowed"))
        if contains_secret(text):
            out.append(Violation("SECRET", f"{path}: credential-like content"))
        if _INSTRUCTION.search(text):
            out.append(Violation("SUSPICIOUS_INSTRUCTION", f"{path}: instruction-like text"))
    return out

def _strings(value: Any, path: str = "") -> Iterator[tuple[str, str]]:
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _strings(k, f"{path}.{k}(key)")
            yield from _strings(v, f"{path}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _strings(v, f"{path}[{i}]")
