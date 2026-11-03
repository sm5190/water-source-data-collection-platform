"""Draft config/city_identity.yaml from independent geographic sources (run by a person, never
during research; a person reviews the draft and marks entries APPROVED).

    uv run python -I scripts/build_city_identity.py --cache-dir <new empty dir> \
        [--out config/city_identity.yaml] [--cities Santiago,Houston] [--offline]

Inputs:
- config/cities.yaml: only each entry's city and country (which cities to describe). Its other
  columns come from the reference workbook and are never read here.
- GeoNames public dumps (CC BY 4.0, https://download.geonames.org/export/dump/): countryInfo.txt
  (ISO codes, ccTLD, languages), admin1CodesASCII.txt (first-level regions), cities500.zip
  (populated places >= 500 people: the city entity and its homonyms), alternatenames/<CC>.zip
  (alternative names with language and preferred/short/colloquial/historic flags).
- Wikidata (CC0): the item linked to the GeoNames id (P1566), its labels, aliases, official
  (P1448) and native (P1705) names, its P131 chain (the first-level region) and its P361
  "part of" metropolitan or urban areas.

Policy (enforced by services/city_identity.py):
- A name is used for matching only if both GeoNames and Wikidata list it (same folded form), or
  Wikidata states it as the official / native name. Everything else goes to unverified_names.
- The region is the GeoNames admin1 unit; it is `verified` when a Wikidata P131 ancestor carries
  the same GeoNames id.
- Metropolitan / urban areas are context names, never city names.
- Homonyms: other places in cities500 (>= 1,000 people) named like the city, or "<city> de ...".
- Nothing is invented: when a source has nothing, the list stays empty, and an unclear entity
  match is written as AMBIGUOUS for a person to settle.

Downloads are untrusted data: they are stored under --cache-dir only, parsed as text, never
executed.
"""

import argparse
import csv
import hashlib
import io
import json
import re
import sys
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path
import httpx
import yaml
from water_source_research.core.paths import CONFIG_DIR
from water_source_research.services.city_identity import (
    CityIdentity, ContextName, CountryInfo, Homonym, IdentityName, NameSource, Region, fold,
)

GEONAMES = "https://download.geonames.org/export/dump/"
WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"
WIKIDATA_ENTITY = "https://www.wikidata.org/wiki/Special:EntityData/{}.json"
USER_AGENT = ("WaterSourceResearchIdentityDraft/0.1 "
              "(https://github.com/sm5190/water-source-data-collection-platform) httpx")
# Catalog country spellings that differ from GeoNames countryInfo names.
COUNTRY_ISO = {"united states of america": "US", "viet nam": "VN", "czech republic": "CZ",
               "south korea": "KR", "turkey": "TR", "ivory coast": "CI", "netherlands": "NL"}
PSEUDO_LANGS = {"link", "post", "iata", "icao", "faac", "fr_1793", "abbr", "wkdt", "unlc", "tcid"}
FEATURE_RANK = {"PPLC": 0, "PPLA": 1, "PPLA2": 2, "PPLA3": 3, "PPLA4": 4, "PPL": 5}
CONTEXT_CLASS = re.compile(r"metropolitan|urban area|agglomeration|conurbation|statistical area",
                           re.IGNORECASE)
QUALIFIED = re.compile(r"^(?P<head>.+?)\s+(de|del|de la|de los|de las|do|da|dos|das)\s+\S")
HOMONYM_MIN_POPULATION = 1000
# A qualified alternate name ("Santiago de Cali" for Cali) is checked only for large places.
QUALIFIED_ALTERNATE_MIN_POPULATION = 100_000
MAX_HOMONYMS = 40

class Fetcher:
    def __init__(self, cache: Path, offline: bool):
        self.cache, self.offline = cache, offline
        self.client = httpx.Client(timeout=60, headers={"User-Agent": USER_AGENT},
                                   follow_redirects=True)
        self.retrieved: dict[str, str] = {}

    def file(self, name: str, url: str) -> Path:
        path = self.cache / name
        if not path.exists():
            if self.offline:
                raise SystemExit(f"offline and {path} is missing")
            path.parent.mkdir(parents=True, exist_ok=True)
            with self.client.stream("GET", url) as r:
                r.raise_for_status()
                with path.open("wb") as f:
                    for block in r.iter_bytes():
                        f.write(block)
            self.retrieved[url] = datetime.now(UTC).isoformat(timespec="seconds")
        else:
            self.retrieved.setdefault(url, datetime.fromtimestamp(
                path.stat().st_mtime, UTC).isoformat(timespec="seconds"))
        return path

    def json(self, name: str, url: str, params: dict | None = None) -> dict:
        path = self.cache / "wikidata" / f"{name}.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        if self.offline:
            raise SystemExit(f"offline and {path} is missing")
        for attempt in range(4):
            r = self.client.get(url, params=params,
                                headers={"Accept": "application/sparql-results+json"})
            if r.status_code in (429, 503):
                time.sleep(5 * (attempt + 1))
                continue
            r.raise_for_status()
            break
        data = r.json()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        time.sleep(0.2)
        return data

    def entity(self, qid: str) -> dict:
        data = self.json(qid, WIKIDATA_ENTITY.format(qid))
        return data["entities"].get(qid) or next(iter(data["entities"].values()))

