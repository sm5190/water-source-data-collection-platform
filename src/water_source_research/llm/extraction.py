"""The extraction gateway: READY context bundle -> evidence-grounded ``FieldClaim``s.

Implements ``LLMGateway`` on top of whichever provider the router selects. The AI is not the
source: the response schema (built per call) only admits the ``S#`` sources that carry
evidence for the target field, typed values, and the field's allowed enum values; every claim
must cite at least one source with a verbatim quote, and each quote is checked against the
passage the model was shown. Ungrounded citations are dropped, and claims left without a
grounded citation are rejected, so no unsupported value reaches field state.
"""

import logging
import re
import unicodedata
from collections.abc import Mapping
from typing import Annotated, Any, Literal
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, create_model, model_validator
from water_source_research.context_manager import ContextBundle, ContextStatus
from water_source_research.core.versions import EXTRACTION_PROMPT_VERSION
from water_source_research.observability.otel import get_tracer
from water_source_research.schemas.common import ValueOrigin, ValueStatus
from water_source_research.schemas.research import EvidenceRef, FieldClaim, FieldDefinition
from water_source_research.services.field_state import load_field_definitions
from .base import AsyncStructuredLLM, LLMError, LLMErrorKind
from .gateway import LLMGateway, UnconfiguredGateway
from .router import build_structured_llm

log = logging.getLogger(__name__)

MAX_CLAIMS = 5
MAX_CITATIONS = 5

SYSTEM_PROMPT = """\
You extract one field of a water-supply dataset for a city from the evidence you are given.

Rules:
- Use only the passages under "## Evidence". Never use outside knowledge, and never guess.
  "## Known facts" give context (e.g. which utility or source is meant); they are not evidence
  for the target field.
- Every claim cites at least one source by its id (e.g. "S1") together with a short quote
  copied verbatim, character for character, from that source's passage, that supports the
  value. Cite only the listed source ids.
- If the evidence does not state the value, return {"claims": []}. Do not return a claim
  whose value is unknown.
- Use value_status "NOT_APPLICABLE" (with value null) only when the evidence explicitly shows
  the field does not apply (e.g. the city has no water transfer system).
- Express numbers in the unit the field name implies (_millions = millions, _m3_day = cubic
  metres per day, percent = 0-100). value_origin: REPORTED when the source states the value
  as given; CALCULATED when you converted units or computed it from reported numbers;
  ESTIMATED only when the source itself calls the figure an estimate.
- Keep reference years with the value they belong to. Return several claims only when the
  sources disagree or report different years, or when the instructions say the field can
  have several distinct values.
- confidence (0-1) reflects how directly and authoritatively the quote supports the value."""

class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")

class _ClaimBase(_Strict):
    # Narrowed per field by ``response_model``.
    value: Any
    value_status: str

    @model_validator(mode="after")
    def _value_matches_status(self) -> "_ClaimBase":
        if self.value_status == ValueStatus.KNOWN and self.value is None:
            raise ValueError("value is required when value_status is KNOWN")
        if self.value_status == ValueStatus.NOT_APPLICABLE and self.value is not None:
            raise ValueError("value must be null when value_status is NOT_APPLICABLE")
        return self

# Types are built at runtime, so they go through Any for static checkers.
def _choice(values: list[str]) -> Any:
    return Literal[tuple(values)]

def _list_of(item: Any) -> Any:
    return list[item]

def value_type(field: FieldDefinition) -> Any:
    """The JSON value type for a field definition (enum -> its allowed values only)."""
    if field.allowed:
        return _choice(field.allowed)
    if field.type == "number":
        return Annotated[float, Field(ge=0, allow_inf_nan=False)]
    if field.type == "integer":
        return Annotated[int, Field(ge=0)]
    if field.type == "percent":
        return Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]
    if field.type == "coordinate":
        limit = 90 if field.key.endswith("latitude") else 180
        return Annotated[float, Field(ge=-limit, le=limit, allow_inf_nan=False)]
    return Annotated[str, StringConstraints(strip_whitespace=True, min_length=1,
                                            max_length=2000)]

def response_model(field: FieldDefinition, source_ids: list[str]) -> type[BaseModel]:
    """Response schema for one call: citations may only name ``source_ids``."""
    citation = create_model(
        "Citation", __base__=_Strict,
        source_id=(_choice(source_ids), ...),
        quote=(Annotated[str, StringConstraints(strip_whitespace=True, min_length=1,
                                                max_length=600)], ...),
    )
    claim = create_model(
        "Claim", __base__=_ClaimBase,
        value=(value_type(field) | None, ...),
        value_status=(_choice([ValueStatus.KNOWN.value, ValueStatus.NOT_APPLICABLE.value]), ...),
        value_origin=(_choice([o.value for o in ValueOrigin]), ...),
        confidence=(Annotated[float, Field(ge=0, le=1)], ...),
        citations=(Annotated[_list_of(citation), Field(min_length=1,
                                                        max_length=MAX_CITATIONS)], ...),
    )
    return create_model("ExtractionResponse", __base__=_Strict,
                        claims=(Annotated[_list_of(claim), Field(max_length=MAX_CLAIMS)], ...))

