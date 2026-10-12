"""A deterministic Toronto 'world' for vertical-slice tests: mocked discovery results, a
mocked website (HTML + PDF), and a mocked VT endpoint that answers like a model would by
quoting the evidence it was shown. The answers are test data, not extraction logic."""

import json
import re
from typing import Any
import httpx2
from acquisition_helpers import Site, html_page, make_acquisition, make_pdf
from discovery_helpers import SETTINGS, FakeCrossref, FakeProvider, web
from llm_helpers import Server, chat, chat_json, make_llm, vt_settings
from water_source_research.core.versions import EXTRACTION_PROMPT_VERSION
from water_source_research.discovery.service import DiscoveryOutcome, DiscoveryService
from water_source_research.llm.extraction import StructuredExtractionGateway
from water_source_research.services.field_state import initialize_city_state, unresolved_fields

TARGETS = ["utility_name", "water_source", "total_city_water_withdrawals_m3_day"]
UTIL_URL = "https://www.toronto.ca/services-payments/water-environment/tap-water-in-toronto/"
PDF_URL = "https://www.toronto.ca/wp-content/uploads/2024/water-annual-report-2023.pdf"
BLOG_URL = "https://blog.example.com/toronto-hikes"
DOWN_URL = "https://down.example.org/toronto-water"

UTIL_SENTENCE = ("Toronto Water is the City of Toronto division that provides drinking water "
                 "to residents and businesses in Toronto.")
SOURCE_SENTENCE = ("Toronto draws its drinking water from Lake Ontario, and the Toronto Island "
                   "community is also served by groundwater wells.")
WITHDRAWAL_SENTENCE = ("In 2023 the Toronto Water treatment plants produced an average of "
                       "980,000 cubic metres of drinking water per day.")

def utility_page() -> bytes:
    return html_page("Tap water in Toronto - City of Toronto", UTIL_SENTENCE, SOURCE_SENTENCE,
                     "Water is treated at four treatment plants before distribution.")

def report_pdf() -> bytes:
    return make_pdf([["Toronto Water Annual Report 2023", "Prepared by Toronto Water."],
                     ["Section 2: Production", WITHDRAWAL_SENTENCE]],
                    title="Toronto Water 2023 Annual Report", mod_date="D:20240315000000Z")

def blog_page() -> bytes:
    return html_page("Best hikes near Toronto", "Our favourite hiking trails near Toronto "
                     "include ravines and parks with great views of the city skyline.")

def make_site() -> Site:
    return (Site().html(UTIL_URL, utility_page()).pdf(PDF_URL, report_pdf())
            .html(BLOG_URL, blog_page()).status(DOWN_URL, 500))

def search_results() -> list:
    return [web("Tap water in Toronto - City of Toronto", UTIL_URL, rank=1),
            web("Toronto Water Annual Report 2023", PDF_URL, rank=2),
            web("Best hikes near Toronto", BLOG_URL, rank=3),
            web("Toronto water information", DOWN_URL, rank=4),
            web("Tap water in Toronto (tracking link)", UTIL_URL + "?utm_source=news", rank=5)]

def make_discovery(results=None) -> DiscoveryService:
    return DiscoveryService(SETTINGS, web=[FakeProvider("searxng", results or search_results())],
                            scholarly=[], crossref=FakeCrossref({}))

class RoundDiscovery:
    """Discovery that returns a different candidate list each round (for rediscovery)."""

    def __init__(self, rounds: list[list]) -> None:
        self.rounds = rounds
        self.settings = SETTINGS
        self.calls = 0

    async def discover(self, queries, *, city, country) -> DiscoveryOutcome:
        found = self.rounds[min(self.calls, len(self.rounds) - 1)]
        self.calls += 1
        return DiscoveryOutcome(candidates=list(found), calls=[],
                                provider_status={"searxng": "ok"})

# --- mocked VT model ------------------------------------------------------------------

ANSWERS: dict[str, list[dict[str, Any]]] = {
    "utility_name": [{"value": "Toronto Water", "quote": "Toronto Water is the City of Toronto "
                      "division that provides drinking water", "confidence": 0.9}],
    "water_source": [
        {"value": "Lake Ontario", "quote": "Toronto draws its drinking water from Lake Ontario",
         "confidence": 0.9},
        {"value": "Groundwater wells (Toronto Island)",
         "quote": "the Toronto Island community is also served by groundwater wells",
         "confidence": 0.7}],
    "total_city_water_withdrawals_m3_day": [
        {"value": 980000, "quote": "produced an average of 980,000 cubic metres of drinking "
                                   "water per day", "confidence": 0.85}],
}

_TARGET = re.compile(r"Target field: (\w+)")
_SOURCE = re.compile(r"^\[(S\d+)\] ", re.MULTILINE)

def source_blocks(prompt: str) -> dict[str, str]:
    evidence = prompt.split("## Evidence", 1)[-1].split("## Instructions", 1)[0]
    marks = list(_SOURCE.finditer(evidence))
    return {m.group(1): evidence[m.start():(marks[i + 1].start() if i + 1 < len(marks)
                                            else len(evidence))]
            for i, m in enumerate(marks)}

class ModelScript:
    """Answers each extraction request by citing the source whose passage holds the quote.

    ``answers`` maps field -> claims; ``override`` (field -> callable(prompt) -> response)
    lets a test return malformed or hallucinated output for chosen fields."""

    def __init__(self, answers=None, override=None) -> None:
        self.answers = ANSWERS if answers is None else answers
        self.override = override or {}
        self.fields: list[str] = []
        self.prompts: list[str] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        # The first user message is the extraction prompt (later ones are repair turns).
        prompt = next(m["content"] for m in body["messages"] if m["role"] == "user")
        field = _TARGET.search(prompt).group(1)
        self.fields.append(field)
        self.prompts.append(prompt)
        if field in self.override:
            return self.override[field](prompt)
        blocks = source_blocks(prompt)
        claims = []
        for a in self.answers.get(field, []):
            sid = next((s for s, text in blocks.items()
                        if " ".join(a["quote"].split()) in " ".join(text.split())), None)
            if sid is None:
                continue
            claims.append({"value": a["value"], "value_status": "KNOWN",
                           "value_origin": "REPORTED", "confidence": a["confidence"],
                           "citations": [{"source_id": sid, "quote": a["quote"]}]})
        return chat_json({"claims": claims})

def malformed(_prompt: str) -> httpx2.Response:
    return chat("Sure! The utility is Toronto Water.")  # not JSON

def make_gateway(script: ModelScript, settings=None, **llm):
    server = Server(script)
    llm_client = make_llm(server, settings or vt_settings(**llm), task="extract",
                          prompt_version=EXTRACTION_PROMPT_VERSION)
    return StructuredExtractionGateway(llm_client), server

def initial_state(fields=TARGETS, run_id="run-toronto", **extra):
    research = initialize_city_state("Toronto", "Canada")
    return {"run_id": run_id, "city_name": "Toronto", "country": "Canada",
            "research_state": research.model_dump(mode="json"),
            "unresolved_fields": unresolved_fields(research, fields), **extra}

__all__ = ["make_acquisition"]
