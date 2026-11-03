# Milestone 3.1 Phase 2: pilot identity review (7 cities)

Generated from `config/city_identity.yaml` after the two curation rules (`scripts/build_city_identity.py::curate`). **Every entry is still DRAFT.** Research uses only APPROVED entries; nothing here is approved until you sign off.

To approve an entry: check it below, edit the YAML if needed, set its `status: APPROVED` and fill `approval` at the top of the file.

Sources: GeoNames dumps (CC BY 4.0) retrieved 2026-11-02 – 2026-11-02; Wikidata (CC0) entity JSON. The reference workbook is not an input.

| City | Region (admin1) | ISO 3166-2 | Region verified | Country | ccTLD | Names used | Unverified | Context | Homonyms (>= 100k) | Flags |
|---|---|---|---|---|---|---|---|---|---|---|
| Houston | Texas | US-TX | yes | United States of America (US) | .us | 1 | 10 | 7 | 0 of 6 | — |
| Santiago | Santiago Metropolitan Region | CL-RM | yes | Chile (CL) | .cl | 2 | 10 | 2 | 11 of 40 | 11 large homonym(s); 2 note(s) |
| Vienna | Vienna | AT-9 | yes | Austria (AT) | .at | 4 | 0 | 1 | 0 of 4 | 1 note(s) |
| Hyderabad | Telangana | IN-TS | yes | India (IN) | .in | 5 | 8 | 0 | 1 of 1 | 1 large homonym(s); 2 note(s) |
| Nairobi | Nairobi City County | KE-30 | yes | Kenya (KE) | .ke | 1 | 6 | 0 | 0 of 0 | — |
| Luanda | Luanda Province | AO-LUA | yes | Angola (AO) | .ao | 4 | 12 | 0 | 0 of 4 | — |
| Toronto | Ontario | CA-ON | yes | Canada (CA) | .ca | 2 | 5 | 8 | 0 of 2 | — |

## Houston, United States of America  (status DRAFT)

- **Canonical name:** Houston (catalog); GeoNames 4699066, Wikidata Q16555
- **Region:** Texas (state, US-TX), verified = True; sources: geonames 4736286 `admin1CodesASCII`, wikidata Q1439 `P131/P1566`, iso3166 US-TX `P300`
  - region names used: Texas, State of Texas, Estado De Texas, Tejas, Kekeka
- **Country:** United States of America (ISO2 US, ISO3 USA, ccTLD .us); other names: United States, Estados Unidos, ‘Amelika Hui Pū ‘ia; Wikidata Q30
- **Names used for matching** (besides the catalog name): none
- **Unverified (never used for matching):** Space City, H-Town, City of Houston, USHOU, Houston TX, Petro metro, Houston (Texas), Ciudad de Houston, Ciudad Espacial, Houston City
- **Context names (metro/urban area, at most PROBABLE, never the city):** Greater Houston, Houston metropolitan area, Houston area, Houston metro area, Houston–The Woodlands–Sugar Land-Galveston, Houston MSA, Gran Houston
- **Homonyms:** Houston (Scotland, United Kingdom, pop 6420; geonames 2646507); Houston (Mississippi, United States, pop 3544; geonames 4430529); Houston (British Columbia, Canada, pop 2993; geonames 5977783); Houston (Alaska, United States, pop 2206; geonames 5864312); Houston (Missouri, United States, pop 2082; geonames 4391354); Houston (Pennsylvania, United States, pop 1266; geonames 5194369)
- **Check before approving:** region and country as above; every name in *Names used* is really this city; nothing in *Unverified* or *Context* should be a name of the city itself.

## Santiago, Chile  (status DRAFT)

- **Canonical name:** Santiago (catalog); GeoNames 3871336, Wikidata Q2887
- **Region:** Santiago Metropolitan Region (region, CL-RM), verified = True; sources: geonames 3873544 `admin1CodesASCII`, wikidata Q2131 `P131/P1566`, iso3166 CL-RM `P300`
  - region names used: Santiago Metropolitan Region, Región Metropolitana de Santiago, Region Metropolitana de Santiago de Chile, Región metropolitana, Metropolitana
