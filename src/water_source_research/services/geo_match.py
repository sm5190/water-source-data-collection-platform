"""Which place a text is about: the target city (City + Region + Country), a homonym, or
somewhere else. Deterministic, built on the approved city identity (services/city_identity.py).

A city name alone never establishes the place:
- a qualified form that isn't one of the city's verified names ("Santiago de Cali",
  "Santiago del Estero") or a listed homonym is another place, and is blanked;
- a bare mention counts when the region or country of the target is named nearby (CONFIRMED),
  or when the document as a whole is about the target country (PROBABLE). Next to another
  country's or region's name, or in a document about another country or region, it is a
  homonym. In a document of unknown scope it is kept unless the identity lists a large
  homonym (>= LARGE_HOMONYM people), when it is AMBIGUOUS and blanked;
- a metropolitan-area name alone is at most PROBABLE; a region name alone is not the city.

Document scope (``document_scope``) weighs the host (country-code TLD, government second level,
US state government hosts) and the country/region names in the title and text.

Without an approved identity (``matcher_for`` returns None) callers keep their earlier,
name-only behaviour, so nothing changes until a person approves the identity.

Every decision carries a reason and the text it was based on, for the item metadata, the
validation check details and the utility registry.
"""

import hashlib
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit
import yaml
from water_source_research.core.paths import CONFIG_DIR
from water_source_research.services.city_identity import CityIdentity, fold, identity_for
from water_source_research.services.places import HYDRONYM, gazetteer

GeoStatus = Literal["CONFIRMED", "PROBABLE", "HOMONYM", "OTHER_PLACE", "AMBIGUOUS", "NONE"]
Scope = Literal["TARGET", "OTHER", "UNKNOWN"]
TARGET_STATUSES = frozenset({"CONFIRMED", "PROBABLE"})
LARGE_HOMONYM = 100_000
WINDOW = 100  # characters either side of a mention that count as "nearby"
# Country-code TLDs widely used as generic ones: they say nothing about the country.
GENERIC_CCTLDS = frozenset({"co", "io", "ai", "me", "tv", "cc", "ly", "fm", "gg", "to", "ws",
                            "sh", "so", "nu", "vc", "ag", "bz", "gl", "im", "la", "ac"})
GOV_SECOND_LEVEL = frozenset({"gov", "gob", "go", "govt", "gouv", "gv", "gub", "admin", "mil",
                              "government", "govern", "regierung", "gc"})
US_STATE_CODES = frozenset({"al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi",
    "id", "il", "in", "ia", "ks", "ky", "la", "me", "md", "ma", "mi", "mn", "ms", "mo", "mt",
    "ne", "nv", "nh", "nj", "nm", "ny", "nc", "nd", "oh", "ok", "or", "pa", "ri", "sc", "sd",
    "tn", "tx", "ut", "vt", "va", "wa", "wv", "wi", "wy", "dc"})
_US_NAMES = frozenset({"united states", "united states of america", "usa", "us", "u.s."})
_QUALIFIER = r"(?:de|del|de la|de los|de las|do|da|dos|das|di|of)"
_CAP_WORD = r"[A-ZÀ-ÖØ-Þ][\w'’\-]+"
_ACCENTS = {"a": "aáàâãäåā", "e": "eéèêëē", "i": "iíìîïī", "o": "oóòôõöøō", "u": "uúùûüū",
            "n": "nñ", "c": "cç", "y": "yýÿ", "s": "sš", "z": "zž"}

def _letters(name: str) -> str:
    """Regex for ``name`` matching accents, case and spacing/hyphens loosely."""
    out = []
    for ch in fold(name):
        if ch == " ":
            out.append(r"[\s\-]+")
        elif ch in _ACCENTS:
            out.append(f"[{_ACCENTS[ch]}{_ACCENTS[ch].upper()}]")
        elif ch.isalpha():
            out.append(f"[{re.escape(ch)}{re.escape(ch.upper())}]")
        else:
            out.append(re.escape(ch))
    return "".join(out)

def _names_regex(names: Iterable[str], case_sensitive_short: bool = True) -> re.Pattern[str]:
    names = sorted({n for n in names if n and n.strip()}, key=len, reverse=True)
    parts = []
    for n in names:
        if case_sensitive_short and len(n) <= 3:
            parts.append(re.escape(n))  # "US", "UK": exact case, never the word "us"
        else:
            parts.append(_letters(n))
    return re.compile("|".join(rf"(?<!\w)(?:{p})(?!\w)" for p in parts) or r"(?!x)x")

