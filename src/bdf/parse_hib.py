"""Parse "heute im bundestag" (hib): the article pages at bundestag.de/presse/hib/kurzmeldungen-<id> and the list
fragment behind bundestag.de/presse/hib that names them.

An article's header line ("Dachzeile") reads "Inneres — Antwort — hib 784/2026": Ressort, kind, issue number. The
body is the paragraphs of the article's content block; the first starts "Berlin: (hib/STO)", the author code.
"""

import html
import re
from dataclasses import dataclass, field

from bdf.names import clean_text

_ID_RE = re.compile(r"kurzmeldungen-(\d+)")
_DATE_RE = re.compile(r'<span class="bt-date">\s*(\d{2})\.(\d{2})\.(\d{4})\s*</span>')
_DACHZEILE_RE = re.compile(r'<span class="bt-dachzeile">(.*?)</span>', re.S)
_TITLE_RE = re.compile(r'<h1 class="bt-artikel__title">(.*?)</h1>', re.S)
_BODY_RE = re.compile(r'<div class="bt-artikel__article">\s*<div class="bt-standard-content">(.*?)</div>', re.S)
_BLOCK_RE = re.compile(r"<(p|li|h[2-4])\b[^>]*>(.*?)</\1>", re.S)
_AUTHOR_RE = re.compile(r"^Berlin:\s*\(hib/([A-ZÄÖÜ]+)\)\s*")
_BTD_RE = re.compile(r"dserver\.bundestag\.de/btd/(\d+)/\d+/\d{2}(\d+)\.pdf")
_NUMBER_RE = re.compile(r"hib\s+(\d+/\d{4})")
_MONTH_NAMES = ("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober",
                "November", "Dezember")  # fmt: skip
_MONTHS = {m: i + 1 for i, m in enumerate(_MONTH_NAMES)}
_LIST_DAY_RE = re.compile(r">\s*(\d{1,2})\.\s+(" + "|".join(_MONTHS) + r")\s+(\d{4})\s*<")

# Committee reports name their committee in a compound ("Der Forschungsausschuss", "des Innenausschusses") or in
# the full form ("des Ausschusses für Digitales und Staatsmodernisierung"); the full form is cut where the name
# ends, so `full_names` (the committees' official names) decides how far it reaches.
_COMPOUND_RE = re.compile(r"\b((?:[A-ZÄÖÜ][a-zäöüß]+-)?[A-ZÄÖÜ][a-zäöüß]*ausschuss)(?:es)?\b")
_FULL_RE = re.compile(r"\bAusschuss(?:es)? (für|des|zur) ")
COMMITTEE_KINDS = ("Ausschuss", "Anhörung")


@dataclass
class Item:
    id: str
    number: str
    date: str
    title: str
    ressort: str
    kind: str
    author_code: str | None
    text: str
    drucksachen: list[str] = field(default_factory=list)


def article_id(url: str) -> str | None:
    m = _ID_RE.search(url)
    return m.group(1) if m else None


def _strip(fragment: str) -> str:
    return clean_text(html.unescape(re.sub(r"<[^>]+>", " ", fragment)))


def _block_text(fragment: str) -> str:
    """A paragraph without its links' hidden labels ("(Dokument, öffnet ein neues Fenster)")."""
    fragment = re.sub(r'<span class="a-link__label --hidden">.*?</span>', "", fragment, flags=re.S)
    text = _strip(fragment)
    return re.sub(r"\(\s+", "(", re.sub(r"\s+([),.;:])", r"\1", text))


def parse_article(page: str, item_id: str) -> Item | None:
    """One article page; None if it is not a hib article (no header line with a hib number)."""
    date_m, head_m, title_m = _DATE_RE.search(page), _DACHZEILE_RE.search(page), _TITLE_RE.search(page)
    if not (date_m and head_m and title_m):
        return None
    head = [p.strip() for p in _strip(head_m.group(1)).split("—")]
    number = _NUMBER_RE.fullmatch(head[-1]) if head else None
    if not number or len(head) < 3:
        return None
    ressort, kind = " — ".join(head[:-2]), head[-2]
    body = _BODY_RE.search(page)
    paragraphs = [t for _, b in _BLOCK_RE.findall(body.group(1) if body else "") if (t := _block_text(b))]
    author = None
    if paragraphs and (m := _AUTHOR_RE.match(paragraphs[0])):
        author = m.group(1)
        paragraphs[0] = paragraphs[0][m.end() :]
    numbers: list[str] = []
    for wp, n in _BTD_RE.findall(body.group(1) if body else ""):
        number_ds = f"{int(wp)}/{int(n)}"
        if number_ds not in numbers:
            numbers.append(number_ds)
    d, mth, y = date_m.groups()
    return Item(
        id=item_id,
        number=number.group(1),
        date=f"{y}-{mth}-{d}",
        title=_strip(title_m.group(1)),
        ressort=ressort,
        kind=kind,
        author_code=author,
        text="\n\n".join(p for p in paragraphs if p),
        drucksachen=numbers,
    )


def committee(text: str, full_names: list[str]) -> str | None:
    """The committee a report names first, nominative ("Innenausschuss", "Ausschuss für Digitales und
    Staatsmodernisierung"); None if it names none. A full form is kept only as far as one of `full_names` reaches."""
    found: list[tuple[int, str]] = []
    m = _COMPOUND_RE.search(text)
    if m and m.group(1) not in ("Ausschuss", "Unterausschuss"):
        found.append((m.start(), m.group(1)))
    for m in _FULL_RE.finditer(text):
        rest = text[m.start(1) :]
        best = max((n for n in full_names if rest.startswith(n.split(" ", 1)[1])), key=len, default=None)
        if best:
            found.append((m.start(), best))
            break
    return min(found)[1] if found else None


def list_page(fragment: str) -> list[tuple[str, str | None]]:
    """(article id, ISO date of its day heading) for every item of one list fragment, in page order."""
    out: list[tuple[str, str | None]] = []
    seen: set[str] = set()
    day: str | None = None
    for m in re.finditer(r'href="[^"]*kurzmeldungen-(\d+)"|' + _LIST_DAY_RE.pattern, fragment):
        if m.group(1):
            if m.group(1) not in seen:
                seen.add(m.group(1))
                out.append((m.group(1), day))
        else:
            d, month, y = m.group(2), m.group(3), m.group(4)
            day = f"{y}-{_MONTHS[month]:02d}-{int(d):02d}"
    return out
