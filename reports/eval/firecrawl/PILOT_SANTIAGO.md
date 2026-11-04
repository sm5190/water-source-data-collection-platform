# Firecrawl fetch fallback: single-city pilot (Santiago)

Date: 2026-11-04. Run `082a8549-9ae5-4011-945b-9adbe6d7c86a`. It used a worker with
`ACQUISITION__FIRECRAWL_ENABLED=true`, concurrency 1, and no other job queued. Firecrawl was
turned off again afterwards.

Commits:
- 2bc39a9: client and settings;
- 4b0b666: escalation, budget, migration 0023;
- d5b92e4: monitoring and check script;
- the credits/proxy fix and the definite-failure fix (this commit).

Full suite: 1239 passed, 37 skipped, before the last two small fixes. The Firecrawl tests pass
after them (28).

## 1. One live fetch (`scripts/firecrawl_check.py`, `live_check.json`)

URL: `https://www.aguasandinas.cl/web/aguasandinas/galer%C3%ADa-patrimonial`, an EMPTY page in a
stored Santiago run whose robots.txt allows it.

| | |
|---|---|
| Status | HTTP 200, same final URL |
| Credits estimated | 1 |
| Team balance | 1025 → 1024 (delta 1) |
| Latency | 3.1 s |
| Representation chosen | MARKDOWN |
| Markdown profile | 23,231 body characters, 219 water terms |
| Our extraction of rawHtml | 10,440 body characters, 105 water terms |

**The response differs from the published schema:**
- **Undocumented `metadata.creditsUsed`:** now recorded as `credits_actual`, apart from the
  estimate, and the budget takes the higher of the two.
- **Undocumented `metadata.proxyUsed`:** now recorded per attempt.
- **Other undocumented fields:** `scrapeId`, `concurrencyLimited`, `language`, `timezone`,
  `favicon`, `viewport`.
- **Missing:** none of the fields relied on (`markdown`, `metadata.url`, `metadata.statusCode`).

**Content preservation:**
- With `onlyMainContent: false`, the markdown keeps navigation menus, which our main-content
  extraction drops. It also has more body text.
- Its water-term count is inflated by the site name ("Aguas Andinas") repeated in the menus. So
  "more water terms" can favour boilerplate.
- Both texts are stored. Whether to discount navigation lines in the profile is an open
  question; nothing was changed.

## 2. Santiago pilot (`pilot_santiago.json`, `scripts/firecrawl_pilot_report.py`)

| Measure | Result |
|---|---|
| Direct fetches | 18 (budget reached in 3 rounds): 9 OK, 3 AUTH_REQUIRED, 3 BOT_CHECK, 1 ROBOTS_DISALLOWED, 1 ROBOTS_UNREACHABLE, 1 TIMEOUT |
| Eligible for Firecrawl | **1** (the TIMEOUT, a SciELO PDF); the other 8 failures are excluded kinds and were not sent |
| Sent | 1 |
| Recovered usable documents | **0**: Firecrawl answered HTTP 500 "All scraping engines failed to retrieve content from this URL" |
| New evidence passages | 0 |
| Claims supported by Firecrawl passages | 0 (none auto-accepted) |
| Credits | estimated 0 charged; the reservation of 2 was kept (recorded UNCERTAIN); team balance 1024 → 1024 (delta 0) |
| Additional correct water sources | **0** (no Firecrawl evidence). The run proposed Maipo River and Mapocho River from direct documents. No Santiago reference dataset is imported, so correctness was not scored |

Unlike earlier Santiago runs (4 and 2 EMPTY pages), this run's discovery returned no EMPTY
pages, so the pilot had almost nothing to recover. **It shows the safeguards and accounting
working on live data. It does not show recovery value.**

## 3. Fix made from the pilot

- **What happened:** the 500 above was recorded UNCERTAIN, keeping a 2-credit reservation.
  Its body states that no content was retrieved, which the billing docs charge at 0, and the
  team balance didn't move.
- **Change:** a 5xx whose body explicitly states a retrieval failure is now FAILED at 0 credits
  (`SCRAPE_FAILED`, test). A generic 5xx (`UNKNOWN_ERROR`) stays UNCERTAIN with its reservation.
- **The pilot's stored record is unchanged.**

## 4. Unrelated finding: PDF parse errors during the two-at-a-time reruns

The 7-city reruns earlier the same day (concurrency 2) lost **16 PDFs** to `PdfiumError: Data
format error`:
- Santiago 7, Toronto 6, Vienna 2, Nairobi 1;
- none on any earlier day;
- none in this one-at-a-time pilot, which read the same World Bank and VAG PDFs without error.

`pypdfium2` is not thread-safe and `acquisition/pdf.py` has no lock, so concurrent parsing is
the probable cause (PROBABLE, not confirmed). A process-wide lock around PDF parsing is the
likely fix. It was not changed here.

## Not done (stopped here, as asked)

- No broader rollout; Firecrawl is off in the running worker.
- No second live fetch to compare `onlyMainContent` settings.
- Before a rollout, a city whose stored runs have several EMPTY pages would test recovery
  better. Of the stored runs, Santiago's earlier runs had 6 EMPTY pages and the overall stored
  set had 41.
