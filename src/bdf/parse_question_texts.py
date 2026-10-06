"""The written texts of the questions to the government (question_text, question_table; docs/design.md "Question
texts"). For now the Mündliche Fragen, all from the Plenarprotokoll:

- **Called in the Fragestunde.** The presidency calls each question and reads it out ("Wir kommen zur Frage 3 des
  Abgeordneten Bernd Schattner, AfD:", then the question as its own paragraph); the answer and every Nachfrage are
  speeches (question_turn). A call is a chair paragraph naming "Frage <n>" whose next paragraph, before any speech,
  ends with a question mark (one call in 21/67 ends with a full stop, not a colon). Calls that only say a question is
  answered in writing, not answered, or that its asker is absent have no question after them and are skipped.
- **Answered in writing.** The annex "Schriftliche Antworten auf Fragen der Fragestunde (Drucksache 21/1949)" lists
  every question not reached: "Frage 6" (or "Fragen 32 und 33"), "Frage des Abgeordneten Bernd Schattner (AfD):",
  the question (one paragraph per number), "Antwort des Parl. Staatssekretärs Stefan Rouenhoff:", the answer, with
  tables kept as cells (question_table) and left out of the text. A joint answer is stored with each question it
  answers. An entry that says the question is not answered (Nr. 9 Satz 2 of the Richtlinien, no text printed) or that
  the answer was not there by the editorial deadline gives no answer row.

Rows are keyed by the Fragen-Drucksache and the question number, "21/1949/6/frage", "21/1949/6/antwort"; their
Vorgang, answerer and the Drucksache's DIP id are filled in after the DIP ingest (bdf/ingest.py,
``ingest_question_links``).
"""

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from bdf.names import DRUCKSACHE_RE, clean_text
from bdf.parse_protocol import Protocol

_PERSON = r"(?P<name>(?:(?:Dr|Prof)\.\s)*[A-ZÄÖÜ][\w'’-]*(?:\s+(?:(?:von|van|de|dos|zu|der)\s+)?[A-ZÄÖÜ][\w'’-]*)*)"
_TITLES = r"(?:(?:Herrn|Herr|Frau|Abgeordneten|Abgeordnete|Kollegin|Kollegen|Kollege)\s+)"
# "zur Frage 3 des Abgeordneten Bernd Schattner", "Frage 1 stammt vom Kollegen Johannes Wagner", "die Frage 2 auf – der
# Fragesteller ist der Abgeordnete Bernd Schattner –"; else "Frage 4 von Stephan Brandner"
_ASKER = re.compile(r"\bFrage (?P<n>\d+)\b[^:]*?\b" + _TITLES + "+" + _PERSON)
_ASKER_PLAIN = re.compile(r"\bFrage (?P<n>\d+)\b,?\s*(?:von|de[rs])\s+" + _PERSON)
_ORDINALS = {"ersten": 1, "zweiten": 2, "dritten": 3, "vierten": 4, "fünften": 5}
_ORDINAL = re.compile(r"\b(" + "|".join(_ORDINALS) + r") Frage\b")  # "Es geht los mit der ersten Frage des …" (21/76)
_NUMBERS = re.compile(r"Fragen? (\d+(?:\s*(?:,|und)\s*\d+)*)")
_HEADER = re.compile(r"Fragen? de[rs] Abgeordneten (?P<name>.+?)(?:\s*\([^)]*\))?:$")
_NOT_ANSWERED = re.compile(r"nicht beantwortet|bei Redaktionsschluss noch nicht vor")


