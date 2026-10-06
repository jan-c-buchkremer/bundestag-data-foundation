"""The answer of the Bundesregierung to a Kleine or Große Anfrage, read from its PDF (question_text and question_table
rows; docs/design.md "Question texts").

The Antwort-Drucksache reprints the whole Anfrage: the askers' preliminary remark and every question, "in kleinerer
Schrifttype" (9.6 pt), each followed by the answer (10.7 pt); the government may add its own preliminary remark
before the first question. DIP's ``drucksache-text`` is the PDF's text layer without the type sizes and with the
tables flattened into lines of numbers (docs/plan.md, step 3), so the PDF itself is read, with pdfplumber:

- **Page 1** carries the title block (the Anfrage's Drucksache, "– Drucksache 21/498 –") and, at its foot, the
  note naming the ministry and the date ("Die Antwort wurde namens der Bundesregierung mit Schreiben des
  Bundesministeriums des Innern vom 29. Juli 2025 übermittelt.").
- **Body lines** are the lines below each page's running head; footnotes and the page-1 note are left out. A line in
  the askers' type (9 to 10.2 pt) that opens with a hanging number ("1." set apart by a gap, not a space as in "28.
  Februar"; two en-spaces right-align 1-9 in long Anfragen) opens a question, sub-items "a)" belong to it; the next
  line in the government's type opens the answer. Smaller type (footnotes, notes under tables) is left out.
- **Joint answers**: a run of questions with no answer between them is answered jointly ("Die Fragen 14 bis 16 werden
  … gemeinsam beantwortet."): the answer is stored with each. The questions are reprinted in the order they are
  answered, so the numbers need not run in order.
- **Sub-questions answered one by one**: an "a)" in the askers' type after the question's answer has begun is a
  question of its own, "3a", with its own answer (21/3326).
- **Misprints and repeats**: a question set in the answer's type (21/1343, Frage 16) is read when it carries the next
  number and ends with a question mark at its first paragraph break; a question printed again with a further answer
  (21/7691, once per ministry) keeps one question row, the answers one after the other.
- **Paragraphs**: a line ending in a space continues with a space, one ending in "-" before a lower-case letter is
  joined without the hyphen, one ending in a URL (broken at the line end) without a space, any other with a space;
  a gap of more than 1.4 times the type size starts a new paragraph.
- **Tables**: pdfplumber's ruled tables are cut out of the text and kept as cells with the part they stand in; a table
  that starts a page and has as many columns as the one ending the page before continues it (a repeated header row
  is dropped). A table ruled only in its head (its body collapses into cells of whole columns) and a table without
  ruling (runs of three or more lines with three or more numbers each) are cut out too but not read: their row says
  ``{"extracted": false, "page": n}``, since a row label over two lines or a note across the columns would shift the
  cells.
- **Annexes**: after the first question, a page that is landscape, opens in a type other than the body's Times, or
  opens with "Anlage", "Anhang", "Tabelle" or "Übersicht" starts the annex (``anlage``); a page opening with
  "Anlage <n>" of another number starts the next one. Annexes are text and tables, whatever their type.

Parsing a long answer takes up to a minute, so ``parse`` keeps its result next to the PDF.
"""

import json
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

import pdfplumber

from bdf.names import DRUCKSACHE_RE, clean_text

SMALL = 10.2  # below: the askers' type (9.6 pt); at or above: the government's (10.7 pt)
TINY = 9.0  # below: table notes and annex tables (6.9-8 pt), which stay with the part they stand in
HEAD = 80  # the running head of pages 2 … ("Drucksache 21/731 – 2 – Deutscher Bundestag – 21. Wahlperiode")
FOOT = 770  # page-1 note and footnotes start below this
_QUESTION = re.compile(r"^(\d{1,3})\s*\.\s*(?=\S)")
_SUB = re.compile(r"^([a-z])\)\s*")  # "a) den aktuellen Planungsstand …"
_ANNEX = re.compile(r"^(?:Anlage|Anhang|Tabellen?|Übersicht)\b")  # a page opening with it after the answers
_ANNEX_PART = re.compile(r"^(?:Anlage|Anhang)\b\s*(\d*)")  # … and a new annex part, unless it repeats the number
# heads and page numbers on annex pages, which may sit lower than HEAD ("BT-Drucksache 21/2706", "31")
_RUNNING_HEAD = re.compile(
    r"^(?:(?:BT-)?Drucksache \d+/\d+|Deutscher Bundestag\b|–\s*\d+\s*–|\d{1,3}|Seite \d+ von \d+)\s*$"
)
_MONTHS = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November",
           "Dezember"]  # fmt: skip
