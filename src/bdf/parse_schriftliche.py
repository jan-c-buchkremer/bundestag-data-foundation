"""The Schriftliche Fragen of one week and their answers, read from the Sammeldrucksache's PDF (question_text and
question_table rows; docs/design.md "Question texts").

A Sammeldrucksache "Schriftliche Fragen mit den in der Woche vom … eingegangenen Antworten der Bundesregierung"
prints every question with its answer, grouped by the answering ministry. It is read with pdfplumber, with the
helpers of bdf/parse_answers.py:

- **Pages before the body** (the title block, "Verzeichnis der Fragenden", "Verzeichnis der Fragen nach
  Geschäftsbereichen") are skipped: the body starts with the first ministry heading or question. Running heads
  (above HEAD) and smaller type (footnotes, notes under tables) are left out.
- **Ministry headings** are set in Arial bold at the text's left edge ("Geschäftsbereich des Bundesministeriums der"
  / "Finanzen"); their lines are joined.
- **A question** opens with its number in the margin (left of 136 pt) followed by "Abgeordnete", "Abgeordneter" or
  "Abg." ("31. Dezember" in the margin is not one). Its lines are set in two columns on the same baselines: the
  asker on the left (the name in bold, may wrap, then the Fraktion in parentheses), the question from 238 pt on.
  Characters are split at QUESTION_X, so a line holds both columns without merging them.
- **The answer** opens with a bold heading at the left edge, "Antwort des Parlamentarischen Staatssekretärs Michael
  Schrodi" / "vom 8. Juli 2026" (rarely "Antwort der Bundesregierung"); its text runs full width up to the next
  question or ministry heading. Questions without an answer between them are answered jointly ("Die Fragen 6 und 7
  werden gemeinsam beantwortet."): the answer is stored with each.
- **Paragraphs** and **tables** follow bdf/parse_answers.py: lines joined by ``_join``, a gap of more than 1.4 times
  the type size starts a paragraph; ruled tables are cut out and kept as cells, a table continued on the next page
  is merged, tables ruled only in their head or not at all are kept as ``{"extracted": false, "page": n}``.
- **Advance versions** ("Vorabfassung - wird durch die lektorierte Version ersetzt.", up the right margin): the
  note's characters are not upright and are left out.
- **The end**: the body ends with "Berlin, den …"; the annexes after it (not printed, or printed as facsimiles) are
  not read.

Parsing a long Sammeldrucksache takes up to a minute, so ``parse`` keeps its result next to the PDF.
"""

import json
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

import pdfplumber

from bdf.names import clean_text
from bdf.parse_answers import (
    _MONTHS,
    HEAD,
    TINY,
    _cells,
    _column_of_numbers,
    _join,
    _line_text,
    _lines,
    _merge_tables,
    _table,
    _unruled_regions,
)

LEFT = 141.7  # the text's left edge: ministry and answer headings, the asker's column
MARGIN = 136  # a question's number stands left of this (117-129 pt)
QUESTION_X = 236  # the question's column starts at 238.1, the asker's ends before
_START = re.compile(r"^(\d{1,3})\s*\.\s*(?:Abgeordneter?|Abg\.)(?=\s|$)")
# "vom 8. Juli 2026"; misprinted "vom 21 Mai 2025", "vom 19. November2025", "von 20. Januar 2026", "20626"
_DATED = re.compile(r"\s+vo[mn]\s+\d{1,2}\b.*$")
_DATE = re.compile(r"(\d{1,2})\.?\s*([A-Za-zä]+)\s*(\d{4})$")
_END = re.compile(r"^Berlin, den\b")


@dataclass
class Line:
    page: int
    top: float
    x0: float  # of its first printed character
    size: float
    text: str  # with its trailing space, if the PDF has one
    bold: bool  # every printed character bold
    arial: bool
    left: list[tuple[str, bool]]  # the asker's column: (text, bold) runs left of QUESTION_X
    right: str  # the question's column, from QUESTION_X on


@dataclass
class Entry:
    number: str
    asker: str  # the name as printed, "Dr. Malte Kaufmann"
    fraction: str | None  # "AfD"
    ministry: str | None  # the section heading, "Geschäftsbereich des Bundesministeriums der Finanzen"
    question: list[str] = field(default_factory=list)  # paragraphs
    answer_name: str | None = None  # "des Parlamentarischen Staatssekretärs Michael Schrodi"
    answer_date: str | None = None
    answer: list[str] = field(default_factory=list)  # paragraphs; a joint answer stands with each of its questions
    tables: list[tuple[int, dict]] = field(default_factory=list)  # (paragraphs of the answer before it, cells)


@dataclass
class Collection:
    entries: list[Entry]


