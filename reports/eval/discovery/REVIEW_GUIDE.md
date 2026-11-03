# Review guide — identity, labels and utility expectations

Every entry below is a **draft (DRAFT / UNCONFIRMED)** and stays one until you confirm it. Research
uses only APPROVED identity entries; evaluation reports a metric as *validated* only when it
rests on confirmed labels. Uncertain or conflicting entries come first.

Where to record decisions:
- `config/city_identity.yaml`: set an entry's `status: APPROVED`, and fill `approval` at the top
  (name, date). To demote a name, move it from `names` to `unverified_names`. To promote one,
  add a source `{source: person, id: <your name>}`. Detail per city:
  `identity_review.md`.
- `tests/evaluation/discovery/labels_sample.csv` and `fresh_pool.csv`: fill
  `confirmed_relevant`, `confirmed_publisher_kind` and `confirmed_serving_utility`, plus
  `reviewer`, `reviewed_on` and optional `reviewer_notes`; then set `label_status: CONFIRMED`. The
  `proposed_*` columns are my drafts. The sheet hides system output on purpose.
- `tests/evaluation/discovery/utility_expectations.yaml`: correct the entries, then set
  `status: CONFIRMED`.

Allowed values: relevant YES / NO / UNCERTAIN; publisher UTILITY_OPERATOR, GOVERNMENT,
REGULATOR, ACADEMIC, TECHNICAL, INDEPENDENT_DATABASE, NEWS, DIRECTORY, COMMERCIAL, OTHER,
UNKNOWN; serving utility YES / NO / UNKNOWN / N_A.

## 1. `config/city_identity.yaml` (56 cities)

### Conflicts first

| City | Issue | Suggestion |
|---|---|---|
| **Hyderabad** (evaluated) | The Urdu names حیدر آباد / حیدرآباد are *used for matching* (official_local). The same spelling names Hyderabad, Sindh, Pakistan (1.9 M), the large homonym. | Move both to `unverified_names`. They only matter in Urdu text, and there they are ambiguous. |
| **Luanda** (evaluated) | The historical spelling "Loanda" is also Loanda, Paraná, Brazil (23k). | Keep it (the country check separates them) or demote it; low risk either way. |
| **Santiago** (evaluated) | "Gran Santiago" / "Greater Santiago" are only unverified names, not context names. The region name "Santiago Metropolitan Region" contains the city name, which is why queries say "Santiago Santiago …". | Add both metro names as context (supporting only, never the city). The query wording is a code fix (F7 in `DIAGNOSIS.md`). |
| Belém | "Mairi" (official_local) is also Mairi, Bahia, Brazil. | Demote, or keep it with the Pará region check. |
| Paris | "Pariz" is also Pārīz, Kerman, Iran. | Demote (a minor spelling). |
| Rome | "Roma" is also Roma, Botoșani, Romania. | **Keep:** it is the official Italian name. |
| Bogotá, Kyiv, Ho Chi Minh City | The region is not verified (the city is its own first-level unit; the GeoNames and Wikidata chains disagree). | Confirm the region by hand, or leave the entry as DRAFT. |

### Evaluated cities (needed for comparison C), no conflict found

| City | Region | Names used | Homonyms | Check |
|---|---|---|---|---|
| Houston | Texas (US-TX) | none | Houston MS, MO, PA, AK; Scotland; BC | "City of Houston" is unverified; promote it if you want it matched. |
| Nairobi | Nairobi City County (KE-30) | none | none | The region contains the city name; fine. |
| Vienna | Vienna (AT-9, city-state) | Wien, Beč, Bécs | Vienna VA, WV, GA, IL | "Wien" equals the region (expected for a city-state). |
| Toronto | Ontario (CA-ON) | City of Toronto | Toronto NSW (AU), Toronto OH | 8 metro context names (GTA etc.). |

The other 45 cities had no flagged issue. Approve them in bulk or leave them DRAFT; they are not
used in Milestone 3.

## 2. `labels_sample.csv` (69 documents: Santiago, Houston, Luanda, Nairobi, Vienna)

Drafts: 32 relevant, 30 not relevant, 7 uncertain; serving utility 16 yes, 9 no, 1 unknown, 43
N_A.

**Uncertain or unknown first (8):**