_NOTE = re.compile(
    r"mit Schreiben (?:des|der) (?P<ministry>.+?) vom (?P<day>\d{1,2})\.\s*(?P<month>\w+) (?P<year>\d{4}) übermittelt"
)


@dataclass
class Line:
    page: int
    top: float
    x0: float
    size: float
    italic: bool
    bold: bool
    text: str  # with its trailing space, if the PDF has one
    numbered: bool = False  # starts with a hanging number: "1." set apart by a gap, not by a space ("28. Februar")
    serif: bool = True  # in the body's Times, not the Arial of most annexes


@dataclass
class Page:
    number: int
    landscape: bool
    first: str  # the text of its first body line
    serif: bool  # … in the body's Times


@dataclass
class Part:
    part: str  # vorbemerkung_fragesteller | vorbemerkung_bundesregierung | frage | antwort | anlage
    numbers: list[str] = field(default_factory=list)
    paragraphs: list[str] = field(default_factory=list)
    tables: list[tuple[int, dict]] = field(default_factory=list)  # (paragraphs before it, cells)
    misprinted: str | None = None  # a question in the answer's type: its number as printed, "16."


@dataclass
class Answer:
    anfrage: str | None  # the Anfrage's Drucksache, "21/498"
    ministry: str | None  # "Bundesministeriums des Innern", as the note names it
    date: str | None  # the date of the ministry's letter
    parts: list[Part]


def _lines(chars: list[dict]) -> dict[float, list[dict]]:
    """The characters of a page by line, keyed by the line's top: a character joins the line whose baseline is within
    a fifth of its size, a sub- or superscript ("CO2") the nearest line within half the size."""
    lines: dict[float, list[dict]] = {}
    bottoms: dict[float, float] = {}
    sizes: dict[float, float] = {}
    small = []
    for ch in sorted(chars, key=lambda c: (c["bottom"], c["x0"])):
        key = next((k for k in reversed(lines) if abs(bottoms[k] - ch["bottom"]) <= 0.2 * ch["size"]
                    and abs(sizes[k] - ch["size"]) < 1.5), None)  # fmt: skip
        if key is None and lines and ch["size"] < 0.8 * max(sizes.values()):
            small.append(ch)
            continue
        if key is None:
            key = ch["top"]
            lines[key], bottoms[key], sizes[key] = [], ch["bottom"], ch["size"]
        lines[key].append(ch)
    for ch in small:  # sub- and superscripts, footnote marks
        key = min(lines, key=lambda k: abs(bottoms[k] - ch["bottom"]), default=None)
        if key is not None and abs(bottoms[key] - ch["bottom"]) <= 0.5 * sizes[key]:
            lines[key].append(ch)
        else:
            lines[ch["top"]] = [ch]
            bottoms[ch["top"]], sizes[ch["top"]] = ch["bottom"], ch["size"]
    return lines


def _hanging_number(chars: list[dict]) -> bool:
    """The line opens with "<digits>." followed by a gap the PDF leaves without printing a space: a question's
    number in its hanging indent."""
    i = 0
    while i < len(chars) and chars[i]["text"].isdigit():
        i += 1
    if not (0 < i < 4 and i + 1 < len(chars) and chars[i]["text"] == "."):
        return False
    after, dot = chars[i + 1], chars[i]
    return not after["text"].isspace() and after["x0"] - dot["x1"] > 0.25 * after["size"]


def _line_text(chars: list[dict]) -> str:
    """The characters of one line, with a space where the PDF leaves a gap but prints none ("1.Wie" -> "1. Wie")."""
    out = []
    for prev, ch in zip([None, *chars], chars, strict=False):
        if prev and ch["x0"] - prev["x1"] > 0.25 * ch["size"] and not prev["text"].isspace() and ch["text"] != " ":
            out.append(" ")
        out.append(ch["text"])
    return "".join(out)


