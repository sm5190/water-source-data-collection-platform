import asyncio
import json
import threading
import pytest
import httpx2
from llm_helpers import (
    VT_URL, Server, Sleeps, chat, chat_json, error, make_llm, openai_settings, responses_api,
    timeout, vt_settings,
)
from pydantic import BaseModel, ConfigDict
from water_source_research.core.config import Settings
from water_source_research.llm import openai_reasoner
from water_source_research.llm.base import LLMError, LLMErrorKind
from water_source_research.llm.config import LLMSettings
from water_source_research.llm.openai_reasoner import OpenAIResponsesBackend
from water_source_research.llm.router import (
    RateLimiter, _LockedCache, build_backend, build_structured_llm,
)
from water_source_research.llm.vt_client import VTChatBackend, strip_fences

class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    year: int

GOOD = {"name": "Toronto Water", "year": 2023}

def call(llm, user="question"):
    return llm.structured("system", user, Answer)

def acall(llm, user="question"):
    return asyncio.run(llm.astructured("system", user, Answer))

# --- provider selection ---------------------------------------------------------------

def test_vt_is_the_default_provider():
    s = Settings(_env_file=None)
    assert s.llm_provider == "vt"
    assert s.llm.requests_per_minute == 27

def test_vt_mode_needs_no_openai_key():
    server = Server(chat_json(GOOD))
    llm = make_llm(server, vt_settings())
    assert isinstance(llm.backend, VTChatBackend)
    assert (llm.provider, llm.model) == ("vt", "gpt-oss-120b")
    assert call(llm) == Answer(**GOOD)
    [request] = server.requests
    assert str(request.url) == f"{VT_URL}/chat/completions"
    assert request.headers["Authorization"] == "Bearer sk-vt-test"

def test_selected_provider_without_credentials_is_unconfigured():
    with pytest.raises(LLMError) as vt:
        build_backend(Settings(_env_file=None, vt_llm_base_url="", vt_llm_api_key="",
                               vt_llm_model=""))
    assert vt.value.kind is LLMErrorKind.UNCONFIGURED
    with pytest.raises(LLMError) as oa:
        build_backend(Settings(_env_file=None, llm_provider="openai", openai_api_key=""))
    assert oa.value.kind is LLMErrorKind.UNCONFIGURED

def test_task_model_override_and_vt_output_cap():
    llm = make_llm(Server(chat_json(GOOD)), vt_settings(), model="DeepSeek-V4.1-Flash")
    assert llm.model == "DeepSeek-V4.1-Flash"
    with pytest.raises(ValueError, match="max_output_tokens"):
        build_backend(vt_settings(max_output_tokens=9000))

def test_switching_to_openai_uses_responses_parse():
    server = Server(responses_api(GOOD))
    llm = make_llm(server, openai_settings())
    assert isinstance(llm.backend, OpenAIResponsesBackend)
    assert (llm.provider, llm.model) == ("openai", "gpt-test")
    assert call(llm) == Answer(**GOOD)
    assert acall(llm, "async question") == Answer(**GOOD)
    assert [r.url.path for r in server.requests] == ["/v1/responses", "/v1/responses"]
    assert server.requests[0].headers["Authorization"] == "Bearer sk-openai-test"
    assert server.bodies()[0]["model"] == "gpt-test"

@pytest.mark.parametrize("failure", [error(429), error(500), timeout, error(401)])
def test_vt_failure_never_falls_back_to_openai(monkeypatch, failure):
    def forbidden(*args, **kwargs):
        raise AssertionError("OpenAI client constructed in VT mode")
    for name in ("OpenAIResponsesBackend", "OpenAIStructuredClient", "OpenAIReasoner"):
        monkeypatch.setattr(getattr(openai_reasoner, name), "__init__", forbidden)
    server = Server(failure)
    llm = make_llm(server, vt_settings(max_retries=2))
    with pytest.raises(LLMError) as exc:
        call(llm)
    assert exc.value.provider == "vt"
    assert {r.url.host for r in server.requests} == {"vt.test"}

# --- request shape ----------------------------------------------------------------------

@pytest.mark.parametrize("mode", ["json_schema", "json_object", "prompt"])
def test_structured_mode_controls_response_format(mode):
    server = Server(chat_json(GOOD))
    call(make_llm(server, vt_settings(vt_structured_mode=mode, max_output_tokens=1234)))
    [body] = server.bodies()
    assert body["model"] == "gpt-oss-120b"
    assert body["max_tokens"] == 1234
    assert '"year"' in body["messages"][0]["content"]  # schema always in the system prompt
    if mode == "json_schema":
        assert body["response_format"]["type"] == "json_schema"
        assert body["response_format"]["json_schema"]["schema"]["required"] == ["name", "year"]
    elif mode == "json_object":
        assert body["response_format"] == {"type": "json_object"}
    else:
        assert "response_format" not in body