- **Country:** Chile (ISO2 CL, ISO3 CHL, ccTLD .cl); other names: none; Wikidata Q298
- **Names used for matching** (besides the catalog name): Santiago de Chile (es: wikidata Q2887 `label:es`, wikidata Q2887 `P1448`, geonames 3871336 `alternatenames`)
- **Unverified (never used for matching):** Santiago del Nuevo Extremo, Santiago de la Nueva Extremadura, Santiago de Nueva Extremadura, Ciudad de Santiago, Ciudad de Santiago de Chile, Metrópolis de Santiago, Capital de Chile, La Capital de Chile, La Capital, CiudadSantiago
- **Context names (metro/urban area, at most PROBABLE, never the city):** Greater Santiago, Gran Santiago
- **Homonyms:** Santiago de Leon de Caracas (Distrito Federal, Venezuela, pop 3000000; geonames 3646738); Santiago de Guayaquil (Guayas, Ecuador, pop 2723665; geonames 3657509); Santiago de Cali (Valle del Cauca Department, Colombia, pop 2392877; geonames 3687925); Santiago de Querétaro (Querétaro, Mexico, pop 1594212; geonames 3991164); Santiago de los Caballeros (Santiago Province, Dominican Republic, pop 1200000; geonames 3492914); Santiago de Guatimala (Guatemala, Guatemala, pop 994938; geonames 3598132); Santiago de Cuba (Santiago de Cuba Province, Cuba, pop 555865; geonames 3536729); Santiago de los Caballeros de Merida (Mérida, Venezuela, pop 300000; geonames 3632308); Santiago del Estero (Santiago del Estero, Argentina, pop 252192; geonames 3835869); Santiago de Surco (Lima region, Peru, pop 251648; geonames 3928245); Santiago (Cagayan Valley, Philippines, pop 108414; geonames 1687801); Santiago de Compostela (Galicia, Spain, pop 99536; geonames 3109642); … 28 more (all listed in the YAML)
- **Notes:** curation: 'Greater Santiago' -> context name (metropolitan alias) | curation: 'Gran Santiago' -> context name (metropolitan alias)
- **Check before approving:** region and country as above; every name in *Names used* is really this city; nothing in *Unverified* or *Context* should be a name of the city itself.

## Vienna, Austria  (status DRAFT)

- **Canonical name:** Vienna (catalog); GeoNames 2761369, Wikidata Q1741
- **Region:** Vienna (state, AT-9), verified = True; sources: geonames 2761367 `admin1CodesASCII`, wikidata Q1741 `P131/P1566`, iso3166 AT-9 `P300`
  - region names used: Vienna, Wien, Beč
- **Country:** Austria (ISO2 AT, ISO3 AUT, ccTLD .at); other names: Österreich, Ausztria, Austrija; Wikidata Q40
- **Names used for matching** (besides the catalog name): Wien (de-at: wikidata Q1741 `label:de`, wikidata Q1741 `P1448`, geonames 2761369 `alternatenames`); Beč (hr: wikidata Q1741 `label:hr`, geonames 2761369 `alternatenames`); Bécs (hu: wikidata Q1741 `label:hu`, geonames 2761369 `alternatenames`)
- **Unverified (never used for matching):** none
- **Context names (metro/urban area, at most PROBABLE, never the city):** Vienna-Bratislava metropolitan region
- **Homonyms:** Vienna (Virginia, United States, pop 16522; geonames 4791160); Vienna (West Virginia, United States, pop 10573; geonames 4825976); Vienna (Georgia, United States, pop 3777; geonames 4228440); Vienna (Illinois, United States, pop 1647; geonames 4252025)
- **Notes:** the city is itself a first-level unit (Wikidata P300 on the city item)
- **Check before approving:** region and country as above; every name in *Names used* is really this city; nothing in *Unverified* or *Context* should be a name of the city itself.

## Hyderabad, India  (status DRAFT)