# --- GeoNames ------------------------------------------------------------------------------

def read_countries(path: Path) -> dict[str, dict]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        c = line.split("\t")
        out[c[0]] = {"iso2": c[0], "iso3": c[1], "name": c[4], "tld": c[9].lstrip("."),
                     "languages": [x.split("-")[0] for x in c[15].split(",") if x],
                     "geonameid": c[16]}
    return out

def read_admin1(path: Path) -> dict[str, dict]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        code, name, ascii_name, gid = line.split("\t")
        out[code] = {"name": name, "ascii": ascii_name, "geonameid": gid}
    return out

def read_cities(path: Path) -> list[dict]:
    rows = []
    with zipfile.ZipFile(path) as z, z.open("cities500.txt") as f:
        for line in io.TextIOWrapper(f, encoding="utf-8"):
            c = line.rstrip("\n").split("\t")
            rows.append({"geonameid": c[0], "name": c[1], "ascii": c[2],
                         "alternates": [a for a in c[3].split(",") if a],
                         "feature": c[7], "country": c[8], "admin1": c[10],
                         "population": int(c[14] or 0)})
    return rows

def read_alternates(path: Path, iso2: str, wanted: set[str]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    with zipfile.ZipFile(path) as z, z.open(f"{iso2}.txt") as f:
        reader = csv.reader(io.TextIOWrapper(f, encoding="utf-8"), delimiter="\t",
                            quoting=csv.QUOTE_NONE)
        for c in reader:
            if c[1] in wanted:
                out.setdefault(c[1], []).append({
                    "id": c[0], "lang": c[2], "name": c[3], "preferred": c[4] == "1",
                    "short": c[5] == "1", "colloquial": c[6] == "1", "historic": c[7] == "1"})
    return out

# --- Wikidata ------------------------------------------------------------------------------

def wikidata_items(fetch: Fetcher, geonameids: list[str]) -> dict[str, list[str]]:
    values = " ".join(f'"{g}"' for g in geonameids)
    q = f"SELECT ?item ?gn WHERE {{ VALUES ?gn {{ {values} }} ?item wdt:P1566 ?gn . }}"
    data = fetch.json("p1566_" + hashlib.sha1(values.encode()).hexdigest()[:12], WIKIDATA_SPARQL,
                      {"query": q, "format": "json"})
    out: dict[str, list[str]] = {}
    for b in data["results"]["bindings"]:
        out.setdefault(b["gn"]["value"], []).append(b["item"]["value"].rsplit("/", 1)[-1])
    return out

def best_item(fetch: Fetcher, qids: list[str]) -> str | None:
    """Of the Wikidata items carrying one GeoNames id, the most linked (most sitelinks)."""
    if not qids:
        return None
    return max(sorted(qids), key=lambda q: len(fetch.entity(q).get("sitelinks", {})))

WEAK_ITEM_SITELINKS = 5

def items_by_name(fetch: Fetcher, name: str, country_gid: str, langs: list[str]) -> list[str]:
    """Items labelled exactly ``name`` (English or a local language) in the country (P17)
    whose GeoNames id is the country's, most sitelinks first."""
    safe = name.replace("\\", "").replace('"', "")
    labels = " UNION ".join(f'{{ ?item rdfs:label "{safe}"@{lang} }}' for lang in langs)
    q = ("SELECT ?item (COUNT(?sl) AS ?n) WHERE { " + labels + " ?c wdt:P1566 \""
         + country_gid + "\" . ?item wdt:P17 ?c . ?sl schema:about ?item } "
         "GROUP BY ?item ORDER BY DESC(?n) LIMIT 3")
    key = "byname_" + hashlib.sha1(f"{safe}|{country_gid}|{langs}".encode()).hexdigest()[:12]
    data = fetch.json(key, WIKIDATA_SPARQL, {"query": q, "format": "json"})
    return [b["item"]["value"].rsplit("/", 1)[-1] for b in data["results"]["bindings"]]

def reverse_parts(fetch: Fetcher, qid: str) -> list[str]:
    """Items that list ``qid`` as a part (P527 has part / P150 contains)."""
    q = f"SELECT ?m WHERE {{ ?m wdt:P527|wdt:P150 wd:{qid} . }}"
    data = fetch.json(f"parts_{qid}", WIKIDATA_SPARQL, {"query": q, "format": "json"})
    return [b["m"]["value"].rsplit("/", 1)[-1] for b in data["results"]["bindings"]]

def claims(entity: dict, prop: str) -> list:
    out = []
    for c in entity.get("claims", {}).get(prop, []):
        if c.get("rank") == "deprecated" or "P582" in (c.get("qualifiers") or {}):
            continue
        v = (c.get("mainsnak") or {}).get("datavalue", {}).get("value")
        if v is not None:
            out.append(v)
    return out

def ended_claims(entity: dict, prop: str) -> list:
    return [(c.get("mainsnak") or {}).get("datavalue", {}).get("value")
            for c in entity.get("claims", {}).get(prop, [])
            if "P582" in (c.get("qualifiers") or {})]

def wd_names(entity: dict, langs: list[str]) -> list[tuple[str, str, str]]:
    """(name, lang, property) from labels and aliases in ``langs``."""
    out = []
    for lang in langs:
        if lang in entity.get("labels", {}):
            out.append((entity["labels"][lang]["value"], lang, f"label:{lang}"))
        for a in entity.get("aliases", {}).get(lang, []):
            out.append((a["value"], lang, f"alias:{lang}"))
    return out

def label(entity: dict, langs: list[str]) -> str | None:
    for lang in ["en", *langs]:
        if lang in entity.get("labels", {}):
            return entity["labels"][lang]["value"]
    return None

# --- assembling one city -------------------------------------------------------------------

def usable_name(name: str) -> bool:
    return (1 < len(name) <= 60 and not any(ch in name for ch in ",:/")
            and not name.isdigit())

def confirmed_names(qid: str, gid: str, entity: dict, alternates: list[dict], langs: list[str],
                    exclude: set[str]) -> tuple[list[IdentityName], list[IdentityName]]:
    """Names listed by both GeoNames and Wikidata (or official/native in Wikidata) -> names;
    the rest -> unverified."""
    gn: dict[str, dict] = {}
    for a in alternates:
        if a["lang"] in PSEUDO_LANGS or a["colloquial"] or not usable_name(a["name"]):
            continue
        if a["lang"] and a["lang"].split("-")[0] not in langs:
            continue
        gn.setdefault(fold(a["name"]), a)
    wd: dict[str, tuple[str, str, str]] = {}
    for name, lang, prop in wd_names(entity, langs):
        if usable_name(name):
            wd.setdefault(fold(name), (name, lang, prop))
    official: dict[str, tuple[str, str | None, str]] = {}
    for prop in ("P1448", "P1705"):
        for v in claims(entity, prop):
            if isinstance(v, dict) and usable_name(v.get("text", "")):
                official.setdefault(fold(v["text"]), (v["text"], v.get("language"), prop))
    historic_official = {fold(v["text"]) for v in ended_claims(entity, "P1448")
                         if isinstance(v, dict) and v.get("text")}
    names, unverified = [], []
    for key in dict.fromkeys([*official, *wd, *gn]):
        if key in exclude:
            continue
        g, w, o = gn.get(key), wd.get(key), official.get(key)
        sources: list[NameSource] = []
        text, lang = (o or w or (g and (g["name"], g["lang"])))[:2]
        if w:
            sources.append(NameSource(source="wikidata", id=qid, property=w[2]))
        if o:
            sources.append(NameSource(source="wikidata", id=qid, property=o[2]))
        if g:
            flags = ",".join(f for f in ("preferred", "short", "historic") if g[f])
            sources.append(NameSource(source="geonames", id=gid, property="alternatenames",
                                      detail=f"alternateNameId {g['id']}"
                                             + (f" ({flags})" if flags else "")))
        kind = "ALTERNATE"
        if (g and g["historic"]) or key in historic_official:
            kind = "HISTORICAL_SPELLING"
        elif o:
            kind = "OFFICIAL_LOCAL"
        elif text.isupper() and len(text) <= 6:
            kind = "ABBREVIATION"
        n = IdentityName(name=text, kind=kind, lang=lang or None, sources=sources)
        (names if n.independently_confirmed else unverified).append(n)
    return names, unverified

def region_for(fetch: Fetcher, city_entity: dict, city_qid: str, admin1: dict | None,
               alternates: dict[str, list[dict]], langs: list[str],
               first_level_terms: list[str]) -> tuple[Region | None, list[str]]:
    notes: list[str] = []
    if admin1 is None:
        return None, ["no GeoNames admin1 region for the city"]
    gid = admin1["geonameid"]
    sources = [NameSource(source="geonames", id=gid, property="admin1CodesASCII",
                          detail=admin1["name"])]
    match_qid, match_entity = None, None
    frontier, seen = [(city_qid, city_entity)], set()
    for _ in range(8):  # walk P131 upwards; the city itself counts (city-states)
        nxt = []
        for qid, ent in frontier:
            if qid in seen:
                continue
            seen.add(qid)
            if gid in [str(v) for v in claims(ent, "P1566")]:
                match_qid, match_entity = qid, ent
                break
            for v in claims(ent, "P131"):
                if isinstance(v, dict) and v.get("id"):
                    nxt.append((v["id"], fetch.entity(v["id"])))
        if match_qid or not nxt:
            break
        frontier = nxt
    region = Region(name=admin1["name"], sources=sources)
    city_label = label(city_entity, langs) or ""
    if match_entity is None and claims(city_entity, "P300") and fold(admin1["name"]) in {
            fold(n) for n, _, _ in wd_names(city_entity, langs)} | {fold(city_label)}:
        match_qid, match_entity = city_qid, city_entity  # the city is itself first-level
        notes.append("the city is itself a first-level unit (Wikidata P300 on the city item)")
    if match_entity is None:
        notes.append("region not verified: no Wikidata P131 ancestor carries the GeoNames "
                     f"admin1 id {gid}")
        return region, notes
    region.verified = True
    region.sources.append(NameSource(source="wikidata", id=match_qid, property="P131/P1566"))
    region.name = label(match_entity, langs) or admin1["name"]
    codes = claims(match_entity, "P300")
    region.iso_3166_2 = codes[0] if codes else None
    if codes:
        region.sources.append(NameSource(source="iso3166", id=codes[0], property="P300"))
    for v in claims(match_entity, "P31"):
        if isinstance(v, dict) and v.get("id"):
            cls = (label(fetch.entity(v["id"]), []) or "").casefold()
            term = next((t for t in sorted(first_level_terms, key=len, reverse=True)
                         if re.search(rf"(?<!\w){re.escape(t.casefold())}(?!\w)", cls)), None)
            if term:
                region.level_term = term
                break
    names, _ = confirmed_names(match_qid, gid, match_entity, alternates.get(gid, [])
                               + [{"id": "admin1", "lang": "", "name": admin1["name"],
                                   "preferred": False, "short": False, "colloquial": False,
                                   "historic": False},
                                  {"id": "admin1-ascii", "lang": "", "name": admin1["ascii"],
                                   "preferred": False, "short": False, "colloquial": False,
                                   "historic": False}], langs, {fold(region.name)})
    region.names = names
    return region, notes

def context_names(fetch: Fetcher, entity: dict, city_qid: str,
                  langs: list[str]) -> list[ContextName]:
    out = []
    parents = [v["id"] for v in claims(entity, "P361") if isinstance(v, dict) and v.get("id")]
    for qid in dict.fromkeys(parents + reverse_parts(fetch, city_qid)):
        part = fetch.entity(qid)
        classes = [label(fetch.entity(c["id"]), []) or "" for c in claims(part, "P31")
                   if isinstance(c, dict) and c.get("id")]
        hit = next((c for c in classes if CONTEXT_CLASS.search(c)), None)
        if not hit:
            continue
        metro = re.search(r"metropolitan|statistical", hit, re.IGNORECASE)
        kind = "METRO_AREA" if metro else "URBAN_AREA"
        seen = set()
        for name, lang, prop in wd_names(part, langs):
            if usable_name(name) and fold(name) not in seen:
                seen.add(fold(name))
                out.append(ContextName(name=name, kind=kind, lang=lang, sources=[NameSource(
                    source="wikidata", id=qid, property=prop, detail=f"part of (P361/P527/P150); {hit}")]))
    return out

def homonyms(cities: list[dict], chosen: dict, target_names: set[str], countries: dict,
             admin1: dict) -> list[Homonym]:
    city_key = fold(chosen["name"])
    out = []
    for r in cities:
        if r["geonameid"] == chosen["geonameid"] or r["population"] < HOMONYM_MIN_POPULATION:
            continue
        if r["country"] == chosen["country"] and r["admin1"] == chosen["admin1"]:
            continue  # parts of the same region (sectors, districts): not other cities
        forms = {fold(r["name"]), fold(r["ascii"])}
        exact = bool(forms & target_names)
        qualified = any((m := QUALIFIED.match(f)) and m.group("head") == city_key for f in forms)
        alias = None
        if not (exact or qualified) and r["population"] >= QUALIFIED_ALTERNATE_MIN_POPULATION:
            alias = next((a for a in r["alternates"] if (m := QUALIFIED.match(fold(a)))
                          and m.group("head") == city_key), None)
        if not (exact or qualified or alias):
            continue
        country = countries.get(r["country"], {}).get("name", r["country"])
        region = admin1.get(f"{r['country']}.{r['admin1']}", {}).get("name")
        detail = ("qualified form" if qualified and not exact
                  else f"alternate name {alias!r}" if alias else None)
        out.append(Homonym(name=alias or r["name"], country=country, iso2=r["country"],
                           region=region, population=r["population"], sources=[NameSource(
                               source="geonames", id=r["geonameid"], property="cities500",
                               detail=detail)]))
    out.sort(key=lambda h: -(h.population or 0))
    return out[:MAX_HOMONYMS]

def choose_entity(cities: list[dict], iso2: str, names: list[str]) -> tuple[dict | None, list]:
    keys = {fold(n) for n in names} | {fold(n).replace(" ", "") for n in names}
    cands = []
    for r in cities:
        if r["country"] != iso2:
            continue
        forms = {fold(r["name"]), fold(r["ascii"])}
        primary = bool(forms & keys or {f.replace(" ", "") for f in forms} & keys)
        if primary or any(fold(a) in keys for a in r["alternates"]):
            cands.append((0 if primary else 1, FEATURE_RANK.get(r["feature"], 9),
                          -r["population"], r))
    cands.sort(key=lambda c: c[:3])
    return (cands[0][3] if cands else None), [c[3] for c in cands]

def first_level_terms(country: str, iso_name: str) -> list[str]:
    data = yaml.safe_load((CONFIG_DIR / "admin_levels.yaml").read_text(encoding="utf-8"))
    for name, entry in (data.get("countries") or {}).items():
        names = {fold(name), *(fold(a) for a in entry.get("aliases", []))}
        if fold(country) in names or fold(iso_name) in names:
            return list(entry.get("first_level") or [])
    return []

def build_one(entry: dict, fetch: Fetcher, cities: list[dict], countries: dict, admin1: dict,
              iso_by_name: dict, qids: dict[str, str], alt_cache: dict) -> CityIdentity:
    city, country = entry["city"], entry["country"]
    ident = CityIdentity(city=city, country=country, status="DRAFT")
    iso2 = COUNTRY_ISO.get(fold(country)) or iso_by_name.get(fold(country))
    if iso2 is None:
        ident.status = "NOT_FOUND"
        ident.notes.append(f"country {country!r} not found in GeoNames countryInfo")
        return ident
    c = countries[iso2]
    langs = list(dict.fromkeys(["en", *c["languages"][:3]]))
    ident.country_info = CountryInfo(
        name=country, iso2=iso2, iso3=c["iso3"], cctld=c["tld"] or None,
        names=[n for n in dict.fromkeys([c["name"]]) if fold(n) != fold(country)],
        languages=[x for x in c["languages"] if len(x) == 2][:3])
    chosen, cands = choose_entity(cities, iso2, ident.catalog_names())
    if chosen is None:
        ident.status = "NOT_FOUND"
        ident.notes.append("no GeoNames populated place with this name in the country")
        return ident
    rivals = [r for r in cands[1:] if r["population"] >= 0.5 * max(chosen["population"], 1)
              and r["admin1"] != chosen["admin1"]
              and fold(r["name"]) == fold(chosen["name"])]
    if rivals:
        ident.status = "AMBIGUOUS"
        ident.notes.append("several places of similar size share the name: "
                           + "; ".join(f"{r['name']} (geonames {r['geonameid']}, admin1 "
                                       f"{r['admin1']}, pop {r['population']})" for r in rivals))
    gid = chosen["geonameid"]
    ident.ids["geonames"] = gid
    alts = alt_cache[iso2]
    adm = admin1.get(f"{iso2}.{chosen['admin1']}")
    qid = best_item(fetch, qids.get(gid, []))
    if len(qids.get(gid, [])) > 1:
        ident.notes.append(f"GeoNames id {gid} is on several Wikidata items "
                           f"{sorted(qids[gid])}; used {qid} (most sitelinks)")
    if qid is None or len(fetch.entity(qid).get("sitelinks", {})) < WEAK_ITEM_SITELINKS:
        for alt in items_by_name(fetch, chosen["name"], c["geonameid"], langs):
            if alt == qid:
                continue
            probe, _ = region_for(fetch, fetch.entity(alt), alt, adm, alts, langs, [])
            if probe is not None and probe.verified:  # same first-level region as GeoNames
                ident.notes.append(f"Wikidata item {alt} matched by name and country (its "
                                   f"region chain reaches GeoNames admin1 {adm['geonameid']}); "
                                   f"the item carrying the GeoNames id ({qid}) is a stub")
                qid = alt
                break
    if qid is None:
        ident.notes.append("no Wikidata item carries this GeoNames id: names stay unverified")
        ident.region = Region(name=adm["name"], sources=[NameSource(
            source="geonames", id=adm["geonameid"], property="admin1CodesASCII")]) if adm else None
        ident.unverified_names = [IdentityName(name=a["name"], lang=a["lang"] or None, sources=[
            NameSource(source="geonames", id=gid, property="alternatenames")])
            for a in alts.get(gid, []) if usable_name(a["name"])
            and a["lang"] not in PSEUDO_LANGS][:30]
        return ident
    ident.ids["wikidata"] = qid
    entity = fetch.entity(qid)
    for v in claims(entity, "P17")[:1]:  # the country's labels in English and local languages
        if isinstance(v, dict) and v.get("id"):
            country_entity = fetch.entity(v["id"])
            extra = [lab["value"] for lang, lab in country_entity.get("labels", {}).items()
                     if lang in langs and usable_name(lab["value"])]
            known = {fold(country), *(fold(n) for n in ident.country_info.names)}
            ident.country_info.names += [n for n in dict.fromkeys(extra) if fold(n) not in known]
            ident.ids["wikidata_country"] = v["id"]
    region, notes = region_for(fetch, entity, qid, adm, alts, langs,
                               first_level_terms(country, c["name"]))
    ident.region, ident.notes = region, ident.notes + notes
    exclude = {fold(n) for n in ident.catalog_names()}
    names, unverified = confirmed_names(qid, gid, entity, alts.get(gid, []), langs, exclude)
    region_keys = {fold(n) for n in (region.all_names() if region else [])}
    country_keys = {fold(country), fold(c["name"])}
    for n in names:
        if fold(n.name) in region_keys:
            n.ambiguous_with.append("region")
        if fold(n.name) in country_keys:
            n.ambiguous_with.append("country")
    ident.names, ident.unverified_names = names, unverified
    ident.context_names = context_names(fetch, entity, qid, langs)
    targets = {fold(n) for n in ident.unambiguous_city_names()}
    ident.homonyms = homonyms(cities, chosen, targets, countries, admin1)
    curate(ident, countries)
    return ident

# --- curation rules (also applied to an existing file with --recurate) ------------------------

LARGE_HOMONYM = 100_000  # services/geo_match.py LARGE_HOMONYM
OFFICIAL_ONLY = {"P1448", "P1705"}
_METRO_BEFORE = r"(?:greater|gran|grand|grande|gro(?:ss|ß)(?:raum)?|metropolitan|metro|" \
                r"area metropolitana|zona metropolitana|region metropolitana|" \
                r"regiao metropolitana|region metropolitaine)"
_METRO_AFTER = r"(?:metropolitan area|metro area|metropolitan region|area|region|msa|" \
               r"metropolitana|metropolitaine)"

def _official_only(n: IdentityName) -> bool:
    return all(s.source == "wikidata" and (s.property or "") in OFFICIAL_ONLY
               for s in n.sources)

def _metro_alias(name: str, city_names: list[str]) -> bool:
    """"Gran Santiago", "Greater Toronto", "Santiago metropolitan area": the marker together
    with the city's whole catalog name (so a city named "Grand Rapids" keeps its name)."""
    key = fold(name).replace("-", " ")
    for city in city_names:
        c = re.escape(fold(city))
        if re.fullmatch(rf"{_METRO_BEFORE}\s+(?:de\s+|of\s+)?{c}", key) or re.fullmatch(
                rf"{c}\s+{_METRO_AFTER}", key):
            return True
    return False

def curate(ident: CityIdentity, countries: dict[str, dict]) -> list[str]:
    """Generic rules applied after drafting; returns what was moved (for the notes).

    - A name supported only by Wikidata's official/native-name properties, in a language of
      the country of a large listed homonym, may name that place too: unverified.
    - An alias formed from a metropolitan marker and the city's name is a context name
      (METRO_AREA), never a name of the city."""
    moved: list[str] = []
    own = ident.country_info.iso2 if ident.country_info else None
    homonym_langs = {lang for h in ident.homonyms if (h.population or 0) >= LARGE_HOMONYM
                     and h.iso2 and h.iso2 != own
                     for lang in countries.get(h.iso2, {}).get("languages", [])}
    keep = []
    for n in ident.names:
        lang = (n.lang or "").split("-")[0]
        if _official_only(n) and lang and lang in homonym_langs:
            ident.unverified_names.append(n)
            moved.append(f"{n.name!r} ({lang}) -> unverified: only Wikidata official/native "
                         f"name, a language of a large homonym's country")
        else:
            keep.append(n)
    ident.names = keep
    known = {fold(c.name) for c in ident.context_names}
    for bucket in ("names", "unverified_names"):
        rest = []
        for n in getattr(ident, bucket):
            if _metro_alias(n.name, ident.catalog_names()):
                if fold(n.name) not in known:
                    known.add(fold(n.name))
                    ident.context_names.append(ContextName(
                        name=n.name, kind="METRO_AREA", lang=n.lang, sources=[
                            s.model_copy(update={"detail": "metropolitan alias of the city"})
                            for s in n.sources]))
                moved.append(f"{n.name!r} -> context name (metropolitan alias)")
            else:
                rest.append(n)
        setattr(ident, bucket, rest)
    ident.notes += [f"curation: {m}" for m in moved if f"curation: {m}" not in ident.notes]
    return moved

def recurate_file(path: Path, countries: dict[str, dict]) -> list[tuple[str, list[str]]]:
    """Apply ``curate`` to every entry of an existing identity file; entries that don't
    change are written back untouched. Statuses are never changed."""
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    changes = []
    for i, entry in enumerate(doc.get("cities", [])):
        if entry.get("status") not in ("DRAFT", "AMBIGUOUS", "APPROVED"):
            continue
        ident = CityIdentity.model_validate(entry)
        moved = curate(ident, countries)
        if moved:
            changes.append((f"{ident.city}, {ident.country}", moved))
            doc["cities"][i] = {"city": ident.city, "country": ident.country,
                                "status": ident.status} | ident.model_dump(
                mode="json", exclude_none=True, exclude_defaults=True)
    path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=100),
                    encoding="utf-8")
    return changes

