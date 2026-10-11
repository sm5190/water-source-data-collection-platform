import hashlib
import re
from water_source_research.acquisition.base import AcquiredDocument
from water_source_research.schemas.research import EvidenceChunk

_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")

def chunk_document(
    doc: AcquiredDocument,
    source_type: str | None = None,
    max_chars: int = 2000,
) -> list[EvidenceChunk]:
    """Split a document into paragraph-aligned chunks of at most ~max_chars.

    Paragraphs longer than max_chars become their own chunk rather than being cut, so every
    chunk is a verbatim span of the source text.
    """
    paragraphs = [p.strip() for p in _PARAGRAPH_BREAK.split(doc.text) if p.strip()]
    groups: list[str] = []
    current: list[str] = []
    size = 0
    for p in paragraphs:
        if current and size + len(p) + 2 > max_chars:
            groups.append("\n\n".join(current))
            current, size = [], 0
        current.append(p)
        size += len(p) + 2
    if current:
        groups.append("\n\n".join(current))

    return [
        EvidenceChunk(
            chunk_id=hashlib.sha1(f"{doc.url}|{text}".encode()).hexdigest()[:16],
            url=doc.url,
            text=text,
            title=doc.title,
            source_type=source_type,
            acquisition_method=doc.acquisition_method,
        )
        for text in groups
    ]