def _runs(chars: list[dict]) -> list[tuple[str, bool]]:
    """The characters as runs of bold and regular text."""
    runs: list[tuple[list[dict], bool]] = []
    for ch in chars:
        bold = "Bold" in ch["fontname"]
        if runs and (runs[-1][1] == bold or ch["text"].isspace()):
            runs[-1][0].append(ch)
        else:
            runs.append(([ch], bold))
    return [(_line_text(cs), bold) for cs, bold in runs]


def read(path: Path) -> list:
    """The body as one stream of ("page", n), ("line", Line) and ("table", rows) in reading order, up to "Berlin,
    den …"."""
    stream: list = []
    with pdfplumber.open(path) as pdf:
        for n, page in enumerate(pdf.pages, start=1):
            tables = []
            for t in page.find_tables():
                rows = t.extract()
                collapsed = len(rows) <= 3 and any(_column_of_numbers(c) for row in rows for c in row)
                tables.append((t.bbox, None if collapsed else _cells(rows)))
            for bbox in _unruled_regions(page, [b for b, rows in tables if rows is not None]):
                tables.append((bbox, None))
            boxes = [b for b, _ in tables]
            chars = [ch for ch in page.chars if ch["upright"]  # not the "Vorabfassung" printed up the margin
                     and not any(b[0] - 1 <= ch["x0"] and ch["x1"] <= b[2] + 1 and b[1] - 1 <= ch["top"] <= b[3] + 1
                                for b in boxes)]  # fmt: skip
            items = [(b[1], ("table", rows if rows is not None else {"extracted": False, "page": n}))
                     for b, rows in tables if b[1] >= HEAD]  # fmt: skip
            end = False
            for top, cs in _lines(chars).items():
                cs.sort(key=lambda c: c["x0"])
                printed = [c for c in cs if not c["text"].isspace()]
                text = _line_text(cs)
                if top < HEAD or not printed:
                    continue  # running head
                size = Counter(round(c["size"], 1) for c in printed).most_common(1)[0][0]
                if size < TINY:
                    continue  # footnotes, notes under tables
                if _END.match(text.strip()):
                    end, cut = True, top
                    continue
                line = Line(n, top, printed[0]["x0"], size, text, all("Bold" in c["fontname"] for c in printed),
                            all("Arial" in c["fontname"] for c in printed),
                            _runs([c for c in cs if c["x0"] < QUESTION_X]),
                            _line_text([c for c in cs if c["x0"] >= QUESTION_X]))  # fmt: skip
                items.append((top, ("line", line)))
            items.sort(key=lambda i: i[0])
            if end:
                items = [i for i in items if i[0] < cut]
            stream.append(("page", n))
            stream += [item for _, item in items]
            if end:
                break
    return stream


# raise when the parser changes, so the cached results are read again
VERSION = 3


def parse(path: Path) -> Collection:
    """The questions and answers in the PDF at ``path``, kept next to it (``<pdf>.schriftliche.json``) for as long as
    the PDF and VERSION are unchanged."""
    cache = path.with_name(path.name + ".schriftliche.json")
    key = f"{VERSION}:{path.stat().st_size}:{path.stat().st_mtime_ns}"
    if cache.exists():
        cached = json.loads(cache.read_text(encoding="utf-8"))
        if cached.get("key") == key:
            return Collection([Entry(**{**e, "tables": [tuple(t) for t in e["tables"]]})
                               for e in cached["collection"]["entries"]])  # fmt: skip
    collection = _parse(path)
    cache.write_text(json.dumps({"key": key, "collection": asdict(collection)}, ensure_ascii=False), encoding="utf-8")
    return collection


def _date(day: str, month: str, year: str) -> str | None:
    try:
        return date(int(year), _MONTHS.index(month) + 1, int(day)).isoformat()
    except ValueError:  # a misprinted date
        return None


def _glue(a: str, b: str) -> str:
    """Two lines of the asker's column: "Dr. Moritz" "Heuberger", "(BÜNDNIS 90/" "DIE GRÜNEN)"."""
    a, b = a.strip(), b.strip()
    if not a or not b:
        return a + b
    return a + b if a.endswith(("/", "-")) else f"{a} {b}"


