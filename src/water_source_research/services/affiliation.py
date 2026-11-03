"""Municipal affiliation: is an organisation a department of the target city's government?
Deterministic, from the run's stored text and links (acquisition/links.py).

- MUNICIPAL_VERIFIED needs corroboration from a page on an official city host
  (``GeoMatcher.official_host``: the identity's official website, or a government-controlled
  suffix spelling the city), independent of the organisation's own host:
  - a sentence there stating that the organisation is a department, division, agency or
    service of the city ("Toronto Water is a division of the City of Toronto",
    "Magistratsabteilung 31 – Wiener Wasser"); or
  - a link there to the organisation's own host whose anchor or context names the
    organisation, under a departments/agencies/services heading or list.
- MUNICIPAL_CANDIDATE (priority only, never authority), from any one of:
  - a self-declaration on the organisation's own host ("a department of the City of X");
  - a copyright/footer line naming the city on that host;
  - a city-department hostname (``municipal_hint``);
  - a link from an official host without a departments context.
A link from any other page is not a municipal signal at all.

A bare hyperlink, a footer, a hostname or a look-alike host never verifies. Every finding
keeps the stating/linking document, the target, the anchor/context or quote, the heading, the
observation time and the basis.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit
from water_source_research.discovery.normalize import municipal_hint
from water_source_research.schemas.research import EvidenceChunk
from water_source_research.schemas.utility import UtilityCandidate
from water_source_research.services.geo_match import GeoMatcher

_UNIT = (r"(?:department|division|bureau|agency|office|service|unit|section|utility|"
         r"departamento|direcci[oó]n|dependencia|servicio|empresa\s+municipal|"
         r"magistratsabteilung|dienststelle|abteilung|r[eé]gie|service\s+municipal|"
         r"autarquia|secretar[ií]a)")
_GOV = (r"(?:city|town|municipality|municipal\s+government|county|metropolitan\s+government|"
        r"ciudad|municipio|municipalidad|alcald[ií]a|ayuntamiento|intendencia|"
        r"gobierno\s+de\s+la\s+ciudad|stadt|gemeinde|magistrat|ville|mairie|prefeitura|"
        r"munic[ií]pio)")
# "<org> is a department of the City of X", "<org>, a division of the City", "a City of X
# department", "<org> ... Magistratsabteilung 31": the organisation's municipal status.
DEPARTMENT_OF = re.compile(
    rf"\b(?:(?:is|are|as|was|,)\s+(?:a|an|the|one\s+of\s+the)?\s*(?:\w+\s+){{0,2}}?{_UNIT}s?"
    rf"\s+(?:of|within|in|run\s+by|operated\s+by|de|del|de\s+la|der|des|du)\s+(?:the\s+|la\s+|"
    rf"el\s+|der\s+|la\s+)?{_GOV}"
    rf"|(?:city|municipal|municipality)\s+(?:of\s+\w+\s+)?{_UNIT}"
    rf"|magistratsabteilung\s+\d+|municipal\s+department\s+\d+|\bMA\s?\d{{2}}\b"
    rf"|{_UNIT}\s+municipal)", re.IGNORECASE)
DEPARTMENTS_CONTEXT = re.compile(
    r"\b(?:departments?|divisions?|agencies|bureaus?|offices|city\s+services|services|"
    r"departamentos|direcciones|dependencias|servicios\s+municipales|dienststellen|"
    r"magistratsabteilungen|services\s+municipaux|secretar[ií]as)\b", re.IGNORECASE)
COPYRIGHT = re.compile(r"(?:©|&copy;|\(c\)|copyright)\s*(?:\d{4}\s*[-–]?\s*\d{0,4}\s*)?"
                       r"(?:the\s+)?(?:city|town|municipality|ciudad|stadt|ville)\s+of\s+",
                       re.IGNORECASE)
_SENTENCE = re.compile(r"(?<=[.!?;])\s+|\n+")
FIRST_PERSON = re.compile(r"\b(?:we|our)\b", re.IGNORECASE)

def _host(url: str) -> str:
    return (urlsplit(url or "").hostname or "").casefold().removeprefix("www.")

@dataclass
class AffiliationEvidence:
    basis: str  # OFFICIAL_STATEMENT | OFFICIAL_DEPARTMENTS_LINK | SELF_DECLARATION |
    #             COPYRIGHT_FOOTER | HOSTNAME_HINT | OFFICIAL_LINK_NO_CONTEXT
    url: str  # the stating or linking document
    quote: str  # the sentence, or the link's anchor + context
    target_url: str | None = None
    heading: str = ""
    observed_at: str | None = None
    chunk_id: str | None = None
    official_reason: str | None = None  # why the stating host is the city's own

    @property
    def verifies(self) -> bool:
        return self.basis in ("OFFICIAL_STATEMENT", "OFFICIAL_DEPARTMENTS_LINK")

@dataclass
class Affiliation:
    candidate_key: str
    name: str
    status: str  # MUNICIPAL_VERIFIED | MUNICIPAL_CANDIDATE
    evidence: list[AffiliationEvidence] = field(default_factory=list)

    @property
    def verified(self) -> bool:
        return self.status == "MUNICIPAL_VERIFIED"

def _names_re(cand: UtilityCandidate) -> re.Pattern[str] | None:
    from water_source_research.services.utility_registry import _name_re
    return _name_re(cand.all_names())

def assess(matcher: GeoMatcher, candidates: Sequence[UtilityCandidate],
           chunks: Sequence[EvidenceChunk], links: Mapping[str, Sequence[Mapping[str, Any]]],
           city: str, documents: Mapping[str, Mapping[str, Any]] | None = None
           ) -> list[Affiliation]:
    """Affiliation per candidate that has any municipal signal (none: not listed)."""
    documents = documents or {}
    out = []
    for cand in candidates:
        pattern = _names_re(cand)
        if pattern is None:
            continue
        # the city government's own site is never the organisation's own host, even when
        # the registry bound it (the host spells the city)
        own = {h for h in cand.bound_hosts if not matcher.official_host(h)}
        evidence: list[AffiliationEvidence] = []
        for c in chunks:
            if not pattern.search(c.text) and not (_host(c.url) in own):
                continue
            host = _host(c.url)
            official = matcher.official_host(c.url)
            for s in _SENTENCE.split(c.text):
                if len(s) < 8 or not DEPARTMENT_OF.search(s):
                    continue
                named = bool(pattern.search(s))
                quote = " ".join(s.split())[:400]
                if official and named and host not in own:
                    evidence.append(AffiliationEvidence(
                        "OFFICIAL_STATEMENT", c.url, quote, chunk_id=c.chunk_id,
                        official_reason=official,
                        observed_at=(documents.get(c.url) or {}).get("retrieved_at")))
                elif host in own and (named or FIRST_PERSON.search(s)):
                    evidence.append(AffiliationEvidence(
                        "SELF_DECLARATION", c.url, quote, chunk_id=c.chunk_id))
            if host in own and COPYRIGHT.search(c.text):
                m = COPYRIGHT.search(c.text)
                evidence.append(AffiliationEvidence(
                    "COPYRIGHT_FOOTER", c.url,
                    " ".join(c.text[m.start():m.end() + 60].split()), chunk_id=c.chunk_id))
        for src_url, ls in links.items():
            src_host = _host(src_url)
            official = matcher.official_host(src_url)
            for link in ls:
                target = (link.get("target_host") or "").casefold().removeprefix("www.")
                if target not in own or src_host in own:
                    continue
                text = f"{link.get('anchor_text', '')} {link.get('context', '')}"
                named = bool(pattern.search(text))
                in_list = bool(DEPARTMENTS_CONTEXT.search(f"{link.get('heading', '')} "
                                                          f"{link.get('context', '')}"))
                quote = " ".join(text.split())[:400]
                if not official:
                    continue  # a link from any other page says nothing about the city
                if named and in_list and not link.get("in_footer"):
                    basis = "OFFICIAL_DEPARTMENTS_LINK"
                else:
                    basis = "OFFICIAL_LINK_NO_CONTEXT"
                evidence.append(AffiliationEvidence(
                    basis, src_url, quote, target_url=link.get("target_url"),
                    heading=link.get("heading") or "", observed_at=link.get("observed_at"),
                    official_reason=official))
        for h in own:
            if municipal_hint(h, city):
                evidence.append(AffiliationEvidence("HOSTNAME_HINT", f"https://{h}/", h))
        if not evidence:
            continue
        dedup, seen = [], set()
        for e in evidence:
            k = (e.basis, e.url, e.quote)
            if k not in seen:
                seen.add(k)
                dedup.append(e)
        status = "MUNICIPAL_VERIFIED" if any(e.verifies for e in dedup) else \
            "MUNICIPAL_CANDIDATE"
        out.append(Affiliation(cand.key, cand.name, status, dedup))
    return out

def bootstrap_queries(matcher: GeoMatcher, affiliations: Sequence[Affiliation],
                      acquired_urls: Sequence[str], already_run: set[str]) -> list[str]:
    """One check per MUNICIPAL_CANDIDATE with no page acquired from an official city host:
    ``site:<official host> "<organisation>"`` (discovery only; at most one per round)."""
    if any(matcher.official_host(u) for u in acquired_urls):
        return []
    sites = [s.host for s in matcher.identity.official_websites]
    if not sites:
        return []
    done = {q.casefold() for q in already_run}
    for a in affiliations:
        if a.status == "MUNICIPAL_CANDIDATE":
            q = f'site:{sites[0]} "{a.name}"'
            if q.casefold() not in done:
                return [q]
    return []