def _cells(rows: list[list]) -> list[list]:
    """pdfplumber's table rows: soft hyphens and line breaks inside cells joined, a merged cell's None kept."""

    def clean(c: str | None) -> str | None:
        if c is None:
            return None
        c = re.sub(r"[\xad-]\n(?=[a-zäöüß])", "", c)  # "Tötungsde-\nlikte"; "Bund-\nLänder" keeps its hyphen
        return clean_text(c.replace("\xad\n", "").replace("-\n", "-"))

    return [[clean(c) for c in row] for row in rows]


def read(path: Path) -> tuple[list, str]:
    """The PDF as one stream of body lines and tables (("line", Line) | ("table", rows)), in reading order, and the
    text of page 1's title block and foot (for the Anfrage's number, the ministry and the date)."""
    stream: list = []
    head = []
    with pdfplumber.open(path) as pdf:
        for n, page in enumerate(pdf.pages, start=1):
            tables = []
            for t in page.find_tables():
                rows = t.extract()
                # ruled only in its head, the body collapses into cells of whole columns joined by line breaks
                collapsed = len(rows) <= 3 and any(_column_of_numbers(c) for row in rows for c in row)
                tables.append((t.bbox, None if collapsed else _cells(rows)))
            for bbox in _unruled_regions(page, [b for b, rows in tables if rows is not None]):
                tables.append((bbox, None))
            boxes = [b for b, _ in tables]
            chars = [ch for ch in page.chars
                     if not any(b[0] - 1 <= ch["x0"] and ch["x1"] <= b[2] + 1 and b[1] - 1 <= ch["top"] <= b[3] + 1
                                for b in boxes)]  # fmt: skip
            by_top = _lines(chars)
            items = [(b[1], ("table", rows if rows is not None else {"extracted": False, "page": n}))
                     for b, rows in tables]  # fmt: skip
            landscape = page.width > page.height
            head_end = 0 if landscape else HEAD  # an annex page in landscape has no running head
            # page 1: the title block ends with its last bold line ("– Drucksache 21/498 –", the title)
            title_end = max((t for t, cs in by_top.items() if t < 450 and any("Bold" in c["fontname"] for c in cs)),
                            default=HEAD) if n == 1 else HEAD  # fmt: skip
            for top, chars in by_top.items():
                chars.sort(key=lambda c: c["x0"])
                text = _line_text(chars)
                fonts = Counter(c["fontname"] for c in chars).most_common(1)[0][0]
                size = Counter(round(c["size"], 1) for c in chars).most_common(1)[0][0]
                k = next((i for i, c in enumerate(chars) if not c["text"].isspace()), len(chars))
                lead = chars[k:]  # long Anfragen right-align 1-9 with two en-spaces (21/3319)
                line = Line(n, top, chars[0]["x0"], size, "Ital" in fonts, "Bold" in fonts, text, _hanging_number(lead),
                            "Times" in fonts)  # fmt: skip
                if n == 1 and (top <= title_end or top > FOOT):
                    head.append(text.strip())  # the title block and the note
                elif top < head_end or (top > FOOT and not landscape) or not text.strip():
                    continue  # running head, footnotes
                else:
                    items.append((top, ("line", line)))
            items.sort(key=lambda i: i[0])
            first = next((v for _, (k, v) in items if k == "line" and not _RUNNING_HEAD.match(v.text.strip())), None)
            stream.append(
                ("page", Page(n, landscape, first.text.strip() if first else "", first.serif if first else False))
            )
            stream += [item for _, item in items]
    return stream, " ".join(head)


_NUMBER = re.compile(r"[(]?[-+–]?\d[\d.,]*%?[)]?|[*x×–—-]")


def _column_of_numbers(cell: str | None) -> bool:
    """A cell holding a whole column of numbers, one per line: the body of a table ruled only in its head."""
    parts = [p for p in (cell or "").split("\n") if p.strip()]
    return len(parts) >= 3 and sum(bool(_NUMBER.fullmatch(p.replace(" ", ""))) for p in parts) >= 0.6 * len(parts)


