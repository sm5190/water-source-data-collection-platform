"""Observability settings. Loaded as ``Settings.observability`` (env: ``OBSERVABILITY__<FIELD>``).

Everything here is optional: with the defaults no telemetry backend is needed, and worker
crash evidence (supervisor + local event files) works with no tracing at all.
"""

from pydantic import BaseModel, Field, model_validator

class ObservabilitySettings(BaseModel):
    # --- worker diagnostics (Phase 1) ---------------------------------------------------
    log_dir: str = "logs"
    sample_seconds: float = Field(default=30.0, gt=0)  # resource sample + heartbeat file
    # A heartbeat file older than this many sample intervals is "missing" (not "dead").
    heartbeat_stale_factor: float = Field(default=3.0, ge=1.5)
    resources_max_mb: float = Field(default=5.0, gt=0)  # per resource file before rotation
    resources_backups: int = Field(default=5, ge=1)  # rotated resource files kept
    events_max_mb: float = Field(default=20.0, gt=0)  # per event file before rotation
    # Rotated event files are deleted only once every line is in Postgres and the total
    # exceeds this; unsynced crash evidence is never deleted.
    events_retain_mb: float = Field(default=500.0, gt=0)
    event_sync_seconds: float = Field(default=60.0, gt=0)  # JSONL -> Postgres

    # --- supervisor ---------------------------------------------------------------------
    restart_base_seconds: float = Field(default=5.0, ge=0)
    restart_max_seconds: float = Field(default=300.0, ge=0)
    max_failures: int = Field(default=5, ge=1)  # unexpected exits within failure_window...
    failure_window_seconds: float = Field(default=3600.0, gt=0)  # ...before giving up
    stop_grace_seconds: float = Field(default=60.0, ge=0)  # graceful stop, then kill
    kill_on_stall: bool = False  # kill a worker whose heartbeat stopped (opt-in)
    stall_kill_seconds: float = Field(default=900.0, gt=0)

    @model_validator(mode="after")
    def _validate(self) -> "ObservabilitySettings":
        if self.restart_max_seconds < self.restart_base_seconds:
            raise ValueError("observability.restart_max_seconds < restart_base_seconds")
        return self
