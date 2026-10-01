"""Preliminary Plenarprotokolle and the pages they lack (docs/design.md, "Preliminary protocols").

bundestag.de serves a protocol's XML under its final URL before the final version exists. That preliminary
version says so in its text ("Der gesamte und damit endgültige Stenografische Bericht der 31. Sitzung wird am
… veröffentlicht") and can end before the sitting does: in WP 21, 8 of 17 preliminary protocols lacked their
last 33–82 pages, the late-evening debates. Two things are read here:

- ``status``: whether an XML is preliminary, the date it announces for the final version, and the Druckseiten
  its ``<sitzungsverlauf>`` covers (``start-seitennr`` to the highest ``druckseitennummer`` marker);
- ``split_pages``: DIP's ``plenarprotokoll-text`` (plain text from the PDF) cut into Druckseiten at the running
  page headers, the fallback for the pages a preliminary XML lacks.

The marker is searched in the whole document text, not in a fixed element, so it is found wherever the
Bundestag prints it. Comments are not text (ElementTree drops them), so an excerpt note cannot trigger it.
"""

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from bdf.names import clean_text

_MONTHS = {
    m: i
    for i, m in enumerate(
        ("januar", "februar", "märz", "april", "mai", "juni", "juli", "august", "september", "oktober", "november",
         "dezember"),
        start=1,
    )
}  # fmt: skip
_STENO = r"Steno(?:g|ph)rafische[nr]?\s+Bericht"
_FINAL_RE = re.compile(rf"endgültige[nr]?\s+{_STENO}", re.I)
_ANNOUNCED_RE = re.compile(rf"endgültige[nr]?\s+{_STENO}.{{0,80}}?\bwird\s+am\s+(.{{4,50}}?)\s+veröffentlicht", re.I)
_PRELIMINARY_KIND_RE = re.compile(rf"vorläufige[nr]?\s+{_STENO}", re.I)
_DATE_NUMERIC_RE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})")
_DATE_WORDS_RE = re.compile(r"(\d{1,2})\.\s*([A-Za-zäÄ]+)\s+(\d{4})")


@dataclass(frozen=True)
class Status:
    preliminary: bool
    final_announced: str | None  # ISO date the marker announces for the final version
    first_page: int | None  # Druckseite the sitzungsverlauf starts on (start-seitennr)
    last_page: int | None  # highest druckseitennummer marker in the sitzungsverlauf


def german_date(s: str) -> str | None:
    """'Montag, den 13. Oktober 2025' or '13.10.2025' -> '2025-10-13'; None if no date is found."""
    if m := _DATE_NUMERIC_RE.search(s):
        day, month, year = (int(g) for g in m.groups())
    elif (m := _DATE_WORDS_RE.search(s)) and m.group(2).lower() in _MONTHS:
        day, month, year = int(m.group(1)), _MONTHS[m.group(2).lower()], int(m.group(3))
    else:
        return None
    return f"{year:04d}-{month:02d}-{day:02d}" if 1 <= month <= 12 and 1 <= day <= 31 else None


def _page_number(el: ET.Element) -> int | None:
    """The page a ``typ="druckseitennummer"`` marker stands for: from its name/id ("S3392") or its text."""
    for value in (el.get("name"), el.get("id"), clean_text("".join(el.itertext()))):
        if value and (m := re.search(r"\d+", value)):
            return int(m.group())
    return None


def status(root: ET.Element) -> Status:
    text = clean_text(" ".join(root.itertext()))
    announced = _ANNOUNCED_RE.search(text)
    preliminary = bool(announced or _FINAL_RE.search(text) or _PRELIMINARY_KIND_RE.search(text))
    verlauf = root.find("sitzungsverlauf")
    pages = [
        n
        for el in (verlauf.iter() if verlauf is not None else ())
        if el.get("typ") == "druckseitennummer" and (n := _page_number(el)) is not None
    ]
    start = root.get("start-seitennr")
    first = int(start) if start and start.isdigit() else min(pages, default=None)
    return Status(
        preliminary=preliminary,
        final_announced=german_date(announced.group(1)) if announced else None,
        first_page=first,
        last_page=max(pages, default=None),
    )


def is_preliminary(path) -> bool:
    return status(ET.parse(path).getroot()).preliminary


# Running header of a protocol page in the PDF text, page number before it (left pages) or after it (right
# pages): "3392 Deutscher Bundestag – 21. Wahlperiode – 31. Sitzung. Berlin, Donnerstag, den 9. Oktober 2025"
_HEADER = (
    r"(?:(?<![\d/.,])(?P<before>\d{{1,5}})\s+)?Deutscher\s+Bundestag\s*[–—-]\s*{wp}\.\s*Wahlperiode\s*[–—-]\s*"
    r"{nr}\.\s*Sitzung\.?\s*[–—-]?\s*Berlin,[^\n]{{0,60}}?\b\d{{4}}\b(?:\s+(?P<after>\d{{1,5}})(?![\d.,/]))?"
)
# a header whose page number jumps further than this from the previous one is taken for a false match
_MAX_PAGE_STEP = 5


def split_pages(text: str, wp: int, nr: int, first_page: int | None = None) -> dict[int, str]:
    """{Druckseite: text} of DIP's plenarprotokoll-text, cut at the running page headers.

    Only headers whose page number continues the sequence (rising, by at most _MAX_PAGE_STEP) start a page, so a
    number that happens to stand next to a header is not taken for a page. Text before the first header (title
    page, table of contents) belongs to no page. Empty when no header is found: the caller then has no fallback
    text and says so, the PDF stays the reference.
    """
    header = re.compile(_HEADER.format(wp=wp, nr=nr))
    starts: list[tuple[int, int, int]] = []  # (page, header start, header end)
    for m in header.finditer(text):
        page = int(m.group("before") or m.group("after") or 0)
        if not page:
            continue
        if starts:
            if not starts[-1][0] < page <= starts[-1][0] + _MAX_PAGE_STEP:
                continue
        elif first_page is not None and not first_page <= page <= first_page + 2000:
            continue
        starts.append((page, m.start(), m.end()))
    pages: dict[int, str] = {}
    for i, (page, _, end) in enumerate(starts):
        stop = starts[i + 1][1] if i + 1 < len(starts) else len(text)
        body = text[end:stop].strip()
        if body:
            pages[page] = body
    return pages
