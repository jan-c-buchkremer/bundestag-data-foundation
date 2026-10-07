"""The pages a preliminary Plenarprotokoll XML lacks, read from the final PDF (docs/design.md, "Preliminary protocols").

bundestag.de serves the preliminary XML under the final URL; in WP 21 some of them end hours before the sitting does
(21/31 stops after TOP 22, the PDF goes on to TOP 29 and beyond). The PDF at the same address is the final version
long before the XML is. ``merge`` reads the PDF, finds where the XML's text stops and appends what follows to the
XML's ``<sitzungsverlauf>`` as the elements the XML would have had: ``<tagesordnungspunkt>`` with its ``T_*`` title
paragraphs, ``<rede>`` with a ``<redner>``, ``<name>`` for the presidency, ``<kommentar>`` and ``<p>``. The parser
then reads them like any other part of the protocol. Every element made here carries ``quelle="pdf"`` and
``seite`` (the Druckseite it starts on), so the rows made from it can name the PDF as their source.

Reading the PDF: a protocol page has two columns, a running head (page number, "Deutscher Bundestag – 21.
Wahlperiode – …", the current speaker), the margin letters (A)–(D) and footnotes. Each column is cropped and read
line by line with its font size and weight:

- a speaker line is set at 9.5 pt, starts with the name in bold and ends with ":" ("Kerstin Griese, Parl.
  Staatssekretärin bei der …:", "Pascal Reddig (CDU/CSU):"); the presidency's is told by its title
  ("Vizepräsident Bodo Ramelow:");
- body text is 10 pt, a paragraph starts with a first-line indent;
- the titles of a called agenda item are 10 pt, indented by about 30 pt ("Erste Beratung des … Drucksache 21/1858");
- a comment is an indented 10 pt paragraph in parentheses ("(Beifall bei der SPD)");
- the Überweisungsvorschlag (7 pt), footnotes (5 pt) and the running head (8–8.5 pt) are left out, and so is the
  printed result of a roll-call vote ("Endgültiges Ergebnis" and the name lists, also 9.5 pt but not bold).

Speakers are matched to the ids the XML gives them elsewhere: the same printed line ("Pascal Reddig (CDU/CSU)")
in another protocol of the Wahlperiode, else the same name. A speaker never seen in an XML gets the id
``pdf-<name>``; ingest reports them.
"""

import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import asdict, dataclass, field, replace
from functools import cache
from pathlib import Path

from bdf.names import clean_text, normalize_fraction

_BODY = 10.0
_SPEAKER = 9.5
_HEAD_BOTTOM = 58  # pt from the top: the running head and the speaker head above the columns
_MARGIN_RE = re.compile(r"^\([A-D]\)\s*|\s*\([A-D]\)$")
_CHAIR_RE = re.compile(r"^(?:Alters)?(?:Vize)?[Pp]räsident(?:in)?\b")
# "Ich rufe den Tagesordnungspunkt 29 auf", "Wir kommen zum Zusatzpunkt 8"; not "Ich komme zurück zu
# Tagesordnungspunkt 25", which announces a roll-call result of an earlier item under the current one, as the XML does
_CALL_RE = re.compile(
    r"\b(?:rufe|kommen|komme)\b(?:(?!zurück).){0,60}?\b(?P<kind>Tagesordnungspunkte?|Zusatzpunkte?)\s+(?P<n>\d+)",
    re.S,
)
_END_RE = re.compile(r"^\(Schluss:\s*\d")
_DRUCKSACHE_START = re.compile(r"^Drucksachen?\s+\d+/\d+")
_RESULT_LIST = re.compile(r"^(?:Endgültiges\s+)?Ergebnis\b")
# the number of a called item hangs left of its first title line ("29 a) Erste Beratung …", "28. Erste Beratung …",
# "ZP 7 Beratung …", "ZP 9 – Zweite und dritte …"); the XML's title starts after it
_TOP_LABEL = re.compile(r"^(?:ZP\s*\d+[a-z]?|\d{1,3}\.?)\s+(?=[a-z]\)\s|–\s|[A-ZÄÖÜ])")
# the bold heading the Anlagen start with ("Anlage 1", "Anlage", "Anlagen zum Stenografischen Bericht"); the sitting
# ends before it, and its pages (excused members, statements on votes, speeches "zu Protokoll") are left out
_ANLAGE_RE = re.compile(r"^Anlagen?(?:\s+\d+)?(?:\s+zum\s+Stenografischen\s+Bericht)?$")
_LINES_VERSION = 6  # bump when Line or the reading changes, so cached lines are read again
# gap between letters that still counts as one word; pdfplumber's default (3 pt) runs the words of some PDFs together
# (21/96: "DeutscherBundestag–21.Wahlperiode")
_X_TOLERANCE = 1.5


