"""Years that a document's structure explicitly attaches to a passage: table rows and headers,
headings and captions, key-value records. Deterministic, on the text as acquired (lines from
the HTML parser and the PDF text layer).

The standardization rules ask for the year a measurement refers to. A year counts only when
the evidence connects it to the measurement:
  table    the value's row is labelled with the year ("2023 | 1,234,000 m3/d"), or sits under
           a header that gives one year, or under a multi-year header whose column lines up
           with the value's cell
  heading  a heading or caption above the value gives exactly one year
           ("Table 4. Water withdrawals, 2023")
  record   a key-value line of the same record ("Year: 2023", "Reporting period: 2022-23")
Publication or posting dates ("July 2018", "Updated 12 March 2024", "© 2021") never count, and
a header with several years whose column can't be lined up is ambiguous (no year).
"""

import re
from dataclasses import dataclass

_YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
_MONTHS = (r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
           r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?|"
           r"enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|"
           r"diciembre|janeiro|fevereiro|março|maio|junho|julho|setembro|outubro|novembro|"
           r"dezembro|janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre|"
           r"novembre|décembre|januar|februar|märz|juni|juli|oktober|dezember")
# A line that is (or starts as) a publication/posting date, not a measurement year.
DATE_LINE = re.compile(
    r"^\W*(?:(?:published|updated|posted|last\s+(?:updated|modified|reviewed)|"
    r"publicado|actualizado|publié|mis\s+à\s+jour|veröffentlicht|aktualisiert|copyright)\b"
    r"|©"
    rf"|(?:\d{{1,2}}(?:st|nd|rd|th)?\s+(?:de\s+)?)?(?:{_MONTHS})\.?\s+(?:\d{{1,2}},?\s+)?"
    r"(?:de\s+)?(?:19|20)\d{2}\W*$"
    r"|\d{1,2}[/.\-]\d{1,2}[/.\-](?:19|20)\d{2}\W*$"
    r"|(?:19|20)\d{2}[/.\-]\d{1,2}[/.\-]\d{1,2}\W*$)", re.IGNORECASE)
_CAPTION = re.compile(r"^\W*(?:table|tab\.|figure|fig\.|chart|exhibit|tabla|cuadro|gráfico|"
                      r"quadro|tabela|figura|tableau|graphique|tabelle|abbildung)\b",
                      re.IGNORECASE)
_RECORD = re.compile(r"^\W*(?:year|reporting\s+(?:year|period)|reference\s+(?:year|period)|"
                     r"period|fiscal\s+year|financial\s+year|fy|año|ano|année|jahr|"
                     r"periodo|período)\s*[:=\-–]\s*(.+)$", re.IGNORECASE)
_CELL_SPLIT = re.compile(r"\s{2,}|\t|\s*\|\s*")
_NUMBER = re.compile(r"\d[\d,.   ]*")
MAX_LINES_UP = 15  # how far above a value a header/heading/caption may govern it
RECORD_WINDOW = 6

@dataclass(frozen=True)
class YearLink:
    year: int | None
    basis: str | None  # table | heading | record
    detail: str = ""
    ambiguous: bool = False

NO_LINK = YearLink(None, None)

def years_of(text: str) -> list[int]:
    return [int(y) for y in _YEAR.findall(text)]

def is_date_line(line: str) -> bool:
    return bool(DATE_LINE.match(line.strip()))

def date_line_years(text: str) -> set[int]:
    """Years that appear on publication/posting-date lines of ``text``: text extraction can
    run such a line into the next one ("July 2018 / Population served: 2,100,000"), so a
    sentence found across lines must not take its year from there."""
    return {y for line in text.splitlines() if is_date_line(line) for y in years_of(line)}

def _norm(text: str) -> str:
    return " ".join(text.split()).casefold()

def locate_line(lines: list[str], quote: str) -> int | None:
    """Index of the line where ``quote`` starts (case/space-insensitive)."""
    head = _norm(quote)[:40]
    if not head:
        return None
    for i, line in enumerate(lines):
        if head in _norm(line):
            return i
    flat = [_norm(x) for x in lines]  # a quote that starts near the end of a line
    for i in range(len(flat) - 1):
        if head in f"{flat[i]} {flat[i + 1]}" and head not in flat[i + 1]:
            return i
    return None

