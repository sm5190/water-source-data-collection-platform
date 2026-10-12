"""Minimal PDF text adapter (pypdfium2): page-numbered text for evidence chunks.

No OCR: scanned PDFs without a text layer yield empty pages (the document is then EMPTY).
Docling remains the planned richer parser (tables/layout) behind the ``docs`` extra.
"""

import hashlib
import re
from .base import AcquiredDocument

_PDF_DATE = re.compile(r"D:(\d{4})(\d{2})?(\d{2})?")
_HYPHEN_BREAK = re.compile(r"(\w)-\r?\n(\w)")

def is_pdf(body: bytes, content_type: str = "") -> bool:
    return body[:5] == b"%PDF-" or "pdf" in content_type.lower()

def parse_pdf(url: str, body: bytes, max_pages: int = 150) -> AcquiredDocument:
    import pypdfium2 as pdfium  # imported lazily: native library

    pdf = pdfium.PdfDocument(body)
    try:
        pages: list[tuple[int, str]] = []
        for index in range(min(len(pdf), max_pages)):
            page = pdf[index]
            textpage = page.get_textpage()
            try:
                text = textpage.get_text_range()
            finally:
                textpage.close()
                page.close()
            text = _clean(text)
            if text:
                pages.append((index + 1, text))
        meta = pdf.get_metadata_dict(skip_empty=True)
    finally:
        pdf.close()
    title = (meta.get("Title") or "").strip() or None
    return AcquiredDocument(
        url=url,
        title=title,
        media_type="application/pdf",
        text="\n\n".join(t for _, t in pages),
        acquisition_method="DIRECT_HTTP_PDF",
        pages=pages,
        source_date=_pdf_date(meta.get("ModDate") or meta.get("CreationDate")),
        content_sha256=hashlib.sha256(body).hexdigest(),
    )

def _clean(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = _HYPHEN_BREAK.sub(r"\1\2", text)
    lines = [" ".join(line.split()) for line in text.split("\n")]
    # Keep blank lines as paragraph breaks, collapse runs of them.
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip()

def _pdf_date(value: str | None) -> str | None:
    if not value:
        return None
    m = _PDF_DATE.match(value)
    if not m:
        return None
    return "-".join(p for p in m.groups() if p)