- **Canonical name:** Hyderabad (catalog); GeoNames 1269843, Wikidata Q1361
- **Region:** Telangana (state, IN-TS), verified = True; sources: geonames 1254788 `admin1CodesASCII`, wikidata Q677037 `P131/P1566`, iso3166 IN-TS `P300`
  - region names used: Telangana, తెలంగాణ, تلنگانہ, तेलंगाना
- **Country:** India (ISO2 IN, ISO3 IND, ccTLD .in); other names: भारत, ভারত; Wikidata Q668
- **Names used for matching** (besides the catalog name): హైదరాబాద్ (te: wikidata Q1361 `P1448`); హైదరాబాదు (te: wikidata Q1361 `P1705`); हैदराबाद (hi: wikidata Q1361 `label:hi`, geonames 1269843 `alternatenames`); হায়দরাবাদ (bn: wikidata Q1361 `label:bn`, geonames 1269843 `alternatenames`)
- **Unverified (never used for matching):** City of Pearls, Haidarābād, Hyd, मुत्याल नगर, Bhaganagar, Hyderabad AP, حیدر آباد‎, حیدرآباد
- **Context names (metro/urban area, at most PROBABLE, never the city):** none
- **Homonyms:** Hyderabad (Sindh, Pakistan, pop 1921275; geonames 1176734)
- **Notes:** curation: 'حیدر آباد\u200e' (ur) -> unverified: only Wikidata official/native name, a language of a large homonym's country | curation: 'حیدرآباد' (ur) -> unverified: only Wikidata official/native name, a language of a large homonym's country
- **Check before approving:** region and country as above; every name in *Names used* is really this city; nothing in *Unverified* or *Context* should be a name of the city itself.

## Nairobi, Kenya  (status DRAFT)

- **Canonical name:** Nairobi (catalog); GeoNames 184745, Wikidata Q3870
- **Region:** Nairobi City County (county, KE-30), verified = True; sources: geonames 184742 `admin1CodesASCII`, wikidata Q3335223 `P131/P1566`, iso3166 KE-30 `P300`
  - region names used: Nairobi City County, Nairobi, Nairobi County, Kaunti ya Nairobi
- **Country:** Kenya (ISO2 KE, ISO3 KEN, ccTLD .ke); other names: none; Wikidata Q114
- **Names used for matching** (besides the catalog name): none
- **Unverified (never used for matching):** Kenya's capital, Kenyan capital, Kenya capital city, Kenya capital, Mji mkuu wa Kenya, Nairoba
- **Context names (metro/urban area, at most PROBABLE, never the city):** none
- **Homonyms:** none
- **Check before approving:** region and country as above; every name in *Names used* is really this city; nothing in *Unverified* or *Context* should be a name of the city itself.

## Luanda, Angola  (status DRAFT)

- **Canonical name:** Luanda (catalog); GeoNames 2240449, Wikidata Q3897
- **Region:** Luanda Province (province, AO-LUA), verified = True; sources: geonames 2240444 `admin1CodesASCII`, wikidata Q190066 `P131/P1566`, iso3166 AO-LUA `P300`
  - region names used: Luanda Province, Província de Luanda, Luanda
- **Country:** Angola (ISO2 AO, ISO3 AGO, ccTLD .ao); other names: none; Wikidata Q916
- **Names used for matching** (besides the catalog name): Loanda (en: wikidata Q3897 `alias:en`, geonames 2240449 `alternatenames`); São Paulo de Loanda (en: wikidata Q3897 `alias:en`, geonames 2240449 `alternatenames`); São Paulo de Luanda (pt: wikidata Q3897 `alias:pt`, geonames 2240449 `alternatenames`)
- **Unverified (never used for matching):** São Paulo da Assunção de Loanda, St Paul of Loanda, St Paul of Luanda, St. Paul of Loanda, St. Paul of Luanda, Saint Paul of Loanda, Saint Paul of Luanda, Cidade de Luanda, São Paolo de Loanda, São Paulo da Assunção de Luanda, St Paul de Loanda, Saint Paul de Loanda
- **Context names (metro/urban area, at most PROBABLE, never the city):** none
- **Homonyms:** Luanda (Bissau, Guinea-Bissau, pop 25236; geonames 13645744); Loanda (Paraná, Brazil, pop 23242; geonames 3458479); Luanda (Vihiga County, Kenya, pop 13319; geonames 188715); Luanda (Busia County, Kenya, pop 10053; geonames 188714)
- **Check before approving:** region and country as above; every name in *Names used* is really this city; nothing in *Unverified* or *Context* should be a name of the city itself.