class StructuredExtractionGateway:
    def __init__(self, llm: AsyncStructuredLLM,
                 fields: Mapping[str, FieldDefinition] | None = None) -> None:
        self.llm = llm
        self.fields = fields if fields is not None else load_field_definitions()

    async def extract(self, bundle: ContextBundle, *, run_id: str,
                      field_key: str) -> list[FieldClaim]:
        if bundle.status is not ContextStatus.READY:
            return []
        passages = evidence_passages(bundle, field_key)
        if not passages:
            return []
        field = self.fields[field_key]
        with get_tracer().start_as_current_span("llm.extract") as span:
            span.set_attribute("extraction.run_id", run_id)
            span.set_attribute("extraction.field_key", field_key)
            span.set_attribute("extraction.prompt_version", EXTRACTION_PROMPT_VERSION)
            span.set_attribute("extraction.sources", len(passages))
            schema = response_model(field, sorted(passages, key=_source_order))
            try:
                response = await self.llm.astructured(
                    SYSTEM_PROMPT, user_prompt(bundle, field, passages), schema)
            except LLMError as exc:
                if exc.kind is not LLMErrorKind.SCHEMA:
                    raise  # transient/config errors fail the run; the queue retries it
                # The model never produced valid output: treat as "no claim", so the field
                # goes back to discovery / human review instead of failing the whole run.
                log.warning("extraction for %s in run %s gave no valid output: %s",
                            field_key, run_id, exc)
                span.set_attribute("extraction.schema_failed", True)
                return []
            raw_claims = response.model_dump()["claims"]
            claims, rejected_citations, rejected_claims = ground_claims(
                bundle, field_key, raw_claims, passages)
            span.set_attribute("extraction.claims_returned", len(raw_claims))
            span.set_attribute("extraction.claims_accepted", len(claims))
            span.set_attribute("extraction.claims_rejected", rejected_claims)
            span.set_attribute("extraction.citations_rejected", rejected_citations)
            return claims

def evidence_passages(bundle: ContextBundle, field_key: str) -> dict[str, str]:
    """``S#`` -> the evidence text shown for ``field_key`` (what citations may point at)."""
    out: dict[str, list[str]] = {}
    for item in bundle.items:
        if item.kind == "evidence" and item.source_id and field_key in item.field_keys:
            out.setdefault(item.source_id, []).append(item.text)
    return {sid: "\n\n".join(texts) for sid, texts in out.items()}

def user_prompt(bundle: ContextBundle, field: FieldDefinition,
                passages: Mapping[str, str]) -> str:
    kind = f"one of: {' | '.join(field.allowed)}" if field.allowed else field.type
    lines = [bundle.render(), "", "## Instructions",
             f"Target field: {field.key} ({kind})."]
    if field.keywords:
        lines.append("Related terms: " + ", ".join(field.keywords) + ".")
    if field.multi_valued:
        lines.append("This field can have several distinct values: if the evidence names more "
                     "than one (e.g. several distinct sources), return one claim per distinct "
                     "value, each with its own citation; never combine them into one string.")
    lines.append("Cite only these sources: " + ", ".join(sorted(passages, key=_source_order))
                 + ".")
    return "\n".join(lines)

def ground_claims(bundle: ContextBundle, field_key: str, raw_claims: list[dict[str, Any]],
                  passages: Mapping[str, str]) -> tuple[list[FieldClaim], int, int]:
    """(accepted claims, rejected citations, rejected claims).

    A citation is kept only if its quote occurs in the cited source's passage (ignoring case,
    whitespace and Unicode presentation differences); a claim needs at least one.
    """
    normalized = {sid: _normalize(text) for sid, text in passages.items()}
    claims: list[FieldClaim] = []
    rejected_citations = rejected_claims = 0
    for raw in raw_claims:
        evidence: list[EvidenceRef] = []
        seen: set[tuple[str, str]] = set()
        for citation in raw["citations"]:
            sid, quote = citation["source_id"], citation["quote"]
            if _normalize(quote) not in normalized.get(sid, "") or (sid, quote) in seen:
                rejected_citations += 1
                continue
            seen.add((sid, quote))
            evidence.append(bundle.evidence_ref(sid, passage=quote))
        if not evidence:
            rejected_claims += 1
            continue
        claims.append(FieldClaim(
            field_key=field_key, value=raw["value"],
            value_status=ValueStatus(raw["value_status"]),
            value_origin=ValueOrigin(raw["value_origin"]),
            confidence=raw["confidence"], evidence=evidence,
        ))
    return claims, rejected_citations, rejected_claims

_SPACE = re.compile(r"\s+")

def normalize_quote(text: str) -> str:
    """The comparison form used to check a quote against a passage."""
    return _normalize(text)

def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    for a, b in (("‘", "'"), ("’", "'"), ("“", '"'), ("”", '"'),
                 ("–", "-"), ("—", "-")):
        text = text.replace(a, b)
    return _SPACE.sub(" ", text).strip()

def _source_order(source_id: str) -> tuple[int, str]:
    digits = source_id.lstrip("S")
    return (int(digits), source_id) if digits.isdigit() else (1 << 30, source_id)

def build_extraction_gateway(settings: Any,
                             fields: Mapping[str, FieldDefinition] | None = None,
                             **llm_kwargs: Any) -> LLMGateway:
    """The extraction gateway on the configured provider, or ``UnconfiguredGateway`` (with a
    warning) when that provider has no credentials, so the graph still runs without them."""
    try:
        llm = build_structured_llm(settings, task="extract",
                                   prompt_version=EXTRACTION_PROMPT_VERSION, **llm_kwargs)
    except LLMError as exc:
        if exc.kind is not LLMErrorKind.UNCONFIGURED:
            raise
        log.warning("LLM extraction disabled: %s", exc)
        return UnconfiguredGateway()
    return StructuredExtractionGateway(llm, fields)
