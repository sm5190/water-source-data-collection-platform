# Milestone 3.1 — geographic-aware extraction and validation: implementation plan (for approval)

Status: **proposed, not implemented.** It builds on the approved identity, passage geography,
feature location and the relationship graph. Acceptance rules stay as they are at first. It is
generic for every city, and no reference data is read by research.

## What the code does today (inspected)

- **Extraction header** (`context_manager/manager.py::_header`, extract-5): `City: <city>,
  <country>` plus the target fields. There is no region, no homonyms and no note that sources may
  lie outside the city. Passages render as `[S#] title / url` with no place information.
- **Relevance gate** (`context_manager/relevance.py`): geographic rules only. An off-topic
  document about the right city can fill a bundle (§6 of `PRE_EXTRACTION_CHECKS.md`: 6
  documents, 6 spurious claims, including "Sai Reddy Lake").
- **Claim relations** (`llm/extraction.py`): they keep a quote and URL, but no chunk id, page or
  character span.
- **Validation:**
  - Checks are only PASS/WARN/FAIL, and AUTO_STRICT accepts only allow-listed warnings, so
    **any new WARN would tighten acceptance**.
  - `OUT_OF_AREA` (normalization) still judges by the publisher: "every citation is another US
    state's government site". That conflicts with the publisher-is-not-the-subject rule.
- **Review cards** (`ui/review.py`): no passage place, feature location, source type or
  relationships.

## Dependencies

| Needed | State |
|---|---|
| Approved identities | **Pending your approval** (§4 of the checks). Without approval everything below falls back to today's name-only behaviour and records geo_basis NAME_ONLY. |
| Passage geography, feature location, typing, graph | Done (Checkpoints B–E) |
| VT VPN | Needed only for the replay and end-to-end steps (E6, E7) |

## Steps

**E1. Identity approval helper** (no research change)
- `scripts/approve_identity.py <city> --reviewer <name>` sets `status: APPROVED`, fills the
  `approval` block with reviewer and date, and refuses an entry that has an open flag (e.g.
  Santiago's website line) until it is edited.
- After approval, geo_basis is APPROVED in normal runs. Identity signatures change, so caches
  re-key.

**E2. Geographic prompt context** (extract-6; changes model input, not acceptance)
- Header from the approved identity, for example:
  > `City: Houston, Texas, United States (not: Houston, Mississippi; …up to 3 listed homonyms).
  > Water sources may lie outside the city, including upstream, in other regions or countries.`
- A short tag per source: `[S3] … — place: target / uncertain / section "Water supply of X"`.
  The tag is taken from `match_place`/`section_place`; feature locations appear as "source
  located in Y" when present.
- About +40 tokens per call. All EXTRACT response caches become invalid once; this is measured
  with `token_audit.py`.

**E3. Topical requirement before a model call** (changes which calls are made, not acceptance)
- Besides the geographic rules, a passage must match the field's topic: its keywords, or the
  water-supply vocabulary for source fields, in the passage or its section heading.
- Off-topic passages: no call, event `skip_reason: OFF_TOPIC`, and the chunk is not marked seen.
- Never applied as a geographic exclusion. Foreign publishers and distant sources are untouched.
- retrieval 13. Measured with `counterfactual_replay.py` (no model calls): slots no longer sent,
  and every claim they had produced, especially correct ones lost.

**E4. Claim provenance** (extract-6, schema-additive)
- Each citation and relation gets chunk id, page and character span at grounding time.
- Source claims get `qualifiers.geo`: passage place (status + reason), feature location, and the
  ids of their graph relationships (CANDIDATE included, labelled).
- This is informational only; no check reads it (the graph isolation guard is extended to
  `qualifiers.geo`).

**E5. Geographic validation signals: recorded, not enforced**
- New signals are stored as **qualifiers, not checks**, so acceptance can't change:
  - `PASSAGE_OTHER_PLACE`: the cited sentence or section is a homonym or other place;
  - `PASSAGE_PLACE_UNCERTAIN`;
  - `SOURCE_LINK_NOT_STATED`: a source claim with no supply relationship to the city or its
    utility;
  - `OUTSIDE_SOURCE_LOCATED`: a source with a stated location elsewhere. This is kept and never
    penalized.
- They are shown on review cards. The revalidation report counts, per field, how many AUTO
  values each signal would have touched. Promoting any of them to WARN is a later decision with
  those numbers.
- `OUT_OF_AREA`: measured only. A counterfactual reports which values a passage-based version
  (instead of the publisher-based one) would change. The rule itself is unchanged.

**E6. Review cards**
- The simple review UI shows, per candidate:
  - the passage place and the source's location;
  - the entity type ("lake, not shown to be natural", "reservoir", "region");
  - stated relationships, with status and quote, e.g. "Río Maipo → Aguas Andinas (verified, MMA
    document)".
- No merging. Possible equivalences are shown as "possibly the same as …" for a person to decide.

**E7. Evaluation**
1. **Offline (no model calls):** tests; no-LLM revalidation of the 8 M1 + 7 M3 runs. Expect 0
   AUTO changes from E4/E5; any change is listed with its cause. Counterfactual replay for E3.
2. **Controlled replay** (saved documents, VT VPN, persistent cache off): 4 cities (Houston,
   Santiago, Vienna, Hyderabad), arms extract-5 vs extract-6 (+E3), ×2 repeats for model
   variance. Report proposed values right/wrong (cache-only judge + reference), spurious source
   claims, upstream/transboundary sources kept, tokens and calls, and the review burden.
3. **Controlled end-to-end** (Phase 7 of the rev-2 plan): baseline vs Milestone 3.1 on recorded
   searches first, then live A-B-B-A, with geo_basis recorded per run and absolute counts with
   variance.
- **Checkpoint G** after 1, a checkpoint after 2, then the final Milestone 3.1 report.

## Tests (synthetic, generic)

- Header lists the region and homonyms only for approved identities; name-only otherwise.
- Source tags: target / uncertain / section.
- Topical requirement:
  - a corporate report naming the city → no call;
  - a utility page → call;
  - an upstream dam page naming the city → call;
  - a foreign publisher's on-topic page → call.
- Citation span and page recorded.
- Qualifiers set but no check added (an acceptance-invariance test on the mocked worlds).
- Review card renders relationships and types.

## Risks

- **E2** may change model outputs in either direction. The topic note could raise recall of
  distant sources and also raise noise. Only the replay can tell.
- **E3** could drop a correct passage that uses unusual wording. Measured before it ships;
  keyword lists are per field and multilingual (en/es/pt/de/fr).
- **Re-keying caches** costs tokens once, measured.

## Decisions requested

1. Approve the identities, or say which stay DRAFT (Santiago's website, Luanda's missing site).
2. Include E3 (topical requirement) in this phase, or keep it for later.
3. The E7 replay needs the VT VPN on, for about 4 cities × 2 arms × 2 repeats.
