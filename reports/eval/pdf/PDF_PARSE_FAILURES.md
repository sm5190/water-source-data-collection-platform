# PDF parse failures of 2026-11-04 (P0)

## What failed

16 documents in the 7-city reruns of 2026-11-04 03:45–03:55 UTC:

| City | Failed PDFs |
|---|---|
| Santiago | 7 |
| Toronto | 6 |
| Vienna | 2 |
| Nairobi | 1 |

- **Error, every time:** `PdfiumError: Failed to load document (PDFium: Data format error).` at
  `pypdfium2.PdfDocument(body)`.
- **History:** no PARSE_ERROR on any earlier day; for example, 118 PDFs were parsed on
  2026-11-02.

## Environment

- **Library:** pypdfium2 5.14.0 / PDFium 156.0.8076.0, installed 2026-10-11 and unchanged.
- **One worker process, `--concurrency 2`:** two run graphs share one event loop. Each round
  fetches up to 6 documents with `asyncio.gather`, and each parse runs in `asyncio.to_thread`.
  So parses ran in parallel threads of one process, both within a run (the failures came in
  same-second bursts) and across its two runs.
- **No shared files:** `parse_pdf` parses from memory and creates no temporary files. It is the
  only PDFium user in the process.
- **Downloads looked complete:** each failed record's `byte_size` equals the size of
  successful copies of the same URL in other runs.

## Reproduction

The script `pdf_repro.py` (in the session scratchpad) parsed the 9 stored copies of the same
URLs. It is read only.

| Mode | Parse errors |
|---|---|
| Sequential, one process | 0 / 9 |
| 2 threads, one process | **20 / 27**, the exact production error |
| 4 and 8 threads, one process | 27 / 27 |
| 3 separate processes at once, each sequential | **0 / 108** |
| With the fix: 2, 4 and 8 threads | **0 / 27 each** |

Without the lock, the threaded test process also crashed natively at interpreter exit
(Windows 0xc000001d in `FPDF_DestroyLibrary`). That is further sign that concurrent use
corrupts PDFium's state in the process.

**Cause:** PDFium is not thread-safe within a process. Separate processes don't interfere,
because each loads its own PDFium and parses from memory. The correct scope is therefore one
lock per process. A cross-process lock would serialize independent workers for no benefit.

## Fix

- **Lock:** `acquisition/pdf.py` holds a module-level `threading.Lock` for the whole PDFium
  session (open → pages → metadata → close). Exceptions propagate unchanged and are still
  recorded as PARSE_ERROR with their message. The extracted text is unchanged.
- **Raw bytes kept on parse failure:** `acquisition/service.py` now stores the raw bytes of a
  fetched document even when its parse fails. They are content addressed, and the record gets
  `content_sha256` and `raw_object_key`, so a future failure can be replayed byte for byte.
  The 16 failures here kept no bytes.
- **Tests:** `tests/test_pdf_concurrency.py` has three tests:
  - **Invariant:** with 8 threads, PDFium documents are never open concurrently. This test
    fails without the lock, as checked.
  - **Parallel results:** parallel parses equal sequential ones.
  - **Failed parse:** it keeps its bytes and its error.
  
  Generated test PDFs don't trigger the race even at 500 KB, which is why the invariant is
  tested directly.

## Reparse of all 16 (`scripts/pdf_reparse_failures.py`, `reparse.json`)

Nothing in the affected runs was modified.

| Group | PDFs | Parsed now | Size equal to the failed fetch |
|---|---|---|---|
| STORED_COPY: bytes of a successful fetch of the same URL in another run (MinIO) | 9 | **9** | 9 |
| NEW_DOWNLOAD: the failed URL fetched again on 2026-11-04 through SafeFetcher (bytes in the scratchpad only) | 7 | **7** | 7 |

**Byte identity can't be established for either group.** The failed attempts kept no bytes and
recorded no hash, so equal size is equal size only.

All 16 parse with the fixed parser: 2 to 150 pages with text, 4.9k to 380k characters. Neither
group was ever corrupt as a file, which is consistent with the reproduction.

## Not done

The affected runs (`5f65fcf5` cancelled; `dff9c0c8`, `1ec84b10` and `7bd70cce` awaiting review)
still lack these documents. Re-researching or reprocessing them is your decision.
