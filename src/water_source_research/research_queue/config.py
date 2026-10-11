"""City-run queue policy. Loaded as ``Settings.research_queue`` (env: ``RESEARCH_QUEUE__<FIELD>``)."""

from typing import Literal
from pydantic import BaseModel, Field, model_validator

class ResearchQueueSettings(BaseModel):
    # Concurrency
    worker_concurrency: int = Field(default=4, ge=1, le=64)  # in-flight runs per worker process
    max_running_global: int | None = Field(default=None, ge=1)  # cap across all workers

    # Leases / heartbeats (crash detection)
    lease_seconds: float = Field(default=300, ge=0.1)
    heartbeat_seconds: float = Field(default=60, ge=0.01)

    # Retries
    max_attempts: int = Field(default=3, ge=1)
    backoff_base_seconds: float = Field(default=30, ge=0)
    backoff_max_seconds: float = Field(default=900, ge=0)

    # Execution
    max_run_seconds: float = Field(default=3600, gt=0)
    poll_interval_seconds: float = Field(default=2.0, gt=0)
    keep_checkpoints: bool = False  # delete a run's graph checkpoints once it has succeeded
    shutdown_grace_seconds: float = Field(default=30, ge=0)

    # Graph checkpoints for resume-after-crash. "auto": Postgres when DATABASE_URL is
    # Postgres, otherwise in-memory (resume only within one process).
    checkpointer: Literal["auto", "postgres", "memory"] = "auto"

    @model_validator(mode="after")
    def _validate(self) -> "ResearchQueueSettings":
        if self.heartbeat_seconds * 2 > self.lease_seconds:
            raise ValueError("research_queue.heartbeat_seconds must be at most half of "
                             "lease_seconds, or healthy runs would lose their lease")
        if self.backoff_max_seconds < self.backoff_base_seconds:
            raise ValueError("research_queue.backoff_max_seconds < backoff_base_seconds")
        return self