@dataclass
class Line:
    page: int  # Druckseite
    text: str
    indent: float  # x of the line's start minus the column's left edge
    top: float
    size: float
    bold: bool
    bold_start: bool  # the first letter is bold: a speaker line starts with the name in bold


@dataclass
class Paragraph:
    kind: str  # speaker | text | comment | title
    page: int
    lines: list[Line] = field(default_factory=list)

    @property
    def text(self) -> str:
        return _join([ln.text for ln in self.lines])

    @property
    def bold(self) -> bool:
        return sum(ln.bold for ln in self.lines) * 2 > len(self.lines)


def _join(lines: list[str]) -> str:
    """Join PDF lines into one paragraph, undoing the hyphenation at line ends: "Bun-" + "despolitik" is one word,
    "CDU/CSU-" + "Fraktion" keeps its hyphen."""
    out = ""
    for ln in lines:
        if not out:
            out = ln
        elif out.endswith("-") and len(out) > 1 and out[-2].isalpha() and ln[:1].isalpha():
            lower = ln[:1].islower() or (out[-2].isupper() and ln[:2].isupper())
            out = out[:-1] + ln if lower else out + ln
        elif out.endswith(("-", "/")) and not out.endswith(" –"):
            out += ln
        else:
            out += " " + ln
    return clean_text(out)


def _line(chars: list[dict], text: str, top: float, left: float, page: int) -> Line | None:
    text = _MARGIN_RE.sub("", text).strip()
    if not text:
        return None
    real = [c for c in chars if c["text"].strip()]
    if real and re.match(r"\([A-D]\)", "".join(c["text"] for c in real[:3])):
        real = real[3:]  # the margin letter starts the line: measure from the text after it
    if not real:
        return None
    size = round(real[0]["size"], 1)  # a speaker line sets the name at 9.5 pt and may set the office at 10 pt
    fonts = [c["fontname"] for c in real]
    return Line(
        page=page,
        text=text,
        indent=real[0]["x0"] - left,
        top=top,
        size=size,
        bold=sum("Bold" in f for f in fonts) * 2 > len(fonts),
        bold_start="Bold" in fonts[0],
    )


# left pages print the number (or a range of numbers) before the running head, right pages after it with spaces
# between the digits ("… 9. Oktober 2025 3 3 9 3"); the table of contents is numbered in roman numerals and is skipped
_PAGE_LEFT_RE = re.compile(r"^(\d{1,5})(?:\s*[-–]\s*\d{1,5})?\s+Deutscher Bundestag")  # "3404-3424 …": a range
_PAGE_RIGHT_RE = re.compile(
    r"^Deutscher Bundestag.*\b\d{4}((?:\s*\d){1,5})(?:\s*[-–](?:\s*\d){1,5})?\s*$"
)  # "… 4 7 0 3 - 4 7 1 8"


def read_lines(pdf_path: Path) -> list[Line]:
    """Every text line of the protocol's sitting part, column by column, with its Druckseite. The title page and the
    table of contents (pages without the running head) are skipped. Reading a protocol's PDF takes about half a
    minute, so the lines are kept next to it (``<pdf>.lines.json``) for as long as the PDF is unchanged."""
    cache_path = pdf_path.with_name(pdf_path.name + ".lines.json")
    key = f"{_LINES_VERSION}:{pdf_path.stat().st_size}:{pdf_path.stat().st_mtime_ns}"
    if cache_path.exists():
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        if cached.get("key") == key:
            return [Line(**ln) for ln in cached["lines"]]
    lines = _read_lines(pdf_path)
    cache_path.write_text(json.dumps({"key": key, "lines": [asdict(ln) for ln in lines]}, ensure_ascii=False))
    return lines