def review_sheet(identities: list[CityIdentity]) -> str:
    """A Markdown summary of the draft for the person approving it."""
    def names(xs, limit=15):
        out = ", ".join(x for x in xs[:limit]) or "—"
        return out + (f" … +{len(xs) - limit}" if len(xs) > limit else "")
    attention = [f"{i.city}, {i.country}" for i in identities
                 if i.status != "DRAFT" or not i.region or not i.region.verified]
    how = ("Drafted from GeoNames and Wikidata by `scripts/build_city_identity.py` (no workbook "
           "input). To approve an entry: check it here, edit `config/city_identity.yaml` if "
           "needed (move a name between `unverified_names`, `names` and `context_names`, fix a "
           "region), set the entry's `status: APPROVED` and fill `approval` at the top. "
           "Research uses approved entries only; until then a city is identified by its "
           "catalog name and country alone.")
    rule = ("Names used for matching are confirmed by both sources or are Wikidata's official "
            "or native name. Unverified names are never used unless you promote one (add a "
            "`{source: person, id: <your name>}` source to it). Metropolitan areas are context "
            "only, never the city.")
    lines = ["# City identity draft — review sheet", "", how, "", rule, "",
             "**Needs attention:** " + ("; ".join(attention) or "none"), ""]
    for i in identities:
        r = i.region
        region = (f"{r.name} ({r.iso_3166_2 or 'no ISO code'}; term {r.level_term or '—'}; "
                  + ("verified" if r.verified else "**NOT verified**") + ")") if r else "**none**"
        used = [n.name + f" ({n.kind.lower()}" + (", same as " + "/".join(n.ambiguous_with)
                                                  if n.ambiguous_with else "") + ")"
                for n in i.names]
        lines += [f"## {i.city}, {i.country} — {i.status}", "", f"- Region: {region}",
                  f"- Names used: {names(used)}",
                  f"- Unverified (not used): {names([n.name for n in i.unverified_names])}",
                  "- Context (metro/urban areas): "
                  + names(list(dict.fromkeys(c.name for c in i.context_names))),
                  f"- Homonyms ({len(i.homonyms)}): "
                  + names([f"{h.name} ({h.region or '?'}, {h.iso2})" for h in i.homonyms], 8),
                  *(f"- Note: {n}" for n in i.notes), ""]
    return "\n".join(lines)

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache-dir", required=True, type=Path,
                    help="directory for the downloaded files (keep it apart from code)")
    ap.add_argument("--catalog", type=Path, default=CONFIG_DIR / "cities.yaml")
    ap.add_argument("--out", type=Path, default=CONFIG_DIR / "city_identity.yaml")
    ap.add_argument("--cities", help="comma-separated subset (catalog city names)")
    ap.add_argument("--offline", action="store_true", help="use cached downloads only")
    ap.add_argument("--review", type=Path, help="also write a Markdown review sheet here")
    ap.add_argument("--recurate", action="store_true",
                    help="apply the curation rules to the existing --out file (needs only "
                         "countryInfo.txt); statuses and unchanged entries are kept")
    args = ap.parse_args()
    fetch = Fetcher(args.cache_dir.resolve(), args.offline)
    if args.recurate:
        sys.stdout.reconfigure(encoding="utf-8")
        countries = read_countries(fetch.file("countryInfo.txt", GEONAMES + "countryInfo.txt"))
        for city, moved in recurate_file(args.out, countries):
            print(city)
            for m in moved:
                print("   ", m)
        return 0
    catalog = yaml.safe_load(args.catalog.read_text(encoding="utf-8"))["cities"]
    entries = [{"city": e["city"], "country": e["country"]} for e in catalog]  # identity only
    if args.cities:
        wanted = {fold(c) for c in args.cities.split(",")}
        entries = [e for e in entries if fold(e["city"]) in wanted]
    countries = read_countries(fetch.file("countryInfo.txt", GEONAMES + "countryInfo.txt"))
    admin1 = read_admin1(fetch.file("admin1CodesASCII.txt", GEONAMES + "admin1CodesASCII.txt"))
    cities = read_cities(fetch.file("cities500.zip", GEONAMES + "cities500.zip"))
    iso_by_name = {fold(c["name"]): iso for iso, c in countries.items()}
    needed = sorted({COUNTRY_ISO.get(fold(e["country"])) or iso_by_name.get(fold(e["country"]))
                     for e in entries} - {None})
    chosen_ids = []
    for e in entries:
        iso2 = COUNTRY_ISO.get(fold(e["country"])) or iso_by_name.get(fold(e["country"]))
        probe = CityIdentity(city=e["city"], country=e["country"])
        r, _ = choose_entity(cities, iso2, probe.catalog_names()) if iso2 else (None, [])
        if r:
            chosen_ids.append(r["geonameid"])
            adm = admin1.get(f"{iso2}.{r['admin1']}")
            if adm:
                chosen_ids.append(adm["geonameid"])
    alt_cache = {}
    for iso2 in needed:
        path = fetch.file(f"alternatenames/{iso2}.zip", f"{GEONAMES}alternatenames/{iso2}.zip")
        alt_cache[iso2] = read_alternates(path, iso2, set(chosen_ids))
    qids = wikidata_items(fetch, sorted(set(chosen_ids)))
    out = []
    for e in entries:
        ident = build_one(e, fetch, cities, countries, admin1, iso_by_name, qids, alt_cache)
        out.append(ident)
        print(f"{ident.status:10} {e['city']}, {e['country']}: region "
              f"{ident.region.name if ident.region else '—'}"
              f"{' (verified)' if ident.region and ident.region.verified else ''}; "
              f"names {len(ident.names)}, unverified {len(ident.unverified_names)}, "
              f"context {len(ident.context_names)}, homonyms {len(ident.homonyms)}")
    doc = {
        "version": 1,
        "status": "DRAFT",
        "note": ("Drafted by scripts/build_city_identity.py from GeoNames and Wikidata; a person "
                 "reviews each entry and sets its status to APPROVED. Identity only: never "
                 "utilities, water sources or research answers. The reference workbook is not "
                 "an input."),
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "sources": {
            "geonames": {"license": "CC BY 4.0", "files": dict(sorted(fetch.retrieved.items()))},
            "wikidata": {"license": "CC0", "endpoints": [WIKIDATA_SPARQL,
                                                          WIKIDATA_ENTITY.format("<QID>")]},
        },
        "approval": {"approved_by": None, "approved_at": None},
        "cities": [{"city": i.city, "country": i.country, "status": i.status}
                   | i.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
                   for i in out],
    }
    args.out.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=100),
                        encoding="utf-8")
    print(f"wrote {len(out)} entries to {args.out}")
    if args.review:
        args.review.write_text(review_sheet(out), encoding="utf-8")
        print(f"review sheet: {args.review}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
