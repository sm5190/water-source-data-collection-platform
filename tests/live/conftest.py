"""Live tests against the REAL discovery APIs and the REAL VT ARC LLM API.

Skipped unless RUN_LIVE_TESTS=1, so `uv run pytest` stays offline:
    RUN_LIVE_TESTS=1 uv run pytest tests/live -v          (bash)
    $env:RUN_LIVE_TESTS=1; uv run pytest tests/live -v    (PowerShell)

Credentials come from .env. The LLM provider is forced to VT: these tests never call paid
OpenAI, whatever LLM_PROVIDER says. Each provider is probed once per session and its tests are
skipped with the reason when it is unreachable (VT off the VT Campus VPN, SearXNG not running,
Semantic Scholar rate-limiting keyless clients).
"""

import os
import sys
from pathlib import Path
import httpx
import pytest

LIVE = os.environ.get("RUN_LIVE_TESTS", "").strip().lower() in {"1", "true", "yes"}
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

def pytest_collection_modifyitems(config, items):
    if LIVE:
        return
    skip = pytest.mark.skip(reason="live API test; set RUN_LIVE_TESTS=1 to run")
    for item in items:
        if "live" in Path(str(item.fspath)).parts:
            item.add_marker(skip)

@pytest.fixture(scope="session")
def live_settings():
    """Real settings from .env, with the LLM forced to VT (never paid OpenAI)."""
    from water_source_research.core.config import get_settings
    settings = get_settings().model_copy(update={"llm_provider": "vt"})
    assert settings.llm_provider == "vt"
    return settings

def _probe(url: str, headers: dict | None = None) -> tuple[int | None, str]:
    try:
        r = httpx.get(url, headers=headers, timeout=30)
        return r.status_code, " ".join(r.text.split())[:160]
    except httpx.HTTPError as exc:
        return None, type(exc).__name__

@pytest.fixture(scope="session")
def vt_ready(live_settings):
    s = live_settings
    if not (s.vt_llm_base_url and s.vt_llm_api_key and s.vt_llm_model):
        pytest.skip("VT_LLM_BASE_URL / VT_LLM_API_KEY / VT_LLM_MODEL not set in .env")
    try:
        r = httpx.get(s.vt_llm_base_url.rstrip("/") + "/models",
                      headers={"Authorization": f"Bearer {s.vt_llm_api_key}"}, timeout=30)
    except httpx.HTTPError as exc:
        pytest.skip(f"VT LLM API unreachable: {type(exc).__name__}")
    if r.status_code != 200:
        body = " ".join(r.text.split())[:160].replace(s.vt_llm_api_key, "<KEY>")
        pytest.skip(f"VT LLM API unreachable ({r.status_code}): {body}")
    ids = {m.get("id") for m in r.json().get("data", [])}
    if s.vt_llm_model not in ids:
        pytest.skip(f"model {s.vt_llm_model!r} not offered by VT ({sorted(ids)})")
    return s

@pytest.fixture(scope="session")
def searxng_ready(live_settings):
    url = live_settings.searxng_url.rstrip("/")
    status, body = _probe(f"{url}/search?q=water&format=json")
    if status != 200:
        pytest.skip(f"SearXNG not reachable at {url} ({status or body}); "
                    "`docker compose --profile discovery up -d searxng`")
    return live_settings
