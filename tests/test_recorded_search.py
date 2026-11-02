"""Search snapshots: every response recorded with a timestamp and digest; replays serve the
recorded results and count queries the snapshot doesn't have."""

import asyncio
import json
from discovery_helpers import FakeProvider, web
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
    assert asyncio.run(replay.search("Santiago new query", 5)) == []
    assert replay.misses == ["Santiago new query"]

def test_a_miss_can_go_live_and_is_still_counted(tmp_path):
    path = tmp_path / "snap.jsonl"
    path.write_text("", encoding="utf-8")
    live = FakeProvider("searxng", [web("Live", "https://live.example/")])
    replay = RecordedProvider("searxng", load_snapshots(path), live=live)
    assert [r.url for r in asyncio.run(replay.search("q", 3))] == ["https://live.example/"]
    assert replay.misses == ["q"]