def _read_lines(pdf_path: Path) -> list[Line]:
    import pdfplumber  # imported here: only preliminary protocols need it

    # (Druckseite, column position, pdfplumber lines); a column position is (left or right page, left or right column)
    columns: list[tuple[int, tuple[bool, int], list[dict]]] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            head = (page.extract_text(x_tolerance=_X_TOLERANCE) or "").split("\n", 1)[0].strip()
            left_page = _PAGE_LEFT_RE.match(head)
            m = left_page or _PAGE_RIGHT_RE.match(head)
            if not m or "Wahlperiode" not in head:
                continue
            number = int(re.sub(r"\s", "", m.group(1)))
            mid = page.width / 2
            for i, (x0, x1) in enumerate(((0, mid), (mid, page.width))):
                col = page.crop((x0, _HEAD_BOTTOM, x1, page.height))
                columns.append(
                    (number, (bool(left_page), i), col.extract_text_lines(return_chars=True, x_tolerance=_X_TOLERANCE))
                )
    # the edge a body line starts at is the same on every page of a column position: the most frequent start there.
    # Per column it is not: a stray line left of it, or a column holding more indented title lines than body text,
    # would make every line look indented (21/40, 4649; 21/86, 10612)
    starts: dict[tuple[bool, int], Counter] = {}
    for _, pos, lines in columns:
        starts.setdefault(pos, Counter()).update(
            round(ln["x0"])
            for ln in lines
            if abs(ln["chars"][0]["size"] - _BODY) < 0.3 and not _MARGIN_RE.match(ln["text"])
        )
    edges = {pos: c.most_common(1)[0][0] for pos, c in starts.items() if c}
    out: list[Line] = []
    for number, pos, lines in columns:
        if pos not in edges:
            continue
        for ln in lines:
            line = _line(ln["chars"], ln["text"], ln["top"], edges[pos], number)
            if line is not None and line.size >= 9:
                out.append(line)
    return out


def _quote_goes_on(prev: Line | None, ln: Line) -> bool:
    """A block quote is indented as a whole: a line indented like the one before it continues that one when it was
    not the end of a paragraph (it fills the column or ends hyphenated). A first-line indent follows a line at the
    edge."""
    return (
        prev is not None
        and prev.indent >= 6
        and abs(ln.indent - prev.indent) < 1
        and (prev.text.endswith("-") or len(prev.text) >= 40)
    )