def spoken_calls(protocol: Protocol) -> list[dict]:
    """The questions called and read out in the protocol's Fragestunden, in order: agenda_item_id, number, name (the
    asker as the presidency names them), text, drucksache_number (the item's Fragen-Drucksache), after_speeches (how
    many speeches of the sitting precede the call) and thread_id (the first speech after the call, before the next
    one; None when nobody spoke to it)."""
    fragestunden = {s.agenda_item_id for s in protocol.speeches if s.kind == "fragestunde"}
    calls = []
    for item in protocol.agenda_items:
        if item["id"] not in fragestunden:
            continue
        drucksache = next(iter(json.loads(item["drucksache_numbers"])), None)
        paragraphs = [p for p in protocol.agenda_paragraphs if p["agenda_item_id"] == item["id"]]
        texts = [_ORDINAL.sub(lambda o: f"Frage {_ORDINALS[o.group(1)]}", p["text"]) for p in paragraphs]
        for i, p in enumerate(paragraphs):
            if p["kind"] != "chair" or not (m := _ASKER.search(texts[i]) or _ASKER_PLAIN.search(texts[i])):
                continue
            question = []
            for k in range(i + 1, min(i + 4, len(paragraphs))):  # a question of several paragraphs ends at its "?"
                q = paragraphs[k]
                if (
                    q["after_speeches"] != p["after_speeches"]
                    or q["kind"] != "chair"
                    or re.search(r"\bFrage \d", texts[k])
                ):
                    break  # a speech, or the next call: this one only said the question is answered in writing
                question.append(q["text"])
                if q["text"].rstrip().endswith("?"):
                    break
            if not question or not question[-1].rstrip().endswith("?"):
                continue
            name = re.sub(r"^" + _TITLES + "+", "", m["name"]).rstrip(".")
            calls.append(
                {"agenda_item_id": item["id"], "number": m["n"], "name": name, "text": "\n\n".join(question),
                 "drucksache_number": drucksache, "after_speeches": p["after_speeches"]}
            )  # fmt: skip
    for i, c in enumerate(calls):
        following = calls[i + 1] if i + 1 < len(calls) else None
        end = following["after_speeches"] if following and following["agenda_item_id"] == c["agenda_item_id"] else None
        c["thread_id"] = next(
            (s.id for s in protocol.speeches
             if s.agenda_item_id == c["agenda_item_id"] and s.position > c["after_speeches"]
             and (end is None or s.position <= end)),
            None,
        )  # fmt: skip
    return calls


def _cells(row: ET.Element) -> list:
    """A table row's cells: the text, or {"text", "colspan", "rowspan"} for a cell spanning more than one."""
    cells = []
    for cell in row:
        if cell.tag not in ("td", "th"):
            continue
        text = clean_text("".join(cell.itertext()))
        span = {k: int(cell.get(k)) for k in ("colspan", "rowspan") if (cell.get(k) or "1") != "1"}
        cells.append({"text": text, **span} if span else text)
    return cells


def table_cells(table: ET.Element) -> dict:
    """A <table> as {"caption", "head", "body", "foot"}: rows of cells, by the section they are in."""
    out: dict = {"caption": clean_text("".join(table.find("caption").itertext())) if table.find("caption") is not None
                 else None, "head": [], "body": [], "foot": []}  # fmt: skip
    for section in table:
        key = {"thead": "head", "tbody": "body", "tfoot": "foot"}.get(section.tag)
        if key:
            out[key] += [_cells(tr) for tr in section if tr.tag == "tr"]
    return out