def test_fenced_json_is_accepted():
    assert strip_fences('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert call(make_llm(Server(chat("```json\n" + json.dumps(GOOD) + "\n```")))) \
        == Answer(**GOOD)

# --- structured output validation ------------------------------------------------------

def test_invalid_output_is_repaired_with_the_validation_error():
    server = Server(chat_json({"name": "Toronto Water"}), chat_json(GOOD))
    assert call(make_llm(server)) == Answer(**GOOD)
    repair = server.bodies()[1]["messages"]
    assert [m["role"] for m in repair] == ["system", "user", "assistant", "user"]
    assert "year" in repair[-1]["content"] and "Field required" in repair[-1]["content"]

@pytest.mark.parametrize("reply", [
    chat_json({"name": "x"}),                            # missing field
    chat_json({**GOOD, "extra": 1}),                     # extra field
    chat_json({"name": "x", "year": "last year"}),       # invalid value
    chat("not json at all"),
    chat(""),
])
def test_persistently_invalid_output_raises_schema_error(reply):
    server = Server(reply)
    with pytest.raises(LLMError) as exc:
        call(make_llm(server, vt_settings(structured_max_attempts=2)))
    assert exc.value.kind is LLMErrorKind.SCHEMA
    assert len(server.requests) == 3  # first try + 2 repairs; nothing accepted

def test_invalid_output_is_never_cached():
    cache = _LockedCache(3600, 10)
    with pytest.raises(LLMError):
        call(make_llm(Server(chat("nope")), vt_settings(structured_max_attempts=0), cache=cache))
    assert len(cache) == 0

# --- retries --------------------------------------------------------------------------

def test_429_retry_honours_vt_body_wait_hint():
    sleeps = Sleeps()
    server = Server(error(429, {"error": {"code": "user_concurrent", "retry_after_s": 7}}),
                    chat_json(GOOD))
    assert call(make_llm(server, sleeps=sleeps)) == Answer(**GOOD)
    assert sleeps.delays == [7.0]

def test_429_retry_honours_retry_after_header_and_cap():
    sleeps = Sleeps()
    server = Server(error(429, headers={"Retry-After": "3"}),
                    error(429, headers={"Retry-After": "999"}), chat_json(GOOD))
    call(make_llm(server, vt_settings(backoff_max_seconds=30), sleeps=sleeps))
    assert sleeps.delays == [3.0, 30.0]

def test_transient_errors_use_exponential_backoff_up_to_the_retry_cap():
    sleeps = Sleeps()
    server = Server(error(503))
    with pytest.raises(LLMError) as exc:
        call(make_llm(server, vt_settings(max_retries=3, backoff_base_seconds=1,
                                          backoff_max_seconds=5), sleeps=sleeps))
    assert exc.value.kind is LLMErrorKind.SERVER
    assert len(server.requests) == 4
    assert sleeps.delays == [1, 2, 4]  # jitter pinned to 1.0

def test_timeouts_are_retried():
    server = Server(timeout, chat_json(GOOD))
    assert acall(make_llm(server)) == Answer(**GOOD)
    assert len(server.requests) == 2

@pytest.mark.parametrize("status, kind", [
    (401, LLMErrorKind.AUTH), (403, LLMErrorKind.AUTH), (413, LLMErrorKind.TOO_LARGE),
    (404, LLMErrorKind.BAD_REQUEST), (400, LLMErrorKind.BAD_REQUEST),
])
def test_permanent_errors_are_not_retried(status, kind):
    server = Server(error(status))
    with pytest.raises(LLMError) as exc:
        call(make_llm(server))
    assert exc.value.kind is kind and exc.value.status == status
    assert len(server.requests) == 1
    assert "sk-vt-test" not in str(exc.value)

def test_plain_text_gateway_errors_are_surfaced():
    vpn = httpx2.Response(403, text="API access is restricted to the VT Campus VPN. Please "
                                    "connect to the VPN and retry.\n",
                          headers={"content-type": "text/plain"})
    with pytest.raises(LLMError) as exc:
        call(make_llm(Server(vpn)))
    assert exc.value.kind is LLMErrorKind.AUTH
    assert "restricted to the VT Campus VPN" in str(exc.value)

# --- caching --------------------------------------------------------------------------

def test_cache_hits_skip_the_provider():
    server = Server(chat_json(GOOD))
    llm = make_llm(server)
    assert call(llm) == call(llm) == acall(llm)
    assert len(server.requests) == 1

def test_cache_key_covers_provider_model_prompt_version_and_schema():
    cache = _LockedCache(3600, 100)
    base = make_llm(Server(chat_json(GOOD)), cache=cache)
    keys = {
        base.cache_key("s", "u", Answer),
        make_llm(Server(chat_json(GOOD)), cache=cache, prompt_version="2")
        .cache_key("s", "u", Answer),
        make_llm(Server(chat_json(GOOD)), cache=cache, model="GLM-5.3")
        .cache_key("s", "u", Answer),
        make_llm(Server(responses_api(GOOD)), openai_settings(), cache=cache)
        .cache_key("s", "u", Answer),
        make_llm(Server(chat_json(GOOD)), cache=cache, task="other")
        .cache_key("s", "u", Answer),
    }

    class Other(Answer):
        note: str = ""

    keys.add(base.cache_key("s", "u", Other))
    keys.add(base.cache_key("s", "u2", Answer))
    assert len(keys) == 7

def test_cache_disabled_with_zero_ttl():
    server = Server(chat_json(GOOD))
    llm = make_llm(server, cache=_LockedCache(0, 10))
    call(llm)
    call(llm)
    assert len(server.requests) == 2

# --- rate limiting --------------------------------------------------------------------

class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds

def test_limiter_enforces_requests_per_minute():
    clock = Clock()
    limiter = RateLimiter(3, 10, clock=clock)
    for _ in range(3):
        limiter.acquire(clock.sleep)
        limiter.release()
    assert clock.now == 1000.0
    limiter.acquire(clock.sleep)  # 4th waits until the first leaves the 60s window
    assert clock.now == pytest.approx(1060.0)

def test_limiter_enforces_concurrency():
    limiter = RateLimiter(100, 2)
    assert limiter.try_acquire() == 0
    assert limiter.try_acquire() == 0
    assert limiter.try_acquire() > 0
    limiter.release()
    assert limiter.try_acquire() == 0

def test_concurrent_async_calls_share_the_concurrency_cap():
    state = {"now": 0, "max": 0}

    async def slow(request):
        state["now"] += 1
        state["max"] = max(state["max"], state["now"])
        await asyncio.sleep(0.02)
        state["now"] -= 1
        return chat_json({"name": json.loads(request.content)["messages"][1]["content"],
                          "year": 2020})

    transport = httpx2.MockTransport(slow)
    llm = build_structured_llm(
        vt_settings(), task="t", prompt_version="1",
        async_http_client=httpx2.AsyncClient(transport=transport),
        limiter=RateLimiter(10_000, 2), cache=_LockedCache(0, 1))

    async def many():
        return await asyncio.gather(*(llm.astructured("s", f"q{i}", Answer) for i in range(6)))

    results = asyncio.run(many())
    assert [r.name for r in results] == [f"q{i}" for i in range(6)]
    assert state["max"] == 2

def test_limiter_is_shared_between_threads_and_event_loop():
    limiter = RateLimiter(10_000, 1)
    limiter.acquire()
    released = threading.Event()

    def worker():
        limiter.acquire()  # blocks until the async side releases
        released.set()
        limiter.release()

    t = threading.Thread(target=worker)
    t.start()
    assert not released.wait(0.15)
    limiter.release()
    t.join(2)
    assert released.is_set()

# --- telemetry ------------------------------------------------------------------------

def test_llm_call_span_records_provider_model_tokens_and_retries(spans):
    server = Server(error(500), chat_json(GOOD, prompt_tokens=21, completion_tokens=9))
    call(make_llm(server, task="extract", prompt_version="extract-1"))
    [span] = [s for s in spans.get_finished_spans() if s.name == "llm.call"]
    a = span.attributes
    assert (a["llm.provider"], a["llm.model"], a["llm.task"], a["llm.prompt_version"]) \
        == ("vt", "gpt-oss-120b", "extract", "extract-1")
    assert (a["llm.attempts"], a["llm.prompt_tokens"], a["llm.completion_tokens"]) == (2, 21, 9)
    assert a["llm.cache_hit"] is False and a["llm.error"] == "server_error"
    assert "llm.latency_ms" in a

def test_settings_validation():
    with pytest.raises(ValueError):
        LLMSettings(max_concurrency=21)  # VT allows 20 requests in flight per user
    with pytest.raises(ValueError):
        LLMSettings(backoff_base_seconds=10, backoff_max_seconds=1)
