"""Observability settings. Loaded as ``Settings.observability`` (env: ``OBSERVABILITY__<FIELD>``).

Everything here is optional: with the defaults no telemetry backend is needed, and worker
crash evidence (supervisor + local event files) works with no tracing at all.
"""

from typing import Literal
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

    # --- tracing (Phase 2) ---------------------------------------------------------------
    otel_enabled: bool = False  # off: no provider is installed, spans are no-ops
    trace_exporter: Literal["otlp", "file", "none"] = "otlp"
    otlp_endpoint: str = ""  # "" = top-level OTEL_EXPORTER_OTLP_ENDPOINT
    # Root sampling ratio for ordinary runs. A run submitted with trace=true is always
    # sampled in full; external trace headers can never force sampling.
    sample_ratio: float = Field(default=1.0, ge=0.0, le=1.0)
    environment: str = "local"
    span_queue_size: int = Field(default=2048, ge=64)  # bounded: spans beyond are dropped
    span_batch_size: int = Field(default=512, ge=1)
    export_timeout_seconds: float = Field(default=5.0, gt=0)
    trace_file: str = ""  # file exporter path ("" = <log_dir>/traces/<service>-<pid>.jsonl)
    trace_file_max_mb: float = Field(default=20.0, gt=0)
    trace_file_backups: int = Field(default=3, ge=0)
    trace_ui_url: str = "http://localhost:16686"  # Jaeger UI (links in /research/runs/…/traces)

    # --- LLM observability (Phase 3) -----------------------------------------------------
    # "off": spans carry ids, usage, versions, status and keyed hashes only. "truncated"
    # (opt-in, debugging): also redacted prompt/response text up to 2000 chars; it may
    # contain evidence passages from acquired documents.
    capture_llm_content: Literal["off", "truncated"] = "off"
    # Langfuse (optional; needs LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY / LANGFUSE_HOST).
    langfuse_enabled: bool = False
    langfuse_flush_interval_seconds: float = Field(default=5.0, gt=0)
    langfuse_ui_url: str = ""  # e.g. https://cloud.langfuse.com/project/<id> (trace links)

    # --- usage ledger and monitoring (Cycle 2) ------------------------------------------
    usage_ledger: bool = True  # record every external API call in provider_usage
    collector_health_url: str = "http://localhost:13133/"
    jaeger_api_url: str = "http://localhost:16686"
    health_probe_timeout_seconds: float = Field(default=1.5, gt=0)

    @model_validator(mode="after")
    def _validate(self) -> "ObservabilitySettings":
        if self.restart_max_seconds < self.restart_base_seconds:
            raise ValueError("observability.restart_max_seconds < restart_base_seconds")
        return self
