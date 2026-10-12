import asyncio
import httpx
import pytest
from discovery_helpers import SETTINGS, Recorder, Sleeps, http_kwargs, ok, timeout
from water_source_research.discovery.http import (
    ProviderError, ProviderErrorKind, ProviderHttpClient, ResponseCache, expect_items,
    parse_retry_after,
)

def make_client(handler: Recorder, sleeps: Sleeps | None = None, settings=SETTINGS, **kw):
    return ProviderHttpClient("openalex", "https://api.example.org", settings,
                              **http_kwargs(handler, sleeps, **kw))

def get(client, path="/works", params=None, **kw):
    return asyncio.run(client.get_json(path, params or {"search": "toronto"}, **kw))

def test_timeout_is_retried_with_exponential_backoff():
    handler, sleeps = Recorder(timeout, timeout, ok({"results": []})), Sleeps()
    assert get(make_client(handler, sleeps)) == {"results": []}
    assert len(handler.requests) == 3
    assert sleeps.delays == [1.0, 2.0]  # base * 2**attempt (jitter fixed at 1.0)

def test_timeout_exhausted_raises_timeout():
    handler = Recorder(timeout)
    with pytest.raises(ProviderError) as err:
        get(make_client(handler))
    assert err.value.kind is ProviderErrorKind.TIMEOUT
    assert len(handler.requests) == SETTINGS.max_retries + 1

def test_429_honours_retry_after_then_succeeds():
    handler, sleeps = Recorder(ok({}, 429, {"Retry-After": "3"}), ok({"ok": 1})), Sleeps()
    assert get(make_client(handler, sleeps)) == {"ok": 1}
    assert sleeps.delays == [3.0]

def test_retry_after_is_capped_by_backoff_max():
    handler, sleeps = Recorder(ok({}, 429, {"Retry-After": "600"}), ok({"ok": 1})), Sleeps()
    get(make_client(handler, sleeps))
    assert sleeps.delays == [SETTINGS.backoff_max_seconds]

def test_429_exhausted_raises_rate_limited():
    handler = Recorder(ok({}, 429))
    with pytest.raises(ProviderError) as err:
        get(make_client(handler))
    assert err.value.kind is ProviderErrorKind.RATE_LIMITED and err.value.status == 429
    assert len(handler.requests) == 3

def test_server_error_is_retried():
    handler = Recorder(ok({}, 503), ok({"ok": 1}))
    assert get(make_client(handler)) == {"ok": 1}
    assert len(handler.requests) == 2

def test_client_error_is_not_retried():
    handler = Recorder(ok({"error": "bad"}, 400))
    with pytest.raises(ProviderError) as err:
        get(make_client(handler))
    assert err.value.kind is ProviderErrorKind.HTTP and err.value.status == 400
    assert len(handler.requests) == 1

def test_404_tolerated_when_requested():
    assert get(make_client(Recorder(ok({}, 404))), not_found_ok=True) is None

def test_malformed_json_is_not_retried():
    handler = Recorder(httpx.Response(200, text="<html>oops</html>"))
    with pytest.raises(ProviderError) as err:
        get(make_client(handler))
    assert err.value.kind is ProviderErrorKind.MALFORMED
    assert len(handler.requests) == 1

def test_network_error_is_retried():
    handler = Recorder(lambda r: httpx.ConnectError("refused", request=r), ok({"ok": 1}))
    assert get(make_client(handler)) == {"ok": 1}

def test_successful_responses_are_cached():
    handler = Recorder(ok({"n": 1}))
    client = make_client(handler)
    assert get(client) == get(client) == {"n": 1}
    assert len(handler.requests) == 1
    get(client, params={"search": "other"})
    assert len(handler.requests) == 2

def test_failures_are_not_cached():
    handler = Recorder(ok({}, 400), ok({"n": 1}))
    client = make_client(handler)
    with pytest.raises(ProviderError):
        get(client)
    assert get(client) == {"n": 1}

def test_secret_params_are_sent_but_never_in_cache_key_or_errors():
    handler = Recorder(ok({}, 401))
    client = make_client(handler, default_params={"api_key": "topsecret123"},
                         secret_params=frozenset({"api_key"}))
    with pytest.raises(ProviderError) as err:
        get(client)
    assert handler.requests[0].url.params["api_key"] == "topsecret123"
    assert "topsecret123" not in str(err.value)
    assert "topsecret123" not in client.cache_key("/works", {"api_key": "topsecret123", "q": 1})

def test_cache_ttl_and_lru():
    now = [0.0]
    cache = ResponseCache(ttl_seconds=10, max_entries=2, clock=lambda: now[0])
    cache.set("a", 1)
    cache.set("b", 2)
    cache.get("a")  # a is now most recent
    cache.set("c", 3)  # evicts b
    assert (cache.get("a"), cache.get("b"), cache.get("c")) == (1, None, 3)
    now[0] = 11
    assert cache.get("a") is None and len(cache) == 1

def test_min_interval_throttles_consecutive_requests():
    settings = SETTINGS.model_copy(deep=True)
    settings.openalex_limits.min_interval_seconds = 0.5
    handler, sleeps = Recorder(ok({"n": 1})), Sleeps()
    client = make_client(handler, sleeps, settings=settings, clock=lambda: 100.0)

    async def two():
        await client.get_json("/a")
        await client.get_json("/b")
    asyncio.run(two())
    assert sleeps.delays == [0.5]

def test_concurrency_limit_is_respected():
    settings = SETTINGS.model_copy(deep=True)
    settings.openalex_limits.max_concurrency = 2
    active = peak = 0

    class SlowTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            return httpx.Response(200, json={})

    client = ProviderHttpClient("openalex", "https://api.example.org", settings,
                                transport=SlowTransport(), cache=ResponseCache(0, 1))

    async def many():
        await asyncio.gather(*(client.get_json(f"/{i}") for i in range(6)))
    asyncio.run(many())
    assert peak == 2

def test_parse_retry_after_forms():
    assert parse_retry_after("7") == 7.0
    assert parse_retry_after(None) is None and parse_retry_after("soon") is None
    assert parse_retry_after("Wed, 21 Oct 2015 07:28:10 GMT", now=1445412480.0) == 10.0

def test_expect_items_shapes():
    assert expect_items("p", {"results": [{"a": 1}, "junk", None]}, "results") == [{"a": 1}]
    assert expect_items("p", {"results": None}, "results", missing_ok=True) == []
    with pytest.raises(ProviderError) as err:
        expect_items("p", [1, 2], "results")
    assert err.value.kind is ProviderErrorKind.MALFORMED
    with pytest.raises(ProviderError):
        expect_items("p", {"results": {"not": "a list"}}, "results")
