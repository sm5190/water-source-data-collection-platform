"""Virginia Tech ARC LLM API backend (https://www.docs.arc.vt.edu/ai/011_llm_api_arc_vt_edu.html).

The service is OpenAI-compatible (Open WebUI over vLLM): Chat Completions at
``{VT_LLM_BASE_URL}/chat/completions`` with a personal Bearer key. Only Chat Completions is
documented, so structured output is requested via ``response_format`` (see
``LLMSettings.vt_structured_mode``) and always validated client-side by the router.
"""

import json
import re
from typing import Any
import openai
from pydantic import BaseModel
from .base import Completion, LLMError, LLMErrorKind, classify_openai_error, usage_tokens
from .config import VT_MAX_OUTPUT_TOKENS, LLMSettings

PROVIDER = "vt"
_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)

class VTChatBackend:
    provider = PROVIDER

    def __init__(self, base_url: str, api_key: str, model: str, settings: LLMSettings, *,
                 http_client: Any = None, async_http_client: Any = None) -> None:
        missing = [name for name, value in (("VT_LLM_BASE_URL", base_url),
                                            ("VT_LLM_API_KEY", api_key),
                                            ("VT_LLM_MODEL", model)) if not value]
        if missing:
            raise LLMError(PROVIDER, LLMErrorKind.UNCONFIGURED, "set " + ", ".join(missing))
        if settings.max_output_tokens > VT_MAX_OUTPUT_TOKENS:
            raise ValueError(f"llm.max_output_tokens must be <= {VT_MAX_OUTPUT_TOKENS} for VT "
                             "(non-streaming requests above it are rejected)")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.settings = settings
        self._api_key = api_key
        self._http_client = http_client
        self._async_http_client = async_http_client
        self._sync: openai.OpenAI | None = None
        self._async: openai.AsyncOpenAI | None = None

    def complete(self, messages: list[dict[str, str]], schema: type[BaseModel]) -> Completion:
        if self._sync is None:
            self._sync = openai.OpenAI(
                api_key=self._api_key, base_url=self.base_url, max_retries=0,
                timeout=self.settings.timeout_seconds, http_client=self._http_client)
        try:
            response = self._sync.chat.completions.create(**self._request(messages, schema))
        except openai.OpenAIError as exc:
            raise classify_openai_error(PROVIDER, exc) from None
        return _completion(response)

    async def acomplete(self, messages: list[dict[str, str]],
                        schema: type[BaseModel]) -> Completion:
        if self._async is None:
            self._async = openai.AsyncOpenAI(
                api_key=self._api_key, base_url=self.base_url, max_retries=0,
                timeout=self.settings.timeout_seconds, http_client=self._async_http_client)
        try:
            response = await self._async.chat.completions.create(
                **self._request(messages, schema))
        except openai.OpenAIError as exc:
            raise classify_openai_error(PROVIDER, exc) from None
        return _completion(response)

    def _request(self, messages: list[dict[str, str]],
                 schema: type[BaseModel]) -> dict[str, Any]:
        json_schema = schema.model_json_schema()
        instruction = ("Respond with a single JSON object, and nothing else, that conforms to "
                       "this JSON Schema:\n" + json.dumps(json_schema, separators=(",", ":")))
        system, *rest = messages
        request: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system["content"] + "\n\n" + instruction},
                         *rest],
            "max_tokens": self.settings.max_output_tokens,
        }
        mode = self.settings.vt_structured_mode
        if mode == "json_schema":
            request["response_format"] = {"type": "json_schema", "json_schema": {
                "name": schema.__name__, "schema": json_schema, "strict": True}}
        elif mode == "json_object":
            request["response_format"] = {"type": "json_object"}
        return request

def _completion(response: Any) -> Completion:
    prompt_tokens, completion_tokens = usage_tokens(getattr(response, "usage", None))
    choices = getattr(response, "choices", None) or []
    content = choices[0].message.content if choices else None
    return Completion(text=strip_fences(content or ""), prompt_tokens=prompt_tokens,
                      completion_tokens=completion_tokens)

def strip_fences(text: str) -> str:
    match = _FENCE.match(text)
    return match.group(1) if match else text.strip()
