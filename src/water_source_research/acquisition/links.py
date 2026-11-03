"""Outbound links of an HTML page, kept as citation-grounded context for municipal
affiliation (services/affiliation.py): where the link points, its anchor text, the text
around it, the nearest heading and whether it sits in the page footer. A link is never
evidence on its own: verification needs the official host and a departments context."""

import re
from urllib.parse import urljoin, urlsplit
from bs4 import BeautifulSoup

MAX_LINKS = 300
CONTEXT_CHARS = 300
_FOOTER = re.compile(r"footer|site-?info|colophon|copyright", re.IGNORECASE)
_SKIP = ("mailto:", "tel:", "javascript:", "#", "data:")

def _clean(text: str, n: int) -> str:
    return " ".join((text or "").split())[:n]

def _in_footer(tag) -> bool:
    for parent in tag.parents:
        if parent.name == "footer":
            return True
        attrs = " ".join([parent.get("id") or "", *(parent.get("class") or [])]) \
            if hasattr(parent, "get") else ""
        if attrs and _FOOTER.search(attrs):
            return True
    return False

def extract_links(soup: BeautifulSoup, base_url: str) -> list[dict]:
    """Up to MAX_LINKS outbound links (http/https), the first occurrence of each
    (target, anchor) pair, in page order."""
    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href or href.lower().startswith(_SKIP):
            continue
        target = urljoin(base_url, href)
        parts = urlsplit(target)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            continue
        anchor = _clean(a.get_text(" "), 500)
        key = (target, anchor)
        if key in seen:
            continue
        seen.add(key)
        parent = a.find_parent(["li", "p", "td", "dd", "div"]) or a.parent
        heading = a.find_previous(["h1", "h2", "h3", "h4", "h5", "h6"])
        out.append({"target_url": target[:2048],
                    "target_host": parts.hostname.casefold().removeprefix("www.")[:255],
                    "anchor_text": anchor,
                    "context": _clean(parent.get_text(" ") if parent else anchor,
                                      CONTEXT_CHARS),
                    "heading": _clean(heading.get_text(" "), 300) if heading else "",
                    "in_footer": _in_footer(a)})
        if len(out) >= MAX_LINKS:
            break
    return out
