"""Reparse the PDFs whose parse failed (error_kind PARSE_ERROR) with the current parser. Read
only for research data: no run, document or chunk is modified.

    uv run python scripts/pdf_reparse_failures.py [--since 2026-11-04] [--download DIR]
        [--out report.json]

Two groups, never mixed:
- STORED_COPY: bytes of a successful fetch of the same canonical URL in another run (MinIO).
- NEW_DOWNLOAD (with --download DIR): a fresh fetch of the failed URL now, through
  SafeFetcher (same robots, address and size rules); bytes written to DIR only.
Neither is known to be byte-identical to what the failed attempt received: those attempts kept
no bytes (before raw-byte retention). Equal size is reported as equal size, not identity.
"""

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path
from sqlalchemy import text
from water_source_research.acquisition.artifacts import MinioArtifactStore
from water_source_research.acquisition.pdf import parse_pdf
from water_source_research.acquisition.safe_http import FetchError, SafeFetcher
from water_source_research.core.config import get_settings
from water_source_research.db.session import get_engine
from water_source_research.research_queue.checkpointing import run_async

def _parse(url: str, data: bytes) -> dict:
    try:
        doc = parse_pdf(url, data)
        return {"parsed": True, "pages_with_text": len(doc.pages or []),
                "text_chars": len(doc.text), "error": None}
    except Exception as exc:  # reported, never hidden
        return {"parsed": False, "pages_with_text": None, "text_chars": None,
                "error": f"{type(exc).__name__}: {exc}"[:200]}

async def main(since: str, download: str | None) -> list[dict]:
    settings = get_settings()
    engine = get_engine()
    with engine.connect() as c:
        failed = c.execute(text(
            "select d.id, left(d.run_id, 8), j.city_name, d.requested_url, d.canonical_url, "
            "d.byte_size, d.error_message from source_documents d join research_jobs j on "
            "j.run_id = d.run_id where d.error_kind = 'PARSE_ERROR' and d.created_at >= :s "
            "order by d.id"), {"s": since}).all()
        copies = {}
        for fid, _, _, _, canon, _, _ in failed:
            row = c.execute(text(
                "select raw_object_key, byte_size, content_sha256, left(run_id, 8) from "
                "source_documents where canonical_url = :u and status = 'OK' and "
                "raw_object_key is not null order by id limit 1"), {"u": canon}).first()
            if row:
                copies[fid] = row
    store = MinioArtifactStore(settings.artifacts)
    fetcher = SafeFetcher(settings.acquisition)
    out = []
    for fid, run, city, url, _, failed_size, err in failed:
        row = {"document_id": fid, "run": run, "city": city, "url": url,
               "failed_byte_size": failed_size, "failed_error": (err or "")[:120]}
        if fid in copies:
            key, size, sha, from_run = copies[fid]
            bucket, name = key.split("/", 1)
            r = store.client.get_object(bucket, name)
            data = r.read()
            r.close()
            r.release_conn()
            row.update(group="STORED_COPY", copy_from_run=from_run, bytes=len(data),
                       sha256=hashlib.sha256(data).hexdigest(),
                       size_equals_failed=len(data) == failed_size, **_parse(url, data))
        elif download:
            try:
                resp = await fetcher.fetch(url, settings.acquisition.max_pdf_bytes)
                data = resp.body
                sha = hashlib.sha256(data).hexdigest()
                Path(download, f"{fid}_{sha[:12]}.pdf").write_bytes(data)
                row.update(group="NEW_DOWNLOAD", bytes=len(data), sha256=sha,
                           size_equals_failed=len(data) == failed_size,
                           content_type=resp.content_type, **_parse(url, data))
            except FetchError as exc:
                row.update(group="NEW_DOWNLOAD", parsed=False, bytes=None,
                           error=f"download failed: {exc.kind.value}")
        else:
            row.update(group="NO_BYTES", parsed=None)
        out.append(row)
    await fetcher.aclose()
    return out

def cli() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser()
    p.add_argument("--since", default="2026-11-04")
    p.add_argument("--download")
    p.add_argument("--out")
    a = p.parse_args()
    if a.download:
        Path(a.download).mkdir(parents=True, exist_ok=True)
    rows = run_async(main(a.since, a.download))
    for r in rows:
        print(f"{r['group']:12} {r['city']:9} parsed={r.get('parsed')!s:5} "
              f"pages={r.get('pages_with_text')} chars={r.get('text_chars')} "
              f"size_eq={r.get('size_equals_failed')} {r.get('error') or ''} {r['url'][:70]}")
    if a.out:
        Path(a.out).write_text(json.dumps(rows, indent=1, default=str), encoding="utf-8")
    return 0

if __name__ == "__main__":
    raise SystemExit(cli())
