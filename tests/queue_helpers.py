"""Builders for city-run queue tests: controllable clock, instrumented graph, workers."""

import asyncio
from collections import Counter
from datetime import timedelta
from langgraph.checkpoint.memory import InMemorySaver
from water_source_research.agents import graph as graph_module
from water_source_research.agents.graph import build_graph
from water_source_research.db.models import utcnow
from water_source_research.research_queue.config import ResearchQueueSettings
from water_source_research.research_queue.queue import ResearchQueue
from water_source_research.research_queue.worker import ResearchWorker
from water_source_research.schemas.research import EvidenceChunk

class Clock:
    def __init__(self) -> None:
        self.now = utcnow()

    def __call__(self):
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)

def queue_settings(**overrides) -> ResearchQueueSettings:
    base = {"lease_seconds": 30, "heartbeat_seconds": 0.05, "backoff_base_seconds": 0,
            "backoff_max_seconds": 0, "poll_interval_seconds": 0.05,
            "shutdown_grace_seconds": 0, "max_attempts": 3}
    return ResearchQueueSettings(**{**base, **overrides})

class Crash(BaseException):
    """Escapes ``except Exception`` like a killed process would: nothing gets recorded."""

class Probe:
    """Instruments graph nodes: counts executions per run, tracks concurrency, injects faults."""

    def __init__(self, delay: float = 0.0) -> None:
        self.delay = delay
        self.acquire_calls: Counter[str] = Counter()
        self.validate_calls: Counter[str] = Counter()
        self.in_flight = 0
        self.max_in_flight = 0
        self.fail_validate_once: type[BaseException] | None = None
        self._failed: set[str] = set()

    def install(self, monkeypatch) -> None:
        original_acquire = graph_module.acquire_node
        original_validate = graph_module.validate_node
        probe = self

        async def acquire(state):
            probe.acquire_calls[state["run_id"]] += 1
            probe.in_flight += 1
            probe.max_in_flight = max(probe.max_in_flight, probe.in_flight)
            try:
                # Wait against a deadline in short, cancellable steps. (Counting 10 ms sleeps
                # is wrong on Windows: timers within the ~15.6 ms clock resolution can fire
                # immediately, so the "work" could take ~0 s.)
                loop = asyncio.get_running_loop()
                deadline = loop.time() + probe.delay
                while (remaining := deadline - loop.time()) > 0:
                    await asyncio.sleep(min(0.05, remaining))
            finally:
                probe.in_flight -= 1
            return await original_acquire(state)

        async def validate(state):
            run_id = state["run_id"]
            probe.validate_calls[run_id] += 1
            if probe.fail_validate_once and run_id not in probe._failed:
                probe._failed.add(run_id)
                raise probe.fail_validate_once("injected failure in validate")
            return await original_validate(state)

        monkeypatch.setattr(graph_module, "acquire_node", acquire)
        monkeypatch.setattr(graph_module, "validate_node", validate)

def make_graph(checkpointer=None):
    return build_graph(max_discovery_rounds=1, checkpointer=checkpointer or InMemorySaver())

def make_queue(engine, clock=None, **overrides) -> ResearchQueue:
    kwargs = {"clock": clock} if clock else {}
    return ResearchQueue(engine, queue_settings(**overrides), **kwargs)

def make_worker(queue, graph, worker_id=None, on_success=None) -> ResearchWorker:
    return ResearchWorker(queue, graph, queue.s, worker_id=worker_id, on_success=on_success)

EVIDENCE = EvidenceChunk(chunk_id="u", url="https://toronto.ca/water", source_type="UTILITY",
                         text="Toronto Water is the municipal utility for drinking water.")
