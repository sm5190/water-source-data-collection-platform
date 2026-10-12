"""Offline fixtures for LLM tests: mocked VT/OpenAI HTTP (the ``openai`` SDK runs on httpx2).

No test makes a real API call: every SDK client gets an ``httpx2.MockTransport``.
"""

import json
from typing import Any
import httpx2
from water_source_research.core.config import Settings
from water_source_research.llm.config import LLMSettings
from water_source_research.llm.router import RateLimiter, _LockedCache, build_structured_llm

VT_URL = "http://vt.test/api/v1"

def vt_settings(**llm: Any) -> Settings:
    """VT mode with a fake key and no OpenAI key; never reads .env."""
    return Settings(_env_file=None, llm_provider="vt", vt_llm_base_url=VT_URL,
                    vt_llm_api_key="sk-vt-test", vt_llm_model="gpt-oss-120b",
                    openai_api_key="", llm=LLMSettings(**llm))

def openai_settings(**llm: Any) -> Settings:
    return Settings(_env_file=None, llm_provider="openai", openai_api_key="sk-openai-test",
                    openai_model="gpt-test", vt_llm_base_url="", vt_llm_api_key="",
                    vt_llm_model="", llm=LLMSettings(**llm))

class Server:
    """MockTransport handler: records requests and replays scripted responses (last repeats).

    An item may be an ``httpx2.Response``, an exception to raise, or a callable taking the
    request.
    """

    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        item = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if callable(item) and not isinstance(item, httpx2.Response):
            item = item(request)
        if isinstance(item, Exception):
            raise item
        # Fresh copy: a body stream can't be replayed (and sync/async clients differ).
        return httpx2.Response(item.status_code, headers=item.headers, content=item.content)

    def bodies(self) -> list[dict]:
        return [json.loads(r.content) for r in self.requests]

    def clients(self) -> dict[str, Any]:
        transport = httpx2.MockTransport(self)
        return {"http_client": httpx2.Client(transport=transport),
                "async_http_client": httpx2.AsyncClient(transport=transport)}

class Sleeps:
    """Records requested delays instead of waiting (usable as sync and async sleep)."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)

    async def asleep(self, seconds: float) -> None:
        self.delays.append(seconds)

def chat(content: str, prompt_tokens: int = 11, completion_tokens: int = 7,
         status: int = 200) -> httpx2.Response:
    return httpx2.Response(status, json={
        "id": "chatcmpl-1", "object": "chat.completion", "created": 0,
        "model": "gpt-oss-120b",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                  "total_tokens": prompt_tokens + completion_tokens},
    })

def chat_json(data: Any, **kw: Any) -> httpx2.Response:
    return chat(json.dumps(data), **kw)

def error(status: int, body: Any = None, headers: dict | None = None) -> httpx2.Response:
    return httpx2.Response(status, json=body if body is not None else {"error": {
        "message": f"HTTP {status}"}}, headers=headers)

def responses_api(data: Any) -> httpx2.Response:
    """A Responses API reply whose output text is ``data`` as JSON (for ``responses.parse``)."""
    return httpx2.Response(200, json={
        "id": "resp_1", "object": "response", "created_at": 0, "status": "completed",
        "model": "gpt-test", "parallel_tool_calls": True, "tool_choice": "auto", "tools": [],
        "output": [{"type": "message", "id": "msg_1", "status": "completed",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": json.dumps(data),
                                 "annotations": []}]}],
        "usage": {"input_tokens": 13, "output_tokens": 5, "total_tokens": 18,
                  "input_tokens_details": {"cached_tokens": 0},
                  "output_tokens_details": {"reasoning_tokens": 0}},
    })

def timeout(request: httpx2.Request) -> Exception:
    return httpx2.ReadTimeout("timed out", request=request)

def make_llm(server: Server, settings: Settings | None = None, *, sleeps: Sleeps | None = None,
             limiter: RateLimiter | None = None, cache: Any = None,
             task: str = "test", prompt_version: str = "1", **kw: Any):
    settings = settings or vt_settings()
    sleeps = sleeps or Sleeps()
    return build_structured_llm(
        settings, task=task, prompt_version=prompt_version, **server.clients(),
        limiter=limiter or RateLimiter(10_000, 20),
        cache=cache if cache is not None else _LockedCache(3600, 100),
        sleep=sleeps, asleep=sleeps.asleep, jitter=lambda: 1.0, **kw)
