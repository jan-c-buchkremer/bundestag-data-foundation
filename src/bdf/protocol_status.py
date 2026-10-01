"""Preliminary Plenarprotokolle and the pages they lack (docs/design.md, "Preliminary protocols").

bundestag.de serves a protocol's XML under its final URL before the final version exists. That preliminary
version says so in its text ("Der gesamte und damit endgültige Stenografische Bericht der 31. Sitzung wird am
… veröffentlicht") and can end before the sitting does: in WP 21, 8 of 17 preliminary protocols lacked their
last 33–82 pages, the late-evening debates. ``status`` reads whether an XML is preliminary, the date it announces
for the final version and the Druckseite it starts on (``start-seitennr``). The XML's body carries no page numbers
(the ``druckseitennummer`` anchors are in the table of contents, which a preliminary XML cuts short as well), so
where a preliminary XML ends is found in the final PDF (bdf/protocol_pdf.py), which also supplies what follows.

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
    last_page: int | None = None  # preliminary: Druckseite the XML's text ends on, found in the PDF (protocol_pdf)


def german_date(s: str) -> str | None:
    """'Montag, den 13. Oktober 2025' or '13.10.2025' -> '2025-10-13'; None if no date is found."""
    if m := _DATE_NUMERIC_RE.search(s):
        day, month, year = (int(g) for g in m.groups())
    elif (m := _DATE_WORDS_RE.search(s)) and m.group(2).lower() in _MONTHS:
        day, month, year = int(m.group(1)), _MONTHS[m.group(2).lower()], int(m.group(3))
    else:
        return None
    return f"{year:04d}-{month:02d}-{day:02d}" if 1 <= month <= 12 and 1 <= day <= 31 else None


def status(root: ET.Element) -> Status:
    text = clean_text(" ".join(root.itertext()))
    announced = _ANNOUNCED_RE.search(text)
    preliminary = bool(announced or _FINAL_RE.search(text) or _PRELIMINARY_KIND_RE.search(text))
    start = root.get("start-seitennr")
    return Status(
        preliminary=preliminary,
        final_announced=german_date(announced.group(1)) if announced else None,
        first_page=int(start) if start and start.isdigit() else None,
    )


def is_preliminary(path) -> bool:
    return status(ET.parse(path).getroot()).preliminary