def annex_entries(root: ET.Element, fragen_drucksachen: list[str] = ()) -> list[dict]:
    """The written answers to Fragestunde questions in a protocol's annexes, in order: drucksache_number, numbers,
    name (the asker), questions (one text per number, or one for all), answer_name, answer (list of ("text", str) and
    ("table", dict)), and answered (False when the entry says there is no answer). ``fragen_drucksachen``: the
    Drucksachen of the protocol's own Fragestunden, which correct a misprinted annex title (21/20: "Drucksache 21/483"
    for 21/1483)."""
    entries: list[dict] = []
    for annex in root.iter("anlagen-text"):
        kind = annex.get("anlagen-typ") or ""
        if "Fragestunde" not in kind:
            continue
        drucksache = (DRUCKSACHE_RE.findall(kind) or [None])[-1]
        if drucksache and drucksache not in fragen_drucksachen:
            wp, n = drucksache.split("/")
            drucksache = next((d for d in fragen_drucksachen if d.startswith(f"{wp}/") and d.endswith(n)), drucksache)
        entry = None
        for el in annex:
            if el.tag == "table":
                if entry and entry["answer_name"]:
                    entry["answer"].append(("table", table_cells(el)))
                continue
            text = clean_text("".join(el.itertext()))
            klasse = el.get("klasse") or ""
            if klasse.startswith("Anlage") and (m := _NUMBERS.fullmatch(text)):
                entry = {"drucksache_number": drucksache, "numbers": re.findall(r"\d+", m.group(1)), "name": None,
                         "questions": [], "answer_name": None, "answer": [], "answered": True}  # fmt: skip
                entries.append(entry)
            elif entry is None or not text:
                continue
            elif klasse == "O" and entry["name"] is None and (h := _HEADER.match(text)):
                entry["name"] = h["name"]
            elif klasse == "O" and text.startswith("Antwort") and text.endswith(":"):
                entry["answer_name"] = re.sub(r"^(?:Gemeinsame )?Antwort de[rs] ", "", text[:-1])
            elif entry["answer_name"] is None and klasse == "p" and entry["name"]:
                entry["questions"].append(text)
            elif entry["answer_name"] is not None:
                entry["answer"].append(("text", text))
            elif _NOT_ANSWERED.search(text):
                entry["answered"] = False
    return entries


def texts(protocol: Protocol, path: Path) -> tuple[list[dict], list[dict]]:
    """question_text and question_table rows of a protocol (without provenance)."""
    rows: list[dict] = []
    tables: list[dict] = []
    seen: set[str] = set()

    def add(row: dict) -> None:
        if row["id"] not in seen:
            seen.add(row["id"])
            rows.append(row | {"position": len(rows) + 1})

    for c in spoken_calls(protocol):
        add({"id": f"{c['drucksache_number']}/{c['number']}/frage", "drucksache_number": c["drucksache_number"],
             "part": "frage", "number": c["number"], "text": c["text"], "name": c["name"], "answer_date": None,
             "thread_id": c["thread_id"]})  # fmt: skip
    fragestunden = {s.agenda_item_id for s in protocol.speeches if s.kind == "fragestunde"}
    fragen_drucksachen = [d for i in protocol.agenda_items if i["id"] in fragestunden
                          for d in json.loads(i["drucksache_numbers"])]  # fmt: skip
    for e in annex_entries(ET.parse(path).getroot(), fragen_drucksachen):
        if not e["name"] or not e["questions"]:
            continue  # not answered under Nr. 9 Satz 2 of the Richtlinien: no asker and no text printed
        questions = e["questions"] if len(e["questions"]) == len(e["numbers"]) else ["\n\n".join(e["questions"])]
        answer = "\n\n".join(t for kind, t in e["answer"] if kind == "text")
        for number, question in zip(e["numbers"], questions, strict=False):
            key = f"{e['drucksache_number']}/{number}"
            add({"id": f"{key}/frage", "drucksache_number": e["drucksache_number"], "part": "frage",
                 "number": number, "text": question, "name": e["name"], "answer_date": None,
                 "thread_id": None})  # fmt: skip
            if not (e["answered"] and e["answer_name"] and answer):
                continue
            add({"id": f"{key}/antwort", "drucksache_number": e["drucksache_number"], "part": "antwort",
                 "number": number, "text": answer, "name": e["answer_name"], "answer_date": protocol.date,
                 "thread_id": None})  # fmt: skip
            k = 0
            for after, (kind, value) in enumerate(e["answer"]):
                if kind == "table":
                    k += 1
                    paragraph = sum(1 for kd, _ in e["answer"][:after] if kd == "text")
                    tables.append({"id": f"{key}/antwort/{k}", "question_text_id": f"{key}/antwort",
                                   "position": k, "after_paragraph": paragraph,
                                   "cells": json.dumps(value, ensure_ascii=False)})  # fmt: skip
    return rows, tables