def paragraphs(lines: list[Line]) -> list[Paragraph]:
    """Lines grouped into paragraphs. A new paragraph starts at a speaker line, at a first-line indent, at a jump
    between body text and the indented title block, and at a comment's opening parenthesis; a comment runs until its
    parentheses close, a speaker line until its colon. Stops after "(Schluss: … Uhr)" and before the Anlagen."""
    out: list[Paragraph] = []
    cur: Paragraph | None = None
    prev: Line | None = None
    in_result = False  # the printed name list of a roll-call vote; the XML parser leaves it out too (AL_* classes)
    for i, ln in enumerate(lines):
        nxt = lines[i + 1] if i + 1 < len(lines) else None
        # a bare number only when indented: a body line that goes on sits at the edge ("15 Prozent möglich." before an
        # indented comment); "ZP 14" may sit there too (21/83)
        label = _TOP_LABEL.match(ln.text) if ln.indent < 22 and ln.size == _BODY else None
        if label and ln.indent <= 1 and not ln.text.startswith("ZP"):
            label = None
        labelled = bool(
            label and nxt is not None and nxt.indent >= 22 and nxt.size == _BODY and not nxt.text.startswith("(")
        )
        if labelled:
            # the first title line with its hanging number (21/96, TOP 29): a title line without the number, which
            # starts an item's title
            ln = replace(ln, text=ln.text[label.end() :], indent=nxt.indent)
        if ln.bold and _ANLAGE_RE.match(ln.text):
            break
        if _END_RE.match(ln.text):  # its own paragraph even when printed without indent (21/14)
            out.append(Paragraph("comment", ln.page, [ln]))
            break
        speaker = abs(ln.size - _SPEAKER) < 0.3 and ln.bold_start
        if speaker and _RESULT_LIST.match(ln.text):
            in_result, cur = True, None
            continue
        if in_result:
            # the list's columns hold short lines (names, "Ja", "CDU/CSU"); a speaker or a full line of text ends it
            if not ((speaker and not _RESULT_LIST.match(ln.text)) or (ln.size >= _BODY and len(ln.text) >= 40)):
                continue
            in_result = False
        title = not speaker and ln.indent >= 22 and not ln.text.startswith("(")
        if cur is not None and cur.kind == "speaker" and not cur.text.endswith(":") and len(cur.lines) >= 4:
            cur.kind = "text"  # no colon after four lines: not a speaker line after all
        if (cur is not None and cur.kind == "speaker" and not cur.text.endswith(":")) or (
            cur is not None
            and cur.kind == "comment"
            and cur.text.count("(") > cur.text.count(")")
            and len(cur.lines) < 20  # a long exchange of interjections runs over 8 lines (21/14)
            and not speaker  # a comment is indented and ends before the next speaker, closed or not (21/37)
            and ln.indent >= 6
        ):
            cur.lines.append(ln)
        elif speaker:
            cur = Paragraph("speaker", ln.page, [ln])
            out.append(cur)
        elif ln.text.startswith("(") and ln.indent >= 6:
            cur = Paragraph("comment", ln.page, [ln])
            out.append(cur)
        elif title:
            same_run = (
                cur is not None
                and cur.kind == "title"
                and prev is not None
                # a word hyphenated across the change from regular to bold ("Anpas-" / "sung des …") stays together
                and (prev.bold == ln.bold or (prev.text.endswith("-") and ln.text[:1].islower()))
                and not _DRUCKSACHE_START.match(ln.text)
                and not re.match(r"^[a-z]\)\s", ln.text)
                and not labelled
            )
            if same_run:
                cur.lines.append(ln)
            else:
                cur = Paragraph("title", ln.page, [ln])
                out.append(cur)
        elif cur is not None and cur.kind == "text" and (ln.indent < 6 or _quote_goes_on(prev, ln)):
            cur.lines.append(ln)
        else:
            cur = Paragraph("text", ln.page, [ln])
            out.append(cur)
        prev = ln
    return out


