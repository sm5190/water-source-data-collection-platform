"""Async worker that executes queued city runs.

Concurrency: at most ``worker_concurrency`` runs in flight per process (plus the optional
global cap enforced at claim time). Each run:
  claim (lease) -> resume-or-start the checkpointed graph -> heartbeat while running
  -> complete / pause for human review / retry-with-backoff / fail / cancel.
A run that pauses on human review (LangGraph interrupt) is parked as AWAITING_REVIEW with its
checkpoint kept, freeing the worker; once the API resumes it, the next worker to claim it
continues the graph with ``Command(resume=...)``.
A heartbeat that finds the lease gone stops the run (fencing); on shutdown, in-flight runs
get ``shutdown_grace_seconds`` and are then released back to the queue.
"""

import asyncio
import logging
import os
import socket
import time
from collections.abc import Callable
from typing import Any
from uuid import uuid4
from langgraph.types import Command
from opentelemetry.trace import Status, StatusCode
from water_source_research.observability.otel import get_tracer
from water_source_research.services.field_state import initialize_city_state, unresolved_fields
from . import telemetry
from .config import ResearchQueueSettings
from .queue import Job, JobStatus, ResearchQueue
from .steps import summarize_step

log = logging.getLogger(__name__)

RESULT_KEYS = ("finalized", "events", "context_bundle_ids", "procedure_selections",
               "planned_queries", "needs_human_review", "review_reason", "fields_needing_evidence",
               "research_state", "claims", "discovery_rounds", "procedure_memory_degraded",
               "post_processing_errors", "distillation_jobs", "acquired_documents",
               "unresolved_fields", "discovery_provider_status", "discovery_candidates",
               "candidate_urls", "resolution_log", "fields_requiring_review", "review_round")

