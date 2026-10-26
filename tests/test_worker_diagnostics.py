"""Worker diagnostics: persistent append-only logs, process samples, state size, and step
summaries that carry memory per step (evidence for resource questions)."""

import asyncio
import logging
from water_source_research.research_queue import diagnostics
from water_source_research.research_queue.diagnostics import (
    Monitor, configure_worker_logging, process_sample, state_size,
)
from water_source_research.research_queue.steps import summarize_step

def test_process_sample_reports_memory_and_handles():
    s = process_sample()
    assert s["rss_mb"] > 0 and s["threads"] >= 1 and s["system_available_mb"] > 0
    assert "private_mb" in s
    assert "handles" in s or "fds" in s

def test_state_size_counts_what_checkpoints_carry():
    state = {"evidence_chunks": [{"text": "abc"}, {"text": "de"}],
             "acquired_documents": [{}], "events": [{}, {}, {}]}
    assert state_size(state) == {"chunks": 2, "chunk_chars": 5, "documents": 1, "events": 3,
                                 "candidates": 0}

def test_step_summaries_record_resources():
    summary = summarize_step("acquire", {"evidence_chunks": [{"text": "x" * 10}]}, {})
    assert summary["resources"]["state"]["chunk_chars"] == 10
    assert summary["resources"]["rss_mb"] > 0

def test_logs_are_per_start_and_append_only(tmp_path):
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        path = configure_worker_logging(tmp_path)
        logging.getLogger("research_worker").info("hello from the test")
        for h in root.handlers:
            h.flush()
        text = path.read_text(encoding="utf-8")
        assert path.name.startswith("worker-") and str(path.parent) == str(tmp_path)
        assert "logging to" in text and "hello from the test" in text
    finally:
        for h in root.handlers[:]:
            if h not in before:
                root.removeHandler(h)
                h.close()
        if diagnostics._FAULT_FILE is not None:
            import faulthandler
            faulthandler.disable()
            diagnostics._FAULT_FILE.close()
            diagnostics._FAULT_FILE = None

def test_monitor_tracks_peak_memory(caplog):
    async def run():
        m = Monitor(interval=0.01, in_flight=lambda: 2)
        m.start()
        await asyncio.sleep(0.05)
        await m.stop()
        return m
    with caplog.at_level(logging.INFO, logger="research_worker.diagnostics"):
        m = asyncio.run(run())
    assert m.peak_rss_mb > 0
    assert any("heartbeat" in r.message and "'in_flight': 2" in r.message
               for r in caplog.records)

def test_monitor_publishes_heartbeat_file_resources_and_callback(tmp_path):
    import json
    from water_source_research.research_queue.diagnostics import heartbeat_path
    received = []

    async def run():
        m = Monitor(interval=0.05, activity=lambda: {"active_runs": {"r1": {"stage": "extract"}},
                                                     "active_jobs": 1},
                    log_dir=tmp_path, instance_id="host-1", on_sample=received.append)
        m.start()
        await asyncio.sleep(0.2)
        await m.stop()

    asyncio.run(run())
    hb = json.loads(heartbeat_path(tmp_path, "host-1").read_text())
    assert hb["active_runs"] == {"r1": {"stage": "extract"}} and hb["pid"] > 0
    assert {"rss_mb", "cpu_percent", "uptime_s", "threads", "at"} <= set(hb)
    lines = (tmp_path / "resources" / "host-1.jsonl").read_text().splitlines()
    assert len(lines) >= 2 and json.loads(lines[0])["active_jobs"] == 1
    assert received and received[-1]["active_jobs"] == 1

def test_monitor_sets_stop_when_the_stop_file_appears(tmp_path):
    from water_source_research.research_queue.diagnostics import stop_path

    async def run():
        stop = asyncio.Event()
        m = Monitor(interval=10, log_dir=tmp_path, instance_id="w1", stop=stop, stop_poll=0.02)
        m.start()
        await asyncio.sleep(0.05)
        assert not stop.is_set()
        stop_path(tmp_path, "w1").parent.mkdir(parents=True, exist_ok=True)
        stop_path(tmp_path, "w1").write_text("now")
        await asyncio.wait_for(stop.wait(), 2)
        await m.stop()

    asyncio.run(run())

def test_checkpoint_writes_are_measured_without_changing_them():
    from langgraph.checkpoint.memory import InMemorySaver
    from queue_helpers import make_graph
    from water_source_research.research_queue.checkpointing import measure_checkpoint_writes
    saver = InMemorySaver()
    stats = measure_checkpoint_writes(saver)
    graph = make_graph(saver)
    config = {"configurable": {"thread_id": "t1"}}
    final = asyncio.run(graph.ainvoke({"run_id": "t1", "city_name": "Oslo", "country": "Norway",
                                       "unresolved_fields": []}, config))
    assert final["finalized"] is True
    assert stats.writes > 0 and stats.peak_bytes > 0 and stats.errors == 0
    assert asyncio.run(graph.aget_state(config)).values["run_id"] == "t1"