def _norm(s: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKC", s).lower() if ch.isalnum())


def find_cut(paras: list[Paragraph], tail_texts: list[str]) -> int | None:
    """Index of the first paragraph after the XML's last text, or None if the XML's end is not found in the PDF.

    The XML's last paragraphs, normalized to letters and digits, are looked up in the PDF's normalized text, last
    occurrence first: their last 300 letters, else the last paragraph of 25 letters or more that is found (the final
    text can differ in a word from the preliminary one). PDF paragraphs right after it that repeat the XML's
    remaining short paragraphs ("Vielen Dank.", the applause) are skipped."""
    norms = [_norm(t) for t in tail_texts]
    ends, pos = [], 0
    for p in paras:
        pos += len(_norm(p.text))
        ends.append(pos)
    stream = "".join(_norm(p.text) for p in paras)

    def after(needle: str) -> int | None:
        at = stream.rfind(needle)
        return None if at < 0 else next((i + 1 for i, end in enumerate(ends) if end >= at + len(needle)), None)

    tail = "".join(norms)
    if len(tail) >= 40 and (cut := after(tail[-300:])) is not None:
        return cut
    for k in range(len(norms) - 1, -1, -1):
        if len(norms[k]) < 25 or (cut := after(norms[k])) is None:
            continue
        rest = set(norms[k + 1 :])
        while cut < len(paras) and _norm(paras[cut].text) in rest:
            cut += 1
        return cut
    return None


# ------------------------------------------------------------------ speakers


@dataclass(frozen=True)
class KnownSpeaker:
    id: str
    first_name: str
    last_name: str
    fraction: str | None
    role: str | None
    title: str | None
    prefix: str | None


def _key(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("\xa0", " ")).strip().rstrip(":").strip().lower()


@cache
def known_speakers(protocol_dir: Path) -> tuple[dict[str, KnownSpeaker], dict[str, KnownSpeaker]]:
    """({printed speaker line: speaker}, {"first last": speaker}) from every protocol XML in the directory; a name
    two persons share is left out of the second map."""
    printed: dict[str, KnownSpeaker] = {}
    by_name: dict[str, KnownSpeaker | None] = {}
    for path in sorted(protocol_dir.glob("*.xml")):
        for p in ET.parse(path).getroot().iter("p"):
            redner = p.find("redner")
            if p.get("klasse") != "redner" or redner is None:
                continue
            name = redner.find("name")

            def get(tag: str, name: ET.Element = name) -> str | None:
                return clean_text(name.findtext(tag)) or None

            sp = KnownSpeaker(
                id=redner.get("id").split()[0],
                first_name=get("vorname") or "",
                last_name=get("nachname") or "",
                fraction=get("fraktion"),
                role=get("rolle/rolle_lang"),
                title=get("titel"),
                prefix=get("namenszusatz"),
            )
            printed[_key(clean_text(redner.tail))] = sp
            name_key = _key(f"{sp.first_name} {sp.last_name}")
            if name_key not in by_name:
                by_name[name_key] = sp
            elif by_name[name_key] is not None and by_name[name_key].id != sp.id:
                by_name[name_key] = None  # two persons of that name: matched by the printed line only
    return printed, {k: v for k, v in by_name.items() if v is not None}


_SPEAKER_RE = re.compile(r"^(?P<name>[^(,]+?)\s*(?:\((?P<fraction>[^)]+)\)|,\s*(?P<role>.+))?:?$")
_ACADEMIC = re.compile(r"^((?:(?:Dr|Prof)\.(?:\s*(?:h\.\s*c\.|med\.|rer\.\s*nat\.|phil\.|jur\.))*\s+)+)")


def resolve(line: str, protocol_dir: Path) -> KnownSpeaker:
    """The speaker of a printed speaker line, as the XML of another protocol names them."""
    printed, by_name = known_speakers(protocol_dir)
    if (sp := printed.get(_key(line))) is not None:
        return sp
    m = _SPEAKER_RE.match(line.rstrip(":").strip())
    name = m.group("name") if m else line.rstrip(":")
    title_m = _ACADEMIC.match(name)
    bare = name[title_m.end() :] if title_m else name
    if (sp := by_name.get(_key(bare))) is not None:
        return KnownSpeaker(sp.id, sp.first_name, sp.last_name,
                            normalize_fraction(m.group("fraction")) if m and m.group("fraction") else sp.fraction,
                            m.group("role") if m and m.group("role") else None, sp.title, sp.prefix)  # fmt: skip
    words = bare.split()
    slug = re.sub(r"[^a-z0-9]+", "-", unicodedata.normalize("NFKD", bare.lower()).encode("ascii", "ignore").decode())
    return KnownSpeaker(
        id=f"pdf-{slug.strip('-')}",
        first_name=" ".join(words[:-1]),
        last_name=words[-1] if words else bare,
        fraction=normalize_fraction(m.group("fraction")) if m and m.group("fraction") else None,
        role=m.group("role") if m and m.group("role") else None,
        title=title_m.group(1).strip() if title_m else None,
        prefix=None,
    )


# ------------------------------------------------------------------ building the XML


def _el(tag: str, page: int, text: str | None = None, **attrs) -> ET.Element:
    el = ET.Element(tag, {**attrs, "quelle": "pdf", "seite": str(page)})
    el.text = text
    return el


def _redner(sp: KnownSpeaker, printed: str, page: int) -> ET.Element:
    p = _el("p", page, klasse="redner")
    redner = ET.SubElement(p, "redner", id=sp.id)
    name = ET.SubElement(redner, "name")
    for tag, value in (("titel", sp.title), ("vorname", sp.first_name), ("namenszusatz", sp.prefix),
                       ("nachname", sp.last_name), ("fraktion", sp.fraction)):  # fmt: skip
        if value:
            ET.SubElement(name, tag).text = value
    if sp.role:
        ET.SubElement(ET.SubElement(name, "rolle"), "rolle_lang").text = sp.role
    redner.tail = printed
    return p


def _title_class(p: Paragraph) -> str:
    if _DRUCKSACHE_START.match(p.text):
        return "T_Drs"
    return "T_fett" if p.bold else "T_NaS"


def build(paras: list[Paragraph], last_top: ET.Element, sitting_code: str, protocol_dir: Path) -> list[ET.Element]:
    """Append the paragraphs to the protocol: those before the first call-up continue ``last_top`` (the XML's last
    agenda item), each call-up ("Ich rufe den Tagesordnungspunkt 29 auf:") opens a new ``<tagesordnungspunkt>``.
    Returns the new agenda item elements. Stops at "(Schluss: … Uhr)"."""
    tops: list[ET.Element] = []
    top = last_top
    rede: ET.Element | None = None
    speaker_id: str | None = None
    chair = False
    n_rede = 0
    for p in paras:
        text = p.text
        if p.kind == "comment" and _END_RE.match(text):
            break
        if p.kind == "speaker":
            printed = text.rstrip(":").strip()
            if _CHAIR_RE.match(printed):
                (rede if rede is not None else top).append(_el("name", p.page, f"{printed}:"))
                chair = True
                continue
            sp = resolve(printed, protocol_dir)
            if rede is None or sp.id != speaker_id:
                n_rede += 1
                rede = _el("rede", p.page, id=f"ID{sitting_code}pdf{n_rede:03d}")
                top.append(rede)
            rede.append(_redner(sp, f"{printed}:", p.page))
            speaker_id, chair = sp.id, False
            continue
        m = _CALL_RE.search(text) if chair and p.kind == "text" else None
        top_id = m and ("Zusatzpunkt" if m.group("kind").startswith("Zusatz") else "Tagesordnungspunkt") + " " + m["n"]
        if top_id and top_id != top.get("top-id"):  # the same number again calls a sub-item of the current block
            top = _el("tagesordnungspunkt", p.page, **{"top-id": top_id})
            tops.append(top)
            rede = None
            top.append(_el("p", p.page, text, klasse="J"))
            continue
        if p.kind == "title":
            top.append(_el("p", p.page, text, klasse=_title_class(p)))
            continue
        if chair and p.kind == "text" and re.search(r"\bschließe\b.{0,20}\bAussprache\b", text):
            (rede if rede is not None else top).append(_el("p", p.page, text, klasse="J"))
            rede = None
            continue
        target = rede if rede is not None else top
        if p.kind == "comment":
            target.append(_el("kommentar", p.page, text))
        else:
            target.append(_el("p", p.page, text, klasse="J"))
    return tops


def merge(root: ET.Element, pdf_path: Path, protocol_dir: Path) -> int | None:
    """Append to a preliminary protocol's XML what the final PDF has after the XML's last text. Returns the Druckseite
    the XML ends on (where the PDF part starts), or None when the XML's end is not found in the PDF; then nothing is
    appended."""
    verlauf = root.find("sitzungsverlauf")
    tops = verlauf.findall("tagesordnungspunkt") if verlauf is not None else []
    if not tops:
        return None
    last_top = tops[-1]
    marker = [el for el in last_top.iter("p") if el.get("klasse") == "Ende"]
    tail: list[str] = []
    for el in reversed(list(last_top.iter())):
        # the presidency's <name> is printed; a speaker's <name> inside <redner> holds the name fields only
        if el.tag in ("p", "kommentar", "name") and el.get("klasse") not in ("Ende", "redner") and not len(el):
            text = clean_text("".join(el.itertext()))
            if text:
                tail.insert(0, text)
            if len(tail) >= 8:
                break
    paras = paragraphs(read_lines(pdf_path))
    cut = find_cut(paras, tail)
    if cut is None:
        return None
    for el in marker:
        for parent in last_top.iter():
            if el in list(parent):
                parent.remove(el)
    wp, nr = root.get("wahlperiode"), int(root.get("sitzung-nr"))
    new_tops = build(paras[cut:], last_top, f"{wp}{nr:03d}", protocol_dir)
    end = verlauf.find("sitzungsende")
    at = list(verlauf).index(end) if end is not None else len(verlauf)
    for i, top in enumerate(new_tops):
        verlauf.insert(at + i, top)
    return paras[cut - 1].lines[-1].page if cut > 0 else None