class _Stop(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason

class ResearchWorker:
    def __init__(
        self,
        queue: ResearchQueue,
        graph: Any,  # compiled LangGraph with a checkpointer
        settings: ResearchQueueSettings,
        worker_id: str | None = None,
        on_success: Callable[[str], None] | None = None,
    ) -> None:
        self.queue = queue
        self.graph = graph
        self.s = settings
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:6]}"
        self.on_success = on_success  # soft post-run hook (e.g. procedure distillation)
        self.in_flight: set[asyncio.Task] = set()
        self.max_in_flight_seen = 0

    # --- loops --------------------------------------------------------------------------

    async def run(self, stop: asyncio.Event) -> None:
        """Process jobs until ``stop`` is set, then drain gracefully."""
        stop_wait = asyncio.create_task(stop.wait())
        try:
            while not stop.is_set():
                await asyncio.to_thread(self.queue.reap_expired)
                await self._fill_slots()
                waiters = {stop_wait, *self.in_flight}
                await asyncio.wait(waiters, timeout=self.s.poll_interval_seconds,
                                   return_when=asyncio.FIRST_COMPLETED)
        finally:
            stop_wait.cancel()
            await self._shutdown()

    async def drain(self) -> int:
        """Process jobs until none are claimable (CLI ``--once`` and tests). Returns count."""
        processed = 0
        while True:
            await asyncio.to_thread(self.queue.reap_expired)
            started = await self._fill_slots()
            if not started and not self.in_flight:
                return processed
            processed += started
            if self.in_flight:
                await asyncio.wait(self.in_flight, return_when=asyncio.FIRST_COMPLETED)

    async def _fill_slots(self) -> int:
        started = 0
        while len(self.in_flight) < self.s.worker_concurrency:
            job = await asyncio.to_thread(self.queue.claim, self.worker_id)
            if job is None:
                break
            task = asyncio.create_task(self._execute(job), name=f"run:{job.run_id}")
            self.in_flight.add(task)
            task.add_done_callback(self.in_flight.discard)
            self.max_in_flight_seen = max(self.max_in_flight_seen, len(self.in_flight))
            started += 1
        return started

    async def _shutdown(self) -> None:
        if not self.in_flight:
            return
        _, pending = await asyncio.wait(set(self.in_flight),
                                        timeout=self.s.shutdown_grace_seconds)
        for task in pending:
            task.cancel()  # _execute releases the lease on cancellation
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    # --- one run ------------------------------------------------------------------------

    async def _execute(self, job: Job) -> None:
        tracer = get_tracer()
        with tracer.start_as_current_span("research.run") as span:
            span.set_attribute("research.run_id", job.run_id)
            span.set_attribute("research_queue.attempt", job.attempts)
            span.set_attribute("research_queue.worker_id", self.worker_id)
            started = time.perf_counter()
            run_task = asyncio.create_task(self._run_graph(job))
            stop_reason: dict[str, str] = {}
            heartbeat = asyncio.create_task(self._heartbeat(job, run_task, stop_reason))
            try:
                state, resumed, paused = await asyncio.wait_for(run_task,
                                                                self.s.max_run_seconds)
            except asyncio.CancelledError:
                if "reason" not in stop_reason:
                    # Worker shutdown: hand the job back without consuming an attempt.
                    await asyncio.to_thread(self.queue.release, job.run_id, self.worker_id)
                    span.set_attribute("research_queue.outcome", "released")
                    raise
                await self._stopped(job, stop_reason["reason"], span)
            except TimeoutError:
                await self._failed(job, f"run exceeded {self.s.max_run_seconds}s", span)
            except Exception as exc:  # noqa: BLE001 - any graph failure is a job failure
                span.record_exception(exc)
                await self._failed(job, f"{type(exc).__name__}: {exc}", span)
            else:
                if paused is not None:
                    await self._paused(job, state, paused, span)
                else:
                    await self._succeeded(job, state, resumed, span)
            finally:
                heartbeat.cancel()
                telemetry.run_duration_seconds.record(time.perf_counter() - started)

    async def _run_graph(self, job: Job) -> tuple[dict[str, Any], bool, int | None]:
        """(final values, resumed from a checkpoint, review round the graph paused on)."""
        config = {"configurable": {"thread_id": job.run_id}}
        snapshot = await self.graph.aget_state(config)
        if snapshot.next:
            paused = paused_round(snapshot)
            if paused is not None and paused > job.review_round:
                # Paused earlier but never recorded as waiting (crash): park it again.
                return dict(snapshot.values), True, paused
            telemetry.runs_resumed.add(1)
            # Resumed review round (the node reads the decisions from the database), or a
            # run that crashed/failed mid-graph: continue from the checkpoint.
            graph_input = Command(resume={"round": paused}) if paused is not None else None
            final = await self._stream(job, graph_input, config, dict(snapshot.values))
            return dict(final.values), True, paused_round(final)
        if snapshot.values:  # graph finished but the job was never marked complete
            return dict(snapshot.values), True, None
        final = await self._stream(job, self._initial_input(job), config, {})
        return dict(final.values), False, paused_round(final)

    async def _stream(self, job: Job, graph_input: Any, config: dict[str, Any],
                      previous: dict[str, Any]) -> Any:
        """Run the graph step by step, recording each completed step for progress views."""
        seq = 0
        started = time.perf_counter()
        async for update in self.graph.astream(graph_input, config, stream_mode="updates"):
            for node, state in update.items():
                if not isinstance(state, dict):
                    continue
                seq += 1
                now = time.perf_counter()
                summary = summarize_step(node, state, previous)
                try:
                    await asyncio.to_thread(self.queue.record_step, job.run_id, self.worker_id,
                                            job.attempts, seq, node,
                                            round((now - started) * 1000, 1), summary)
                except Exception:
                    # Progress records are informational; never fail the run over them.
                    log.warning("could not record step %s for %s", node, job.run_id,
                                exc_info=True)
                started, previous = now, state
        return await self.graph.aget_state(config)

    async def _heartbeat(self, job: Job, run_task: asyncio.Task,
                         stop_reason: dict[str, str]) -> None:
        while not run_task.done():
            await asyncio.sleep(self.s.heartbeat_seconds)
            try:
                beat = await asyncio.to_thread(self.queue.heartbeat, job.run_id, self.worker_id)
            except Exception:  # transient DB error: retry next beat
                log.warning("heartbeat failed for %s", job.run_id, exc_info=True)
                continue
            if not beat.lease_held:
                stop_reason["reason"] = "lease_lost"
            elif beat.cancel_requested:
                stop_reason["reason"] = "cancelled"
            if "reason" in stop_reason:
                run_task.cancel()
                return

    # --- outcomes -----------------------------------------------------------------------

    async def _succeeded(self, job: Job, state: dict[str, Any], resumed: bool, span) -> None:
        result = {k: state.get(k) for k in RESULT_KEYS if k in state}
        recorded = await asyncio.to_thread(self.queue.complete, job.run_id, self.worker_id,
                                           result, resumed)
        span.set_attribute("research_queue.outcome", "succeeded" if recorded else "lease_lost")
        span.set_attribute("research_queue.resumed", resumed)
        if not recorded:
            return
        if not self.s.keep_checkpoints:
            await self._delete_checkpoints(job.run_id)
        if self.on_success:
            try:
                await asyncio.to_thread(self.on_success, job.run_id)
            except Exception:  # post-run hooks never affect the result
                log.warning("post-run hook failed for %s", job.run_id, exc_info=True)

    async def _paused(self, job: Job, state: dict[str, Any], round_no: int, span) -> None:
        """The graph is waiting for human review: park the job, keep the checkpoint."""
        result = {k: state.get(k) for k in RESULT_KEYS if k in state}
        recorded = await asyncio.to_thread(self.queue.await_review, job.run_id,
                                           self.worker_id, round_no, result)
        span.set_attribute("research_queue.outcome",
                           "awaiting_review" if recorded else "lease_lost")
        span.set_attribute("review.round", round_no)

    async def _failed(self, job: Job, error: str, span) -> None:
        status = await asyncio.to_thread(self.queue.fail, job.run_id, self.worker_id, error)
        span.set_status(Status(StatusCode.ERROR, error[:200]))
        span.set_attribute("research_queue.outcome",
                           "retry" if status is JobStatus.QUEUED else "failed")

    async def _stopped(self, job: Job, reason: str, span) -> None:
        if reason == "cancelled":
            await asyncio.to_thread(self.queue.mark_cancelled, job.run_id, self.worker_id)
        # lease_lost: another worker owns the job now; touch nothing.
        span.set_attribute("research_queue.outcome", reason)

    async def _delete_checkpoints(self, run_id: str) -> None:
        saver = getattr(self.graph, "checkpointer", None)
        if saver is None or not hasattr(saver, "adelete_thread"):
            return
        try:
            await saver.adelete_thread(run_id)
        except Exception:  # cleanup only
            log.warning("could not delete checkpoints for %s", run_id, exc_info=True)

    @staticmethod
    def _initial_input(job: Job) -> dict[str, Any]:
        research = initialize_city_state(job.city_name, job.country)
        return {
            "run_id": job.run_id,
            "city_name": job.city_name,
            "country": job.country,
            "research_state": research.model_dump(mode="json"),
            "unresolved_fields": unresolved_fields(research, job.field_keys),
        }

def paused_round(snapshot: Any) -> int | None:
    """Review round a checkpointed graph is paused on (human_review interrupt), if any."""
    for item in getattr(snapshot, "interrupts", ()) or ():
        value = getattr(item, "value", None)
        if isinstance(value, dict) and isinstance(value.get("round"), int):
            return value["round"]
    return None