| id | City | Document | Draft | Why uncertain |
|---|---|---|---|---|
| D020 | Vienna | environment.ec.europa.eu (PFAS news) | UNCERTAIN, GOVERNMENT | Danube bank filtrate sampled at Vienna; link to the drinking water unclear |
| D035 | Luanda | english.www.gov.cn (Chinese-built water projects) | UNCERTAIN, GOVERNMENT | Near Luanda; link to the supply not stated |
| D046 | Luanda | documents1.worldbank.org (groundwater assessment) | UNCERTAIN, TECHNICAL | Quotes only list cities |
| D050 | Nairobi | wra.go.ke situation reports | UNCERTAIN, REGULATOR | National list page |
| D061 | Houston | greaterhoustonwater.com | YES, publisher UNKNOWN, serving UNKNOWN | Northeast plant expansion project site; publisher unclear (city or contractor consortium) |
| D063 | Nairobi | nairobiwater.co.ke/locations | UNCERTAIN, UTILITY_OPERATOR, YES | Navigation-only quote |
| D065 | Santiago | dga.mop.gob.cl water-quality inventory | UNCERTAIN, GOVERNMENT | National, not Santiago-specific |
| D069 | Santiago | ine.gob.cl environment yearbook | UNCERTAIN, GOVERNMENT | Gran Santiago tables; water content not quoted |

Then the remaining 61 rows, by city. The quickest check is the `proposed_basis` column against
the quotes.

## 3. `fresh_pool.csv` (56 documents: Santiago, Houston; the recall pool of the fresh runs)

Drafts: 22 relevant, 25 not relevant, 9 uncertain.

| id | City | Document | Draft | Why uncertain |
|---|---|---|---|---|
| F008 | Santiago | aguasandinas.cl | UNCERTAIN, UTILITY_OPERATOR, YES | Only a bot-check page was fetched (no content) |
| F053 | Santiago | jstor.org resrep42654.10 | UNCERTAIN, ACADEMIC | Bot-check page, no content |
| F010 | Santiago | PLOS ONE, ecosystem services | UNCERTAIN, ACADEMIC | Santiago–Valparaíso region; water supply unclear |
| F018 | Santiago | dga.mop.gob.cl reports 2023 | UNCERTAIN, GOVERNMENT | List page |
| F046 | Santiago | SciELO, fluoride intake | UNCERTAIN, ACADEMIC | Communes with fluoridated public water; supply facts thin |
| F003 | Houston | twdb.texas.gov water use dashboard | UNCERTAIN, GOVERNMENT | Statewide dashboard |
| F027 | Houston | harriscountyutility.com | UNCERTAIN, COMMERCIAL | Homeowner guide: Houston Public Works vs MUDs |
| F031 | Houston | pubs.usgs.gov cir1182 | UNCERTAIN, GOVERNMENT | National subsidence report |
| F042 | Houston | publichealth.harriscountytx.gov | UNCERTAIN, GOVERNMENT | Says Houston's system is the largest in Texas |

Decide for F008 and F053 whether a document whose content couldn't be fetched counts as
relevant. I suggest UNCERTAIN, which is excluded from precision, and noting it as "inaccessible".

No URL appears in both sheets with different drafts (0 conflicts).

## 4. `utility_expectations.yaml`

| City | Draft | Uncertain |
|---|---|---|
| Santiago | Serving: Aguas Andinas (aguasandinas.cl), Aguas Cordillera, Aguas Manquehue, SMAPA (Maipú). Predecessor: EMOS. Not serving: Almar Water Solutions, Aguas Horizonte | No hosts are given for the three smaller operators. Should the investor site aguasandinasinversionistas.cl count as Aguas Andinas' host? (The registry binds it.) |
| Houston | Houston Public Works (aliases Houston Water, City of Houston Public Works; houstonpublicworks.org, houstontx.gov); Coastal Water Authority (wholesale). Not serving: Houston MS, a contractor | Should houstontx.gov (the whole city site) count as the operator's host? Should other wholesalers (e.g. a river authority) be listed? |
| Nairobi | Nairobi City Water and Sewerage Company (aliases; nairobiwater.co.ke). Not serving: Oloolaiser | Should the Athi Water Works Development Agency (bulk works) be listed as a wholesaler? |
| Luanda | EPAL (epal.co.ao) | Is the alias "Luanda Provincial Water Company" right? |
| Vienna | Vienna Water / Wiener Wasser (wien.gv.at) | The host is the whole city site, the same question as Houston. |
| **Toronto, Hyderabad** | **Missing.** | Needed if comparison C reports utility identification for them. Say the word and I'll draft them (marked UNCONFIRMED). |

## 5. What the labels do not cover yet

The 7 new full runs and the future runs for comparison C fetched documents that are in neither
sheet. Measuring document precision and pooled recall for C needs a third pool, built after
those runs (evaluation-only, same blind format). I'll draft it once the C runs exist.