@lru_cache
def _country_aliases() -> dict[str, list[str]]:
    data = yaml.safe_load((CONFIG_DIR / "admin_levels.yaml").read_text(encoding="utf-8")) or {}
    out = {}
    for name, entry in (data.get("countries") or {}).items():
        names = [name, *entry.get("aliases", [])]
        for n in names:
            out[fold(n)] = names
    return out

@dataclass(frozen=True, slots=True)
class GeoMatch:
    status: GeoStatus
    reason: str
    evidence: str = ""

    @property
    def is_target(self) -> bool:
        return self.status in TARGET_STATUSES

@dataclass(frozen=True, slots=True)
class DocumentScope:
    country: Scope = "UNKNOWN"
    region: Scope = "UNKNOWN"
    reasons: tuple[str, ...] = ()

    @property
    def elsewhere(self) -> bool:
        return self.country == "OTHER" or self.region == "OTHER"

    def label(self) -> str:
        if self.country == "OTHER":
            return "OTHER_COUNTRY"
        if self.region == "OTHER":
            return "OTHER_REGION"
        return "TARGET_COUNTRY" if self.country == "TARGET" else "UNKNOWN"

UNKNOWN_SCOPE = DocumentScope(reasons=("no signal",))

@dataclass(slots=True)
class Decision:
    status: GeoStatus
    reason: str
    snippet: str

@dataclass(slots=True)
class Neutralized:
    text: str  # same length as the input; other places' mentions blanked
    decisions: list[Decision] = field(default_factory=list)

    @property
    def kept(self) -> list[Decision]:
        return [d for d in self.decisions if d.status in TARGET_STATUSES]

def _blank(text: str, spans: list[tuple[int, int]]) -> str:
    if not spans:
        return text
    chars = list(text)
    for a, b in spans:
        for i in range(a, b):
            if not chars[i].isspace():
                chars[i] = " "
    return "".join(chars)

def _snippet(text: str, a: int, b: int, pad: int = 40) -> str:
    return " ".join(text[max(0, a - pad):b + pad].split())

