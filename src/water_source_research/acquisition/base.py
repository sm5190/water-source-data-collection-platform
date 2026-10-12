from dataclasses import dataclass

@dataclass(slots=True)
class AcquiredDocument:
    url: str
    title: str | None
    media_type: str
    text: str
    acquisition_method: str
    # PDFs: (1-based page number, page text); chunks then carry page numbers.
    pages: list[tuple[int, str]] | None = None
    retrieved_at: str | None = None  # ISO 8601 UTC
    source_date: str | None = None  # publication/modification date from the document itself
    content_sha256: str | None = None
