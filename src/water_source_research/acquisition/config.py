"""Acquisition policy. Loaded as ``Settings.acquisition`` (env: ``ACQUISITION__<FIELD>``)
and raw-artifact storage as ``Settings.artifacts`` (env: ``ARTIFACTS__<FIELD>``)."""

from pydantic import BaseModel, Field, model_validator

class AcquisitionSettings(BaseModel):
    enabled: bool = True

    # Volume: documents fetched per discovery round / per run, and per host per round.
    max_documents_per_round: int = Field(default=6, ge=1, le=50)
    max_documents_total: int = Field(default=18, ge=1, le=200)
    max_per_host: int = Field(default=2, ge=1)

    # Politeness: global in-flight cap, one request at a time per host with a minimum gap.
    max_concurrency: int = Field(default=4, ge=1, le=32)
    per_host_min_interval_seconds: float = Field(default=1.0, ge=0)

    # Per request
    timeout_seconds: float = Field(default=30, gt=0)
    max_redirects: int = Field(default=5, ge=0, le=10)
    max_html_bytes: int = Field(default=5_000_000, ge=1)
    max_pdf_bytes: int = Field(default=25_000_000, ge=1)
    max_pdf_pages: int = Field(default=150, ge=1)
    respect_robots: bool = True
    user_agent: str = "VT-Water-Source-Research/0.1 (research crawler; respects robots.txt)"

    # Chunking (characters). Overlap is carried into the next chunk only when it fits.
    chunk_max_chars: int = Field(default=2000, ge=200)
    chunk_overlap_chars: int = Field(default=200, ge=0)

    @model_validator(mode="after")
    def _validate(self) -> "AcquisitionSettings":
        if self.max_documents_total < self.max_documents_per_round:
            raise ValueError("acquisition.max_documents_total < max_documents_per_round")
        if self.chunk_overlap_chars >= self.chunk_max_chars:
            raise ValueError("acquisition.chunk_overlap_chars must be < chunk_max_chars")
        return self

class ArtifactSettings(BaseModel):
    """S3-compatible store (MinIO in docker-compose) for raw downloaded documents."""

    enabled: bool = True
    endpoint: str = "localhost:9000"  # host:port, no scheme
    access_key: str = "minioadmin"
    secret_key: str = "minioadmin"
    bucket: str = "source-documents"
    secure: bool = False