class GeoMatcher:
    def __init__(self, identity: CityIdentity):
        self.identity = identity
        info = identity.country_info
        own_country = [identity.country, *(info.names if info else [])]
        own_country += _country_aliases().get(fold(identity.country), [])
        self.country_names = list(dict.fromkeys(own_country))
        self.region_names = identity.region_names()
        self.city_names = identity.city_names()
        # In a city-state (Vienna, Hamburg) the region is the city: sharing its name is fine.
        city_state = fold(identity.city) in {fold(r) for r in self.region_names}
        self.strong_names = [n.name for n in identity.names if not n.ambiguous_with
                             or (city_state and set(n.ambiguous_with) == {"region"})]
        targets = {fold(n) for n in (*self.country_names, *self.region_names, *self.city_names)}
        homonym_names = [h.name for h in identity.homonyms if fold(h.name) not in targets]
        _, gazetteer_countries = gazetteer()
        other_countries = {h.country for h in identity.homonyms} | set(gazetteer_countries)
        self.other_country_names = [c for c in other_countries
                                    if fold(c) not in {fold(x) for x in self.country_names}]
        self.other_region_names = [r for r in {h.region for h in identity.homonyms if h.region}
                                   if fold(r) not in targets]
        self.city_re = _names_regex(self.city_names)
        self.strong_re = _names_regex(self.strong_names) if self.strong_names else None
        self.country_re = _names_regex(self.country_names)
        # A city-state's region has the city's own name: that never confirms the city.
        city_keys = {fold(n) for n in self.city_names}
        signal_regions = [r for r in self.region_names if fold(r) not in city_keys]
        self.region_re = _names_regex(signal_regions) if signal_regions else None
        self.context_re = (_names_regex(identity.context_place_names())
                           if identity.context_names else None)
        self.homonym_re = _names_regex(homonym_names) if homonym_names else None
        self.other_country_re = _names_regex(self.other_country_names)
        self.other_region_re = (_names_regex(self.other_region_names)
                                if self.other_region_names else None)
        city_alt = "|".join(_letters(n) for n in identity.catalog_names())
        self.qualified_re = re.compile(
            rf"(?<!\w)(?:{city_alt})\s+{_QUALIFIER}\s+({_CAP_WORD}(?:\s+{_CAP_WORD})?)")
        self.large_homonym = any((h.population or 0) >= LARGE_HOMONYM
                                 for h in identity.homonyms)
        info_cc = (info.cctld or info.iso2 or "").casefold() if info else ""
        self.cctld = info_cc
        region = identity.region
        self.region_host_labels = set()
        if region:
            if region.iso_3166_2 and "-" in region.iso_3166_2:
                self.region_host_labels.add(region.iso_3166_2.split("-", 1)[1].casefold())
            self.region_host_labels |= {fold(n).replace(" ", "") for n in self.region_names}

    # --- signals -------------------------------------------------------------------------

    def _target_signal(self, text: str) -> str | None:
        for pattern, label in ((self.strong_re, "verified name"), (self.region_re, "region"),
                               (self.country_re, "country")):
            if pattern is not None and (m := pattern.search(text)):
                return f"{label} {m.group(0)!r}"
        return None

    def _other_signal(self, text: str) -> str | None:
        if m := self.other_country_re.search(text):
            return f"other country {m.group(0)!r}"
        if self.other_region_re is not None and (m := self.other_region_re.search(text)):
            return f"other region {m.group(0)!r}"
        return None

    # --- document scope ------------------------------------------------------------------

    def host_scope(self, url: str) -> DocumentScope:
        host = (urlsplit(url or "").hostname or "").casefold().removeprefix("www.")
        labels = host.split(".") if host else []
        if not labels:
            return UNKNOWN_SCOPE
        tld = labels[-1]
        second = labels[-2] if len(labels) >= 2 else ""
        us = self.cctld == "us" or fold(self.identity.country) in _US_NAMES
        if tld in ("gov", "mil") or (tld == "us" and second in US_STATE_CODES | {"fed"}):
            country: Scope = "TARGET" if us else "OTHER"
            region: Scope = "UNKNOWN"
            state = second if tld == "us" else (second if second in US_STATE_CODES else "")
            if not state and tld == "gov":
                state = next((lab for lab in labels[:-1] if lab in US_STATE_CODES), "")
                state = state or (second if second.isalpha() else "")
            if us and state:
                if state in self.region_host_labels:
                    region = "TARGET"
                elif state in US_STATE_CODES or state in {
                        fold(r).replace(" ", "") for r in self.other_region_names}:
                    region = "OTHER"
            return DocumentScope(country, region, (f"host {host}",))
        if not self.cctld:  # country code unknown (no approved identity): no host signal
            return UNKNOWN_SCOPE
        if len(tld) == 2 and second in GOV_SECOND_LEVEL:  # gov.co, gob.cl, go.ke
            return DocumentScope("TARGET" if tld == self.cctld else "OTHER", "UNKNOWN",
                                 (f"government host {host}",))
        if len(tld) == 2 and tld not in GENERIC_CCTLDS:
            if tld == self.cctld or (tld == "uk" and self.cctld == "gb"):
                return DocumentScope("TARGET", "UNKNOWN", (f"country-code host .{tld}",))
            return DocumentScope("OTHER", "UNKNOWN", (f"country-code host .{tld}",))
        return UNKNOWN_SCOPE

    def document_scope(self, url: str, title: str | None, texts: Iterable[str]) -> DocumentScope:
        host = self.host_scope(url)
        body = "\n".join([title or "", *texts])
        title_text = title or ""
        mine = len(self.country_re.findall(body))
        theirs = len(self.other_country_re.findall(body))
        reasons = list(host.reasons)
        country = host.country
        if host.country == "UNKNOWN":
            if mine and not theirs:
                country = "TARGET"
            elif theirs and not mine and (theirs >= 2 or self.other_country_re.search(
                    title_text)):
                country = "OTHER"
            elif mine > 2 * theirs:
                country = "TARGET"
            elif theirs > 2 * mine and theirs >= 3:
                country = "OTHER"
            reasons.append(f"country names: target {mine}, other {theirs}")
        elif host.country == "OTHER" and host.reasons and "government" not in host.reasons[0] \
                and mine >= 2 and mine > 2 * theirs:
            country = "TARGET"  # a foreign commercial ccTLD writing about the target country
            reasons.append(f"country names: target {mine}, other {theirs}")
        region = host.region
        if region == "UNKNOWN" and country != "OTHER" and self.other_region_re is not None:
            own = len(self.region_re.findall(body)) if self.region_re else 0
            other = len(self.other_region_re.findall(body))
            if other and not own and (other >= 2 or self.other_region_re.search(title_text)):
                region = "OTHER"
            elif own and not other:
                region = "TARGET"
            reasons.append(f"region names: target {own}, other {other}")
        return DocumentScope(country, region, tuple(reasons))

    # --- mentions ------------------------------------------------------------------------

    def neutralize(self, text: str, scope: DocumentScope | None = None) -> Neutralized:
        """``text`` with mentions of other places that share the city's name blanked, and the
        decision for each mention of the name."""
        scope = scope or UNKNOWN_SCOPE
        # Mentions are looked for with water bodies blanked ("Lake Houston" isn't Houston);
        # the returned text keeps them (retrieval looks for named water bodies).
        work = HYDRONYM.sub(lambda m: " " * len(m.group(0)), text)
        blank: list[tuple[int, int]] = []
        decisions: list[Decision] = []
        target_keys = {fold(n) for n in self.city_names}
        place_keys = {fold(n) for n in (*self.country_names, *self.region_names)}
        for m in self.qualified_re.finditer(work):
            whole, qualifier = fold(m.group(0)), fold(m.group(1))
            own_multiword = (k for k in target_keys if " " in k)
            if whole in target_keys or any(whole.startswith(k + " ") for k in own_multiword):
                continue  # one of the city's own names ("Santiago de Chile")
            if qualifier in place_keys or qualifier.split()[0] in place_keys:
                continue  # qualified by its own region/country
            blank.append(m.span())
            decisions.append(Decision("HOMONYM", f"qualified form {m.group(0)!r}",
                                      _snippet(work, *m.span())))
        if self.homonym_re is not None:
            for m in self.homonym_re.finditer(work):
                if any(a <= m.start() < b for a, b in blank):
                    continue
                blank.append(m.span())
                decisions.append(Decision("HOMONYM", f"homonym {m.group(0)!r}",
                                          _snippet(work, *m.span())))
        work = _blank(work, blank)
        # Metropolitan/urban-area names ("Greater Houston") are context: at most PROBABLE, and
        # the city name inside them is not a separate mention.
        if self.context_re is not None:
            context_spans = []
            for m in self.context_re.finditer(work):
                context_spans.append(m.span())
                if scope.elsewhere:
                    decisions.append(Decision("HOMONYM", f"metropolitan name {m.group(0)!r} "
                                              f"in a document about {scope.label().lower()}",
                                              _snippet(work, *m.span())))
                else:
                    decisions.append(Decision("PROBABLE", f"metropolitan/urban area "
                                              f"{m.group(0)!r}", _snippet(work, *m.span())))
            work = _blank(work, context_spans)
        bare: list[tuple[int, int]] = []
        for m in self.city_re.finditer(work):
            a, b = m.span()
            near = work[max(0, a - WINDOW):b + WINDOW]
            strong = self.strong_re is not None and self.strong_re.fullmatch(m.group(0))
            target = "verified name" if strong else self._target_signal(near)
            other = self._other_signal(near)
            snip = _snippet(work, a, b)
            if target and not other:
                decisions.append(Decision("CONFIRMED", f"{target} nearby", snip))
            elif target and other:
                if scope.elsewhere:
                    bare.append((a, b))
                    decisions.append(Decision("HOMONYM", f"{other} nearby in a document about "
                                              f"{scope.label().lower()}", snip))
                else:
                    decisions.append(Decision("CONFIRMED", f"{target} nearby ({other} too)",
                                              snip))
            elif other and scope.country == "TARGET" and scope.region != "OTHER":
                decisions.append(Decision("PROBABLE", f"document about the target country "
                                          f"({other} nearby)", snip))
            elif other:
                bare.append((a, b))
                decisions.append(Decision("HOMONYM", f"{other} nearby", snip))
            elif scope.elsewhere:
                bare.append((a, b))
                decisions.append(Decision("HOMONYM", f"document about "
                                          f"{scope.label().lower()}", snip))
            elif scope.country == "TARGET":
                decisions.append(Decision("PROBABLE", "document about the target country",
                                          snip))
            elif self.large_homonym:
                bare.append((a, b))
                decisions.append(Decision("AMBIGUOUS", "bare name, unknown document scope, a "
                                          "large place shares the name", snip))
            else:
                decisions.append(Decision("PROBABLE", "bare name, no large homonym", snip))
        return Neutralized(_blank(text, blank + bare), decisions)

    def match_place(self, text: str, scope: DocumentScope | None = None,
                    context: str = "") -> GeoMatch:
        """The place ``text`` (with optional heading/title ``context``) is about."""
        n = self.neutralize(text + ("\n" + context if context else ""), scope)
        if n.kept:
            best = next((d for d in n.kept if d.status == "CONFIRMED"), n.kept[0])
            return GeoMatch(best.status, best.reason, best.snippet)
        if any(d.status == "HOMONYM" for d in n.decisions):
            d = next(d for d in n.decisions if d.status == "HOMONYM")
            return GeoMatch("HOMONYM", d.reason, d.snippet)
        if any(d.status == "AMBIGUOUS" for d in n.decisions):
            d = next(d for d in n.decisions if d.status == "AMBIGUOUS")
            return GeoMatch("AMBIGUOUS", d.reason, d.snippet)
        if self.context_re is not None and (m := self.context_re.search(n.text)):
            if (scope or UNKNOWN_SCOPE).elsewhere:
                return GeoMatch("OTHER_PLACE", "metropolitan name in a document about "
                                "another place", m.group(0))
            return GeoMatch("PROBABLE", f"metropolitan/urban area {m.group(0)!r}", m.group(0))
        if m := self.other_country_re.search(n.text):
            return GeoMatch("OTHER_PLACE", f"other country {m.group(0)!r}", m.group(0))
        return GeoMatch("NONE", "the city is not named")

    def names_city(self, text: str, scope: DocumentScope | None = None) -> bool:
        return bool(self.neutralize(text, scope).kept)

    def locality_patterns(self, other: re.Pattern[str]) -> tuple[re.Pattern[str],
                                                                  re.Pattern[str]]:
        """(target, other) for retrieval locality on neutralized text: the city's verified
        names or its country; other places = ``other`` plus other countries and the homonyms'
        regions."""
        target = re.compile("|".join(p.pattern for p in (self.city_re, self.country_re)))
        extra = [p.pattern for p in (self.other_country_re, self.other_region_re) if p]
        return target, re.compile("|".join([other.pattern, *extra]))

