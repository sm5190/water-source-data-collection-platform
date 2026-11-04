"""Approve city identities, or record a government website, in config/city_identity.yaml.

    uv run python scripts/approve_identity.py approve "Houston" "Santiago" ... \\
        --reviewer <name> --basis "<what was reviewed>"
    uv run python scripts/approve_identity.py website "Santiago" <host> --url <url> \\
        --level MUNICIPAL|REGIONAL|PROVINCIAL|NATIONAL [--unverified] --note "<why>" \\
        --source "<who/what says so>"

approve validates each entry first and refuses it on failure:
- the region has both sources (verified);
- the country has an ISO code and a ccTLD;
- every name used for matching is independently confirmed;
- every website has provenance;
- a website that is MUNICIPAL and verified is not regional/provincial.

It sets ``status: APPROVED`` with ``approval`` {approved_by, approved_at, basis}, fills the file's
top-level approval note, and leaves every other entry as it is (DRAFT stays DRAFT). A missing or
unverified municipal website never blocks approval: geographic identity and government-site
affiliation are separate.
"""

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path
import yaml
from water_source_research.core.paths import CONFIG_DIR
from water_source_research.services.city_identity import (
    CityIdentity, EntryApproval, NameSource, OfficialWebsite, fold,
)

PATH = CONFIG_DIR / "city_identity.yaml"

def problems(ident: CityIdentity) -> list[str]:
    out = []
    if ident.region is None or not ident.region.verified:
        out.append("region not verified by both sources")
    info = ident.country_info
    if info is None or not info.iso2 or not info.cctld:
        out.append("country ISO code or ccTLD missing")
    for n in ident.names:
        if not n.independently_confirmed:
            out.append(f"name {n.name!r} not independently confirmed")
    for w in ident.official_websites:
        if not w.sources:
            out.append(f"website {w.host} has no provenance")
    return out

def _find(doc: dict, city: str) -> int:
    for i, e in enumerate(doc["cities"]):
        if fold(e["city"]) == fold(city):
            return i
    raise SystemExit(f"no identity entry for {city!r}")

def _dump(doc: dict, path: Path) -> None:
    path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=100),
                    encoding="utf-8")

def _store(doc: dict, i: int, ident: CityIdentity) -> None:
    doc["cities"][i] = {"city": ident.city, "country": ident.country,
                        "status": ident.status} | ident.model_dump(
        mode="json", exclude_none=True, exclude_defaults=True)

def approve(cities: list[str], reviewer: str, basis: str, path: Path = PATH) -> int:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    now = datetime.now(UTC).isoformat(timespec="seconds")
    failed = 0
    for city in cities:
        i = _find(doc, city)
        ident = CityIdentity.model_validate(doc["cities"][i])
        found = problems(ident)
        if found:
            failed += 1
            print(f"REFUSED {city}: " + "; ".join(found))
            continue
        ident.status = "APPROVED"
        ident.approval = EntryApproval(approved_by=reviewer, approved_at=now, basis=basis)
        _store(doc, i, ident)
        print(f"APPROVED {ident.city}, {ident.country}")
    approved = [e["city"] for e in doc["cities"] if e.get("status") == "APPROVED"]
    doc["approval"] = {"approved_by": reviewer, "approved_at": now,
                       "note": f"{len(approved)} entries APPROVED ({', '.join(approved)}); the "
                               f"rest stay DRAFT. Per-entry approval metadata on each entry."}
    _dump(doc, path)
    return failed

def record_website(city: str, host: str, url: str, level: str, verified: bool, note: str,
                   source: str, path: Path = PATH) -> None:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    i = _find(doc, city)
    ident = CityIdentity.model_validate(doc["cities"][i])
    host = host.casefold().removeprefix("www.")
    site = OfficialWebsite(host=host, url=url, level=level, verified=verified, note=note,
                           sources=[NameSource(source="person", id="review",
                                               property="government website",
                                               detail=source[:300])])
    ident.official_websites = [w for w in ident.official_websites if w.host != host] + [site]
    _store(doc, i, ident)
    _dump(doc, path)
    print(f"{city}: {host} recorded as {level}{'' if verified else ' (unverified)'}")

def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("approve")
    a.add_argument("cities", nargs="+")
    a.add_argument("--reviewer", required=True)
    a.add_argument("--basis", default="")
    w = sub.add_parser("website")
    w.add_argument("city")
    w.add_argument("host")
    w.add_argument("--url", required=True)
    w.add_argument("--level", required=True,
                   choices=["MUNICIPAL", "REGIONAL", "PROVINCIAL", "NATIONAL", "UNKNOWN"])
    w.add_argument("--unverified", action="store_true")
    w.add_argument("--note", default="")
    w.add_argument("--source", required=True)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if args.cmd == "approve":
        return 1 if approve(args.cities, args.reviewer, args.basis) else 0
    record_website(args.city, args.host, args.url, args.level, not args.unverified, args.note,
                   args.source)
    return 0

if __name__ == "__main__":
    sys.exit(main())
