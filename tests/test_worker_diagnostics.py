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