def _unruled_regions(page, ruled: list[tuple]) -> list[tuple]:
    """Bounding boxes of tables without ruling: runs of at least three lines with three or more numeric cells each
    (cells: words closer than 0.9 times the type height), with up to four short label lines above, outside the ruled
    tables. Their cells are not read: a row label over two lines or a note across the columns shifts them."""
    words = [w for w in page.extract_words(x_tolerance=1.5)
             if not any(b[0] - 1 <= w["x0"] and w["x1"] <= b[2] + 1 and b[1] - 1 <= w["top"] <= b[3] + 1
                        for b in ruled)]  # fmt: skip
    if not words:
        return []
    size = sorted(w["bottom"] - w["top"] for w in words)[len(words) // 2]
    lines: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if lines and abs(w["top"] - lines[-1][0]["top"]) <= 2:
            lines[-1].append(w)
        else:
            lines.append([w])
    info = []
    for line in lines:
        line.sort(key=lambda w: w["x0"])
        cells = [[line[0]]]
        for w in line[1:]:
            (cells[-1].append(w) if w["x0"] - cells[-1][-1]["x1"] < 0.9 * size else cells.append([w]))
        texts = ["".join(w["text"] for w in c) for c in cells]
        numeric = sum(bool(_NUMBER.fullmatch(t)) for t in texts)
        short = all(len(" ".join(w["text"] for w in c)) < 40 for c in cells)
        info.append((line, numeric, short))
    regions, run = [], []

    def close() -> None:
        while run and info[run[-1]][1] < 3:
            run.pop()
        if sum(info[i][1] >= 3 for i in run) >= 3:
            first = run[0]
            while first > 0 and run[0] - first < 4 and info[first - 1][2]:
                first -= 1
            ws = [w for i in range(first, run[-1] + 1) for w in info[i][0]]
            regions.append((min(w["x0"] for w in ws) - 1, min(w["top"] for w in ws) - 1,
                            max(w["x1"] for w in ws) + 1, max(w["bottom"] for w in ws) + 1))  # fmt: skip
        run.clear()

    for i, (line, numeric, short) in enumerate(info):
        if run and line[0]["top"] - max(w["bottom"] for w in info[run[-1]][0]) > 2.5 * size:
            close()
        if numeric >= 3 or (run and short):
            run.append(i)
        elif run:
            close()
    close()
    return regions


def _join(paragraph: str, line: Line) -> str:
    text = line.text.lstrip()  # its trailing space tells how the next line joins
    if not paragraph or paragraph.endswith(" "):
        return paragraph + text
    if paragraph.endswith("-"):
        return (paragraph[:-1] if text[:1].islower() else paragraph) + text
    if re.search(r"(?:https?://|www\.|/|\.html?|_)\S*$", paragraph):
        return paragraph + text  # a URL broken at the line end
    return paragraph + " " + text  # a line without its trailing space (annexes set in Arial)


def _merge_tables(stream: list) -> list:
    """Join a table that opens a page to the one that closed the page before, when the column counts agree."""
    out: list = []
    previous = None  # the last table or line before this item, page markers skipped
    for kind, value in stream:
        if kind == "page":
            out.append((kind, value))
            continue
        if (kind == "table" and previous and previous[0] == "table" and isinstance(value, list) and value
                and isinstance(previous[1], list) and previous[1] and len(value[0]) == len(previous[1][0])
                and any(k == "page" for k, _ in out[out.index(previous) + 1 :])):  # fmt: skip
            rows = value[1:] if value[0] == previous[1][0] else value
            merged = ("table", previous[1] + rows)
            out[out.index(previous)] = merged
            previous = merged
            continue
        out.append((kind, value))
        previous = (kind, value)
    return out


def _table(rows: list[list] | dict) -> dict:
    """Cells as question_table stores them: the first row as head when the table has more than one; a table whose
    cells could not be read stays {"extracted": false, "page": n}."""
    if isinstance(rows, dict):
        return rows
    return {"caption": None, "head": rows[:1] if len(rows) > 1 else [], "body": rows[1:] if len(rows) > 1 else rows,
            "foot": []}  # fmt: skip


def _unmistake(parts: list[Part]) -> None:
    """A question taken for misprinted that does not end with a question mark was a numbered item of the answer
    before it: put it back, with the answer that followed it."""
    i = 0
    while i < len(parts):
        p = parts[i]
        if p.misprinted is None or " ".join(p.paragraphs).rstrip().endswith("?") or i == 0:
            i += 1
            continue
        into = parts[i - 1]
        absorbed = [p] + ([parts[i + 1]] if i + 1 < len(parts) and parts[i + 1].part == "antwort" else [])
        for k, q in enumerate(absorbed):
            paragraphs = (
                [p.misprinted + q.paragraphs[0], *q.paragraphs[1:]] if k == 0 and q.paragraphs else q.paragraphs
            )
            into.tables += [(len(into.paragraphs) + a, c) for a, c in q.tables]
            into.paragraphs += paragraphs
        del parts[i : i + len(absorbed)]


# raise when the parser changes, so the cached results are read again
VERSION = 7


def parse(path: Path) -> Answer:
    """The answer in the PDF at ``path``. Reading a PDF takes up to a minute (385 pages in 21/6171), so the result is
    kept next to it (``<pdf>.answer.json``) for as long as the PDF and VERSION are unchanged."""
    cache = path.with_name(path.name + ".answer.json")
    key = f"{VERSION}:{path.stat().st_size}:{path.stat().st_mtime_ns}"
    if cache.exists():
        cached = json.loads(cache.read_text(encoding="utf-8"))
        if cached.get("key") == key:
            a = cached["answer"]
            return Answer(a["anfrage"], a["ministry"], a["date"],
                          [Part(p["part"], p["numbers"], p["paragraphs"], [tuple(t) for t in p["tables"]])
                           for p in a["parts"]])  # fmt: skip
    answer = _parse(path)
    cache.write_text(json.dumps({"key": key, "answer": asdict(answer)}, ensure_ascii=False), encoding="utf-8")
    return answer


def _parse(path: Path) -> Answer:
    stream, head = read(path)
    # a table between two lines of one page joins the lines' stream; consecutive tables may continue one another
    # only across a page break, which the stream does not show: tables are merged when nothing stands between them
    stream = _merge_tables(stream)
    parts: list[Part] = []
    current: Part | None = None
    paragraph = ""
    last: Line | None = None
    pending: list[str] = []  # questions waiting for their answer
    asked: set[str] = set()
    main: list[str] = []  # the numbered questions in order, for a sub-question's number
    misprinted_open: set[str] = set()  # a question set in the answer's type, not ended yet

    def close_paragraph() -> None:
        nonlocal paragraph
        if current is not None and paragraph.strip():
            current.paragraphs.append(clean_text(paragraph))
        paragraph = ""

    def start(part: str, numbers: list[str]) -> None:
        nonlocal current, last
        close_paragraph()
        current = Part(part, numbers)
        parts.append(current)
        last = None

    for kind, value in stream:
        if kind == "page":
            page: Page = value
            in_annex = current is not None and current.part == "anlage"
            heading = _ANNEX_PART.match(page.first)
            if not page.first:
                continue  # a blank padding page
            if (in_annex and heading and heading.group(1) != current.numbers[0]) or (not in_annex and main and (
                    page.landscape or not page.serif or _ANNEX.match(page.first))):  # fmt: skip
                start("anlage", [heading.group(1) if heading else ""])  # its printed number: pages repeat it
            continue
        if kind == "table":
            if current is not None:
                close_paragraph()
                current.tables.append((len(current.paragraphs), _table(value)))
            continue
        line: Line = value
        text = line.text.strip()
        squeezed = text.replace(" ", "")
        if current is not None and current.part == "anlage":
            if _RUNNING_HEAD.match(text) or ((current.paragraphs or [paragraph])[0].startswith(text) and last):
                continue  # page heads and numbers; the annex's heading repeated on each page
            if (last is not None and line.top - last.top > 1.4 * line.size) or (
                last is not None and line.page != last.page
            ):
                close_paragraph()
            paragraph = _join(paragraph, line)
            last = line
            continue  # an annex is text and tables, whatever its type
        if squeezed in ("VorbemerkungderFragesteller", "VorbemerkungderFragestellerinnenundFragesteller"):
            start("vorbemerkung_fragesteller", [])
            continue
        if squeezed == "VorbemerkungderBundesregierung":
            start("vorbemerkung_bundesregierung", [])
            continue
        small = TINY <= line.size < SMALL
        tiny = line.size < TINY
        if tiny:
            continue  # footnotes and notes under tables; the answer's text says what a "VS – Vertraulich" note says
        m = _QUESTION.match(text) if line.numbered and (current is None or current.part != "anlage") else None
        # a question misprinted in the answer's type (21/1343, Frage 16): only the next number, up to its first break
        misprinted = (m is not None and not small and not tiny and int(m.group(1)) == len(asked) + 1
                      and m.group(1) not in asked)  # fmt: skip
        if (
            current is not None
            and current.part == "frage"
            and current.numbers[0] in misprinted_open
            and last
            and line.top - last.top > 1.4 * line.size
        ):
            misprinted_open.clear()
            start("antwort", list(pending))
            pending = []
        elif m and (small or misprinted):
            if misprinted:
                misprinted_open.add(m.group(1))
            asked.add(m.group(1))
            main.append(m.group(1))
            start("frage", [m.group(1)])
            current.misprinted = m.group(0) if misprinted else None
            pending.append(m.group(1))
            line = Line(line.page, line.top, line.x0, line.size, line.italic, line.bold, line.text.lstrip()[m.end() :])
        elif small and current is not None and current.part == "antwort" and asked and (sub := _SUB.match(text)):
            # a sub-question answered on its own after the question's first answer (21/3326): Frage "3a"
            number = f"{main[-1]}{sub.group(1)}"
            start("frage", [number])
            pending = [number]
            line = Line(
                line.page, line.top, line.x0, line.size, line.italic, line.bold, line.text.lstrip()[sub.end() :]
            )
        elif not small and not tiny and current is not None and current.part == "frage" and not misprinted_open:
            start("antwort", list(pending))
            pending = []
        elif current is None:
            continue  # before any part: nothing the table knows
        new_page = line.page != last.page if last else False
        if last is not None and (
            line.top - last.top > 1.4 * line.size or (new_page and not paragraph.endswith((" ", "-")))
        ):
            close_paragraph()
        paragraph = _join(paragraph, line)
        last = line
    close_paragraph()
    _unmistake(parts)
    anfrage = next(iter(DRUCKSACHE_RE.findall(re.sub(r"^.*?Wahlperiode", "", head))), None)
    note = _NOTE.search(head)
    answered = None
    if note and note["month"] in _MONTHS:
        answered = date(int(note["year"]), _MONTHS.index(note["month"]) + 1, int(note["day"])).isoformat()
    return Answer(anfrage, clean_text(note["ministry"]) if note else None, answered, parts)


def rows(answer: Answer, document: str) -> tuple[list[dict], list[dict]]:
    """question_text and question_table rows (without provenance) of one answer; ``document`` is the Antwort's
    Drucksache, the source of every row."""
    key = answer.anfrage or document
    texts: list[dict] = []
    tables: list[dict] = []
    annex = 0
    for part in answer.parts:
        if not part.paragraphs and not part.tables:
            continue
        numbers = part.numbers or [None]
        for number in numbers:
            if part.part == "anlage":
                annex += 1
                number = str(annex)
                rid = f"{key}/{number}/anlage"
            elif number is None:
                rid = f"{key}/{part.part}"
            else:
                rid = f"{key}/{number}/{part.part}"
            if (earlier := next((t for t in texts if t["id"] == rid), None)) is not None:
                # a question printed again with a further answer (21/7691 repeats its questions for each ministry):
                # the question once, the answers one after the other
                if part.part in ("antwort", "anlage"):
                    after = len(earlier["text"].split("\n\n")) if earlier["text"] else 0
                    earlier["text"] = "\n\n".join(t for t in (earlier["text"], *part.paragraphs) if t)
                    k0 = sum(1 for t in tables if t["question_text_id"] == rid)
                    tables += [{"id": f"{rid}/{k0 + k}", "question_text_id": rid, "position": k0 + k,
                                "after_paragraph": after + a, "cells": json.dumps(c, ensure_ascii=False)}
                               for k, (a, c) in enumerate(part.tables, start=1)]  # fmt: skip
                continue
            texts.append({
                "id": rid, "drucksache_number": key, "position": len(texts) + 1, "part": part.part, "number": number,
                "text": "\n\n".join(part.paragraphs),
                "name": answer.ministry if part.part in ("antwort", "vorbemerkung_bundesregierung") else None,
                "answer_date": answer.date if part.part not in ("vorbemerkung_fragesteller", "frage") else None,
                "thread_id": None,
            })  # fmt: skip
            for k, (after, cells) in enumerate(part.tables, start=1):
                tables.append({"id": f"{rid}/{k}", "question_text_id": rid, "position": k, "after_paragraph": after,
                               "cells": json.dumps(cells, ensure_ascii=False)})  # fmt: skip
    return texts, tables
