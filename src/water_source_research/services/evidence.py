import hashlib
import re
from water_source_research.acquisition.base import AcquiredDocument
from water_source_research.schemas.research import EvidenceChunk

_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
_SENTENCE_END = re.compile(r"(?<=[.!?;:])\s+|\n")

def chunk_id(url: str, page_number: int | None, text: str) -> str:
    """Deterministic: the same text of the same document (page) always gets the same id."""
    page = "" if page_number is None else str(page_number)
    return hashlib.sha1(f"{url}|{page}|{text}".encode()).hexdigest()[:16]

def chunk_document(
    doc: AcquiredDocument,
    source_type: str | None = None,
    max_chars: int = 2000,
    overlap_chars: int = 0,
) -> list[EvidenceChunk]:
    """Split a document into paragraph-aligned chunks of at most ~max_chars.

    Every chunk is a verbatim span of the extracted text (quotes must be found in it). PDFs
    are chunked per page so chunks carry page numbers. A paragraph longer than max_chars is
    split into windows at sentence/whitespace boundaries. With ``overlap_chars`` > 0, the
    trailing paragraph (or the tail window of a split paragraph) of a chunk is repeated at
    the start of the next one when it fits in both, so facts spanning a boundary survive.
    """
    sections = doc.pages if doc.pages is not None else [(None, doc.text)]
    chunks: list[EvidenceChunk] = []
    seen: set[str] = set()
    for page_number, text in sections:
        for piece in _split(text, max_chars, overlap_chars):
            cid = chunk_id(doc.url, page_number, piece)
            if cid in seen:
                continue
            seen.add(cid)
            chunks.append(EvidenceChunk(
                chunk_id=cid,
                url=doc.url,
                text=piece,
                title=doc.title,
                page_number=page_number,
                source_type=source_type,
                acquisition_method=doc.acquisition_method,
                retrieved_at=doc.retrieved_at,
            ))
    return chunks

def _split(text: str, max_chars: int, overlap_chars: int) -> list[str]:
    paragraphs: list[str] = []
    for p in _PARAGRAPH_BREAK.split(text):
        p = p.strip()
        if not p:
            continue
        if len(p) > max_chars:
            paragraphs.extend(_windows(p, max_chars, overlap_chars))
        else:
            paragraphs.append(p)

    groups: list[str] = []
    current: list[str] = []
    size = 0
    for p in paragraphs:
        if current and size + len(p) + 2 > max_chars:
            groups.append("\n\n".join(current))
            tail = current[-1]
            if overlap_chars and len(tail) <= overlap_chars and len(tail) + len(p) + 2 <= max_chars:
                current, size = [tail], len(tail) + 2
            else:
                current, size = [], 0
        current.append(p)
        size += len(p) + 2
    if current:
        groups.append("\n\n".join(current))
    return groups

def _windows(text: str, max_chars: int, overlap_chars: int) -> list[str]:
    """Cut a long paragraph into <= max_chars windows ending at sentence (else whitespace)
    boundaries; consecutive windows overlap by up to ``overlap_chars``."""
    out: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(n, start + max_chars)
        if end < n:
            cut = _last_boundary(text, start + max_chars // 2, end)
            end = cut if cut > start else end
        piece = text[start:end].strip()
        if piece:
            out.append(piece)
        if end >= n:
            break
        next_start = end
        if overlap_chars:
            back = max(start + 1, end - overlap_chars)
            ws = text.find(" ", back, end)
            next_start = ws + 1 if ws != -1 else end
        start = next_start if next_start > start else end
    return out

def _last_boundary(text: str, lo: int, hi: int) -> int:
    best = -1
    for m in _SENTENCE_END.finditer(text, lo, hi):
        best = m.end()
    if best != -1:
        return best
    ws = text.rfind(" ", lo, hi)
    return ws + 1 if ws != -1 else -1