def index_scope(matcher: GeoMatcher, index, url: str | None) -> DocumentScope:
    """The scope of ``url``'s document in a validation EvidenceIndex (cached on the index)."""
    if not url:
        return UNKNOWN_SCOPE
    cache = index.__dict__.setdefault("_geo_scopes", {})
    if url not in cache:
        texts = [c.text for (u, _), cs in index.by_key.items() if u == url for c in cs]
        title = (index.documents.get(url) or {}).get("title")
        cache[url] = matcher.document_scope(url, title, texts)
    return cache[url]

# --- per-run helpers ------------------------------------------------------------------------

def _allow_draft() -> bool:
    return os.environ.get("CITY_IDENTITY_ALLOW_DRAFT", "").strip().lower() in {"1", "true",
                                                                                "yes"}

@lru_cache(maxsize=64)
def _matcher(city: str, country: str, allow_draft: bool) -> GeoMatcher | None:
    ident = identity_for(city, country, allow_draft=allow_draft)
    if ident.status == "CATALOG_ONLY":
        return None
    return GeoMatcher(ident)

def matcher_for(city: str | None, country: str | None) -> GeoMatcher | None:
    """The matcher for an approved identity (or a draft when CITY_IDENTITY_ALLOW_DRAFT is set,
    for evaluation only), else None: callers keep their name-only behaviour."""
    if not city or not country:
        return None
    return _matcher(city.strip(), country.strip(), _allow_draft())

def identity_signature(city: str | None, country: str | None) -> str:
    """Part of cache keys: changes when the identity used for the city changes."""
    m = matcher_for(city, country)
    if m is None:
        return "catalog-only"
    return hashlib.sha256(m.identity.model_dump_json().encode()).hexdigest()[:16]

def document_scopes(matcher: GeoMatcher, chunks: Iterable) -> dict[str, DocumentScope]:
    """url -> scope for the documents of ``chunks`` (EvidenceChunk-like: url, title, text)."""
    by_url: dict[str, list] = {}
    for c in chunks:
        by_url.setdefault(c.url, []).append(c)
    return {u: matcher.document_scope(u, cs[0].title, (c.text for c in cs))
            for u, cs in by_url.items()}

def scope_for(scopes: Mapping[str, DocumentScope], url: str | None) -> DocumentScope:
    return scopes.get(url or "", UNKNOWN_SCOPE)
