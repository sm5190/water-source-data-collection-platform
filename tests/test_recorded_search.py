"""Search snapshots: every response recorded with a timestamp and digest; replays serve the
recorded results and count queries the snapshot doesn't have."""

import asyncio
import json
import pytest
from discovery_helpers import FakeProvider, web
from water_source_research.discovery.http import ProviderError, ProviderErrorKind
from water_source_research.discovery.queries import ineffective_topics
from water_source_research.discovery.queries import PlannedQuery
from water_source_research.discovery.service import FAILURE, DiscoveryService
from water_source_research.discovery.config import DiscoverySettings
from water_source_research.discovery.recorded import (
    RecordedProvider, RecordingProvider, load_snapshots,
)

def test_record_then_replay(tmp_path):
    path = tmp_path / "snap.jsonl"
    live = FakeProvider("searxng", [web("Water in Santiago", "https://x.example/a")])
    rec = RecordingProvider(live, path, run_label="baseline-1")
    asyncio.run(rec.search("Santiago  Chile water", 5))
    [row] = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert row["provider"] == "searxng" and row["run_label"] == "baseline-1"
    assert row["searched_at"] and len(row["sha256"]) == 64
    replay = RecordedProvider("searxng", load_snapshots(path))
    hit = asyncio.run(replay.search("santiago chile   WATER", 5))  # whitespace/case-insensitive
    assert [r.url for r in hit] == ["https://x.example/a"] and replay.hits == 1
    with pytest.raises(ProviderError) as err:  # a miss is unknown, never "no results"
        asyncio.run(replay.search("Santiago new query", 5))
    assert err.value.kind is ProviderErrorKind.NOT_RECORDED
    assert replay.misses == ["Santiago new query"] and replay.live_misses == []

def test_a_miss_can_go_live_and_is_still_counted(tmp_path):
    path = tmp_path / "snap.jsonl"
    path.write_text("", encoding="utf-8")
    live = FakeProvider("searxng", [web("Live", "https://live.example/")])
    replay = RecordedProvider("searxng", load_snapshots(path), live=live)
    assert [r.url for r in asyncio.run(replay.search("q", 3))] == ["https://live.example/"]
    assert replay.misses == ["q"] and replay.live_misses == ["q"]

def test_a_miss_is_a_failed_call_not_an_empty_search(tmp_path):
    path = tmp_path / "snap.jsonl"
    path.write_text("", encoding="utf-8")
    service = DiscoveryService(DiscoverySettings(),
                               web=[RecordedProvider("searxng", load_snapshots(path))])
    outcome = asyncio.run(service.discover(
        [PlannedQuery("web", "Santiago Chile water", "water_source", "water_source")], city="Santiago"))
    [call] = outcome.calls
    assert call.result == FAILURE and call.strategy == "not_recorded"
    assert outcome.provider_status["searxng"] == "not_recorded"

def _event(query, strategy, retained=0):
    return {"kind": "TOOL_CALL", "node": "discover", "strategy": strategy,
            "attrs": {"query": query, "sources_retained": retained}}

def test_replay_misses_never_make_a_topic_ineffective():
    topic = "drinking water source"
    misses = [_event(f"Santiago {topic}", "not_recorded") for _ in range(3)]
    assert ineffective_topics(misses, [topic]) == set()
    empty = [_event(f"{place} {topic}", "web_search") for place in ("Santiago", "Maipú")]
    assert ineffective_topics(empty, [topic]) == {topic}