def _cells(line: str) -> list[str]:
    return [c for c in _CELL_SPLIT.split(line.strip()) if c]

def _non_year_numbers(text: str) -> list[str]:
    years = set(_YEAR.findall(text))
    return [n for n in _NUMBER.findall(text) if n.strip(",.") not in years]

def _prose(line: str) -> bool:
    """A sentence-like paragraph: structure above it doesn't govern what follows."""
    s = line.strip()
    return len(s) > 160 and s.endswith((".", "!", "?"))

def _header_row(line: str) -> bool:
    """A table header: years with only a few other words ("Indicator  2021  2022  2023")."""
    s = line.strip()
    if not _YEAR.search(s) or is_date_line(s) or _CAPTION.match(s):
        return False
    words = re.findall(r"[^\W\d_]+", _YEAR.sub(" ", s))
    return len(words) <= 4 and not s.endswith(".")

def _heading(line: str) -> bool:
    s = line.strip()
    return bool(s) and not is_date_line(s) and (
        bool(_CAPTION.match(s)) or (len(s) <= 120 and not s.endswith((".", ";", ","))))

def structural_year(text: str, quote: str, value_hint: str | None = None) -> YearLink:
    """The year the document's structure attaches to ``quote`` (see module docstring).
    ``value_hint``: the number as written, to find the value's cell in a table row."""
    lines = text.splitlines()
    i = locate_line(lines, quote)
    if i is None:
        return NO_LINK
    row = lines[i]
    label = re.match(r"^\W*((?:19|20)\d{2})(?:\s*[-–/]\s*\d{2,4})?\b", row)
    if label and _non_year_numbers(row[label.end():]):
        return YearLink(int(label.group(1)), "table", "row labelled with the year")
    for j in range(max(0, i - RECORD_WINDOW), min(len(lines), i + RECORD_WINDOW + 1)):
        if j != i and not lines[j].strip():
            if j < i:
                continue
            break
        m = _RECORD.match(lines[j])
        if m and not is_date_line(m.group(1)):
            ys = years_of(m.group(1))
            if len(set(ys)) == 1 or (len(ys) == 2 and ys[1] == ys[0] + 1):
                return YearLink(ys[0] if len(set(ys)) == 1 else ys[1], "record",
                                lines[j].strip()[:80])
    for j in range(i - 1, max(-1, i - 1 - MAX_LINES_UP), -1):
        line = lines[j]
        if not line.strip():
            continue
        if _prose(line):
            break
        if is_date_line(line):
            continue
        ys = sorted(set(years_of(line)))
        if _header_row(line):
            if len(ys) == 1:
                return YearLink(ys[0], "table", f"header {line.strip()[:60]!r}")
            header, cells = _cells(line), _cells(row)
            hint = (value_hint or "").strip()
            k = next((n for n, c in enumerate(cells) if hint and hint in c), None)
            if k is not None and len(header) == len(cells):
                y = years_of(header[k])
                if len(y) == 1:
                    return YearLink(y[0], "table", f"column {header[k]!r}")
            elif k is not None and len(header) == len(cells) - 1 and k >= 1:
                y = years_of(header[k - 1])  # a header without a label cell
                if len(y) == 1:
                    return YearLink(y[0], "table", f"column {header[k - 1]!r}")
            return YearLink(None, None, f"several years in the header {line.strip()[:60]!r}",
                            ambiguous=True)
        if _heading(line) and len(ys) == 1:
            return YearLink(ys[0], "heading", line.strip()[:80])
        if _heading(line) and len(ys) > 1:
            return YearLink(None, None, f"several years in {line.strip()[:60]!r}",
                            ambiguous=True)
    return NO_LINK

def governed_lines(text: str, quote: str, window: int = 12) -> tuple[str | None, list[str]]:
    """For a quote that is (part of) a heading, caption, table header or record line: (that
    line, the lines it governs below it); (None, []) if the quote's line is none of these or
    is a publication date."""
    lines = text.splitlines()
    i = locate_line(lines, quote)
    if i is None:
        return None, []
    line = lines[i]
    if is_date_line(line) or not (_heading(line) or _header_row(line) or _RECORD.match(line)):
        return None, []
    below = []
    for line_below in lines[i + 1:i + 1 + window]:
        if _prose(line_below) and below:
            break
        below.append(line_below)
    return line, below