## Toronto, Canada  (status DRAFT)

- **Canonical name:** Toronto (catalog); GeoNames 6167865, Wikidata Q172
- **Region:** Ontario (province, CA-ON), verified = True; sources: geonames 6093943 `admin1CodesASCII`, wikidata Q1904 `P131/P1566`, iso3166 CA-ON `P300`
  - region names used: Ontario, ᐋᓐᑎᐅᕆᔪ
- **Country:** Canada (ISO2 CA, ISO3 CAN, ccTLD .ca); other names: ᑲᓇᑕ; Wikidata Q16
- **Names used for matching** (besides the catalog name): City of Toronto (en: wikidata Q172 `alias:en`, wikidata Q172 `P1448`)
- **Unverified (never used for matching):** The Six, T-O, The 416, Hogtown, ᑐᕌᓐᑐ
- **Context names (metro/urban area, at most PROBABLE, never the city):** Greater Toronto Area, GTA, Greater Toronto, Toronto metropolitan area, Toronto metro area, Toronto area, Grand Toronto, Région métropolitaine de Toronto
- **Homonyms:** Toronto (New South Wales, Australia, pop 5670; geonames 10103951); Toronto (Ohio, United States, pop 4882; geonames 5174095)
- **Check before approving:** region and country as above; every name in *Names used* is really this city; nothing in *Unverified* or *Context* should be a name of the city itself.

## What changed in this phase

- Hyderabad: the two Urdu forms (Wikidata P1448/P1705 only) moved to unverified: Urdu is a language of Pakistan, which has a large homonym (Hyderabad, Sindh, 1.9 M). Telugu, Hindi and Bengali forms stay (no large homonym's country speaks them / two sources).
- Santiago: Greater Santiago and Gran Santiago moved from unverified names to context names (METRO_AREA).
- No other entry of the 56 changed. Rules are generic (builder `curate`, tests `tests/test_identity_curation.py`).

## Approval addendum (2026-11-03): official websites and domain checks

Each official website comes from Wikidata P856 of the city item. All resolved in DNS on
2026-11-03. "Controlled" means a government-only suffix (.gov, gv.at, go.ke, gov.in); "open"
means only P856 vouches for it.

| City | Region (admin1, both sources) | Official website(s) | Suffix | Flag |
|---|---|---|---|---|
| Houston | Texas (US-TX) | houstontx.gov | controlled | — |
| Toronto | Ontario (CA-ON) | toronto.ca | open (P856) | — |
| Vienna | Vienna (AT-9) | wien.gv.at; wien.at | controlled; open (P856) | — |
| Nairobi | Nairobi City County (KE-30) | nairobi.go.ke | controlled | — |
| Hyderabad | Telangana (IN-TS) | ghmc.gov.in | controlled | Greater Hyderabad Municipal Corporation: the city's government, but the host doesn't spell the city |
| Santiago | Santiago Metropolitan Region (CL-RM) | gobiernosantiago.cl | open (P856) | **Check:** appears to be a provincial/regional government site, not the Municipality of Santiago's. If so, remove it (affiliation would then have no official host for Santiago) or replace it with a person-sourced entry. |
| Luanda | Luanda Province (AO-LUA) | none on Wikidata | — | **Check:** no official host, so municipal affiliation can't verify for Luanda until one is added with provenance |

**What approval activates:** research uses only APPROVED entries. For an approved city, normal
runs get:
- City + Region + Country matching and homonym handling;
- region inference, document/passage scope and feature location;
- official hosts for affiliation;
- geo_basis APPROVED.

Cache keys change with the identity signature, so stored bundles and responses are not reused
across that change.

**To approve:** set `status: APPROVED` on the entry and fill `approval` at the top of
`config/city_identity.yaml`. Entries with an open flag can be approved after the website line is
edited, or stay DRAFT.