def _parse(path: Path) -> Collection:
    stream = _merge_tables(read(path))
    entries: list[Entry] = []
    ministry: list[str] = []  # the heading's lines
    in_heading = False  # the ministry heading is being read
    state = None  # "question" | "heading" (the answer's) | "answer"
    entry: Entry | None = None
    pending: list[Entry] = []  # the questions the next answer answers
    name = fraction = ""
    heading = ""
    paragraphs: list[str] = []  # of the question or the answer being read
    tables: list[tuple[int, dict]] = []
    paragraph = ""
    last: Line | None = None  # the last line of the column being read

    def close_paragraph() -> None:
        nonlocal paragraph
        if paragraph.strip():
            paragraphs.append(clean_text(paragraph))
        paragraph = ""

    def close() -> None:
        """End the question or answer being read."""
        nonlocal paragraphs, tables, last, name, fraction
        close_paragraph()
        if state == "question" and entry is not None:
            entry.asker, entry.fraction = clean_text(name), clean_text(fraction).strip("() ") or None
            entry.question = paragraphs
        elif state in ("heading", "answer"):
            for e in pending:
                e.answer, e.tables = list(paragraphs), list(tables)
            pending.clear()
        paragraphs, tables, last, name, fraction = [], [], None, "", ""

    def add(line: Line, text: str) -> None:
        nonlocal paragraph, last
        if last is not None and (line.top - last.top > 1.4 * line.size
                                 or (line.page != last.page and not paragraph.endswith((" ", "-")))):  # fmt: skip
            close_paragraph()
        if re.search(r"(?:https?://|www\.)\S*-$", paragraph):
            paragraph += text.lstrip()  # a URL broken after one of its hyphens keeps it ("razzia-" "in-ukrainer")
        else:
            paragraph = _join(paragraph, Line(line.page, line.top, line.x0, line.size, text, False, False, [], ""))
        last = line

    for kind, value in stream:
        if kind == "page":
            continue
        if kind == "table":
            if state in ("answer", "heading"):
                close_paragraph()
                tables.append((len(paragraphs), _table(value)))
            continue
        line: Line = value
        text = line.text.strip()
        start = _START.match(text) if line.x0 < MARGIN else None
        if line.arial and line.bold and abs(line.x0 - LEFT) < 3:
            if not in_heading:
                close()
                state, ministry, in_heading = None, [], True
            ministry.append(text)
            continue
        in_heading = False
        if start:
            close()  # after a question: the two are answered jointly, it stays pending
            entry = Entry(start.group(1), "", None, " ".join(ministry) or None)
            entries.append(entry)
            pending.append(entry)
            state = "question"
            if line.right.strip():
                add(line, line.right)
            continue
        if state == "question" and line.bold and text.startswith("Antwort") and abs(line.x0 - LEFT) < 3:
            close()
            state, heading = "heading", text
            if _DATED.search(heading):
                state = "answer"
                _set_heading(pending, heading)
            continue
        if state == "heading":
            if line.bold:
                heading = _glue(heading, text)
                if not _DATED.search(heading):
                    continue
            _set_heading(pending, heading)  # the heading ends with its date, or before the first line of text
            state = "answer"
            if not line.bold:
                add(line, line.text)
            continue
        if state == "question":
            for run, bold in line.left:
                if bold:
                    name = _glue(name, run)
                else:
                    fraction = _glue(fraction, run)
            if line.right.strip():
                add(line, line.right)
            continue
        if state == "answer":
            add(line, line.text)
    close()
    return Collection(entries)


def _set_heading(entries: list[Entry], heading: str) -> None:
    """The answerer and the date from the answer's heading, for each question it answers."""
    dated = _DATED.search(heading)
    m = _DATE.search(dated.group(0)) if dated else None
    answer_name = clean_text(re.sub(r"^Antwort\s+", "", heading[: dated.start()] if dated else heading)) or None
    answered = _date(*m.groups()) if m else None
    for e in entries:
        e.answer_name, e.answer_date = answer_name, answered


def rows(collection: Collection, document: str) -> tuple[list[dict], list[dict]]:
    """question_text and question_table rows (without provenance) of one Sammeldrucksache ``document``: per question
    a "frage" row named after the asker and, if answered, an "antwort" row named after the answerer."""
    texts: list[dict] = []
    tables: list[dict] = []
    for e in collection.entries:
        parts = [("frage", e.question, e.asker, None, [])]
        if e.answer or e.tables:
            parts.append(("antwort", e.answer, e.answer_name, e.answer_date, e.tables))
        for part, paragraphs, name, answered, cells in parts:
            rid = f"{document}/{e.number}/{part}"
            texts.append({
                "id": rid, "drucksache_number": document, "position": len(texts) + 1, "part": part,
                "number": e.number, "text": "\n\n".join(paragraphs), "name": name, "answer_date": answered,
                "thread_id": None,
            })  # fmt: skip
            for k, (after, c) in enumerate(cells, start=1):
                tables.append({"id": f"{rid}/{k}", "question_text_id": rid, "position": k, "after_paragraph": after,
                               "cells": json.dumps(c, ensure_ascii=False)})  # fmt: skip
    return texts, tables
