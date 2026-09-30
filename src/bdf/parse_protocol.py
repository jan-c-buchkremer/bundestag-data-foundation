"""Parse a Plenarprotokoll XML (DTD dbtplenarprotokoll, WP 19+) into sitting, agenda items,
speeches, paragraphs and the speakers seen.

Speech splitting rules (see docs/decisions.md):
- one ``<rede>`` normally is one speech by the speaker of its first ``<p klasse="redner">``;
- when another ``<redner>`` appears inside the same ``<rede>`` (Zwischenfrage), a new speech
  starts, id ``<rede id>-2``, ``-3`` …; the original speaker's continuation is another one;
- presidency remarks inside a ``<rede>`` (``<name>Vizepräsident …:</name>`` and the paragraphs
  that follow until the speaker resumes) are kept as paragraphs of kind ``chair``;
- ``<kommentar>`` is kind ``comment``; ``T_*`` classes are ``procedural``; all else is ``text``.
- ``Speech.kind`` is ``rede`` for all of the above.

Every WP21 Fragestunde (25 agenda items, one per sitting week) has no ``<rede>`` at all: the
question, the minister's or Staatssekretär's answer and every Nachfrage are ``<p klasse="redner">``
paragraphs directly under ``<tagesordnungspunkt>``, framed by the presidency's own text ("Ich rufe
die Frage 1 … auf", "Herr Staatssekretär.", "Haben Sie eine Nachfrage?"). ``_is_fragestunde`` detects
such an item structurally (no ``<rede>``, at least one direct ``<p klasse="redner">``) rather than by
title, so it also catches any other Wahlperiode that turns out to print Fragestunden the same way.
``_split_fragestunde`` turns each run starting at such a paragraph into its own ``Speech`` of kind
``fragestunde`` (id ``"<agenda_item_id>/f<n>"``, ``n`` counting redner runs in document order —
synthetic but stable across re-parses), the same way ``_split_rede`` does inside a ``<rede>``:
``<kommentar>`` attaches to it as a ``comment`` paragraph (and so earns interjection rows), and the
run ends at the next redner paragraph or presidency ``<name>``. The presidency's own framing text
(calling the item, the ministry, the question number, "Haben Sie eine Nachfrage?") is not attached to
any speech; it stays an ``agenda_item_paragraph`` of kind ``chair``, as it always was. Recording the
question number and the addressed ministry as their own fields was skipped: ``speech.fraction`` (the
asker) and ``speech.speaker_role`` (the answering office) already cover the counts callers need.

Text directly under ``<tagesordnungspunkt>``, outside any ``<rede>`` and outside a Fragestunde item,
is where the presidency calls items, puts questions to the vote and announces results. It is kept as
agenda item paragraphs (``Protocol.agenda_paragraphs``) with kinds ``chair``, ``comment`` and
``procedural`` (``T_*`` classes). The printed name lists of roll-call votes (``AL_Namen``,
``AL_Partei``, ``AL_Ja-Nein-Enth``) are left out: the XLSX has them per member.
"""

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from bdf.names import DRUCKSACHE_RE, clean_text, iso_date, normalize_fraction
from bdf.parse_sub_items import TITLE_CLASSES_SKIPPED as _TITLE_CLASSES_SKIPPED
from bdf.parse_sub_items import split as _split_sub_items

_VOTE_LIST_CLASSES = {"AL_Namen", "AL_Partei", "AL_Ja-Nein-Enth"}


@dataclass
class Speaker:
    id: str
    first_name: str
    last_name: str
    fraction: str | None
    role: str | None
    academic_title: str | None
    name_prefix: str | None
    printed: str


@dataclass
class Speech:
    id: str
    speaker: Speaker
    position: int
    agenda_item_id: str | None
    paragraphs: list[tuple[str, str]] = field(default_factory=list)  # (kind, text)
    kind: str = "rede"  # rede | fragestunde

    @property
    def text(self) -> str:
        return "\n\n".join(t for k, t in self.paragraphs if k == "text")


@dataclass
class Protocol:
    wahlperiode: int
    number: int
    date: str
    start_time: str | None
    end_time: str | None
    agenda_items: list[dict]
    speeches: list[Speech]
    # text directly under <tagesordnungspunkt>: dicts with id, agenda_item_id, position, kind, text,
    # klasse (the XML class of a procedural paragraph) and after_speeches (how many speeches of the sitting
    # precede it, to interleave with speech paragraphs)
    agenda_paragraphs: list[dict] = field(default_factory=list)
    # blocks of items called up one by one inside one agenda item (bdf/parse_sub_items.py)
    agenda_sub_items: list[dict] = field(default_factory=list)

    @property
    def sitting_id(self) -> str:
        return f"{self.wahlperiode}/{self.number}"

    @property
    def document_id(self) -> str:
        return f"BT-PlPr. {self.sitting_id}"


def _undouble(s: str) -> str:
    """Upstream bug seen in WP21 (e.g. 21/18, 21/24, 21/25): a speaker who is both MdB and
    minister gets two ids in one attribute ('11005217 999990074') and every name element
    doubled ('SvenjaSvenja'). The first id is the MdB id; halve doubled strings."""
    half = len(s) // 2
    return s[:half] if s and len(s) % 2 == 0 and s[:half] == s[half:] else s


def _speaker(p: ET.Element) -> Speaker:
    redner = p.find("redner")
    name = redner.find("name")
    field = lambda tag: _undouble(clean_text(name.findtext(tag)))  # noqa: E731
    return Speaker(
        id=redner.get("id").split()[0],
        first_name=field("vorname"),
        last_name=field("nachname"),
        fraction=normalize_fraction(field("fraktion") or None),
        role=field("rolle/rolle_lang") or None,
        academic_title=field("titel") or None,
        name_prefix=field("namenszusatz") or None,
        printed=clean_text(redner.tail).rstrip(":").strip(),
    )


def _paragraph_kind(el: ET.Element, chair_mode: bool) -> str:
    if el.tag == "kommentar":
        return "comment"
    if chair_mode:
        return "chair"
    if (el.get("klasse") or "").startswith("T_"):
        return "procedural"
    return "text"


def _split_rede(rede: ET.Element, position: int, agenda_item_id: str | None) -> list[Speech]:
    """Walk the children of one <rede> and return one Speech per contiguous speaker."""
    speeches: list[Speech] = []
    current: Speech | None = None
    chair_mode = False
    for el in rede:
        if el.tag == "p" and el.get("klasse") == "redner" and el.find("redner") is not None:
            speaker = _speaker(el)
            chair_mode = False
            if current is None or current.speaker.id != speaker.id:
                suffix = "" if not speeches else f"-{len(speeches) + 1}"
                current = Speech(
                    id=f"{rede.get('id')}{suffix}",
                    speaker=speaker,
                    position=position + len(speeches),
                    agenda_item_id=agenda_item_id,
                )
                speeches.append(current)
            continue
        if el.tag == "name":  # presidency interjection inside the speech
            chair_mode = True
            text = clean_text("".join(el.itertext()))
            if current is not None and text:
                current.paragraphs.append(("chair", text))
            continue
        if el.tag not in ("p", "kommentar", "zitat"):
            continue  # <a> page anchors, footnotes
        text = clean_text("".join(el.itertext()))
        if current is None or not text:
            continue
        kind = _paragraph_kind(el, chair_mode)
        current.paragraphs.append((kind, text))
    return speeches


def _agenda_item(top: ET.Element, sitting_id: str, position: int) -> dict:
    title_parts, numbers = [], []
    for p in top.findall("p"):
        klasse = p.get("klasse") or ""
        if not klasse.startswith("T_"):
            continue
        text = clean_text("".join(p.itertext()))
        numbers += [n for n in DRUCKSACHE_RE.findall(text) if n not in numbers]
        if klasse not in _TITLE_CLASSES_SKIPPED and text:
            title_parts.append(text)
    top_titel = top.find("top-titel")
    titel_text = clean_text("".join(top_titel.itertext())) if top_titel is not None else ""
    if titel_text:
        title_parts.insert(0, titel_text)
    return {
        "id": f"{sitting_id}/{position}",
        "position": position,
        "top_id": clean_text(top.get("top-id")),
        "title": " | ".join(title_parts) or None,
        "drucksache_numbers": json.dumps(numbers),
    }


def _agenda_paragraphs(top: ET.Element, agenda_item_id: str, speeches_before: int) -> list[dict]:
    """Paragraphs of one <tagesordnungspunkt> outside its <rede> elements, in document order."""
    rows: list[dict] = []
    chair_mode = True  # the presidency holds the floor between speeches
    for el in top:
        if el.tag == "rede":
            speeches_before += sum(1 for _ in _rede_speakers(el))
            continue
        klasse = el.get("klasse") or ""
        if klasse in _VOTE_LIST_CLASSES:
            continue
        if el.tag == "p" and klasse == "redner" and el.find("redner") is not None:
            kind, text, chair_mode = "speaker", clean_text(el.find("redner").tail).rstrip(":").strip(), False
        elif el.tag == "name":
            kind, text, chair_mode = "chair", clean_text("".join(el.itertext())), True
        elif el.tag in ("p", "kommentar", "zitat"):
            kind = "procedural" if klasse.startswith("T_") else _paragraph_kind(el, chair_mode)
            text = clean_text("".join(el.itertext()))
        else:
            continue
        if text:
            n = len(rows) + 1
            rows.append(
                {"id": f"{agenda_item_id}/{n}", "agenda_item_id": agenda_item_id, "position": n, "kind": kind,
                 "text": text, "klasse": klasse, "after_speeches": speeches_before}
            )  # fmt: skip
    return rows


def _is_fragestunde(top: ET.Element) -> bool:
    """A Fragestunde-style agenda item: no ``<rede>`` at all, but at least one direct-child
    ``<p klasse="redner">`` (docstring above). Structural, not title-based, so it is robust to any
    Wahlperiode printing its Fragestunde this way, without depending on the German wording."""
    return top.find("rede") is None and any(
        p.get("klasse") == "redner" and p.find("redner") is not None for p in top.findall("p")
    )


def _split_fragestunde(top: ET.Element, agenda_item_id: str, position: int, speeches_before: int) -> tuple[
    list[Speech], list[dict]
]:  # fmt: skip
    """A Fragestunde tagesordnungspunkt: each run starting at a direct-child ``<p klasse="redner">``
    becomes a ``Speech`` of kind ``fragestunde`` (mirrors ``_split_rede``, applied to the top's own
    children instead of a ``<rede>``'s); the presidency's framing text before, between and after those
    runs is returned as agenda_item_paragraph rows, exactly as ``_agenda_paragraphs`` would keep it."""
    speeches: list[Speech] = []
    paragraphs: list[dict] = []
    current: Speech | None = None
    n = 0
    for el in top:
        klasse = el.get("klasse") or ""
        if klasse in _VOTE_LIST_CLASSES:
            continue
        if el.tag == "p" and klasse == "redner" and el.find("redner") is not None:
            n += 1
            speeches_before += 1
            current = Speech(
                id=f"{agenda_item_id}/f{n}",
                speaker=_speaker(el),
                position=position + len(speeches),
                agenda_item_id=agenda_item_id,
                kind="fragestunde",
            )
            speeches.append(current)
            continue
        if el.tag == "name":
            current = None
            text = clean_text("".join(el.itertext()))
            if text:
                m = len(paragraphs) + 1
                paragraphs.append(
                    {"id": f"{agenda_item_id}/{m}", "agenda_item_id": agenda_item_id, "position": m,
                     "kind": "chair", "text": text, "after_speeches": speeches_before}
                )  # fmt: skip
            continue
        if el.tag not in ("p", "kommentar", "zitat"):
            continue
        text = clean_text("".join(el.itertext()))
        if not text:
            continue
        if current is not None:
            current.paragraphs.append((_paragraph_kind(el, chair_mode=False), text))
            continue
        kind = "procedural" if klasse.startswith("T_") else _paragraph_kind(el, chair_mode=True)
        m = len(paragraphs) + 1
        paragraphs.append(
            {"id": f"{agenda_item_id}/{m}", "agenda_item_id": agenda_item_id, "position": m,
             "kind": kind, "text": text, "klasse": klasse, "after_speeches": speeches_before}
        )  # fmt: skip
    return speeches, paragraphs


def _rede_speakers(rede: ET.Element):
    """The speeches a <rede> splits into, as _split_rede counts them (one per change of speaker)."""
    last = None
    for el in rede:
        if el.tag == "p" and el.get("klasse") == "redner" and el.find("redner") is not None:
            sid = el.find("redner").get("id").split()[0]
            if sid != last:
                last = sid
                yield sid


def parse(path: Path) -> Protocol:
    root = ET.parse(path).getroot()
    wp, nr = int(root.get("wahlperiode")), int(root.get("sitzung-nr"))
    sitting_id = f"{wp}/{nr}"
    verlauf = root.find("sitzungsverlauf")
    agenda_items: list[dict] = []
    speeches: list[Speech] = []
    agenda_paragraphs: list[dict] = []
    for el in verlauf:
        if el.tag == "rede":
            speeches += _split_rede(el, len(speeches) + 1, None)
        elif el.tag == "tagesordnungspunkt":
            item = _agenda_item(el, sitting_id, len(agenda_items) + 1)
            agenda_items.append(item)
            if _is_fragestunde(el):
                fq_speeches, fq_paragraphs = _split_fragestunde(el, item["id"], len(speeches) + 1, len(speeches))
                speeches += fq_speeches
                agenda_paragraphs += fq_paragraphs
            else:
                agenda_paragraphs += _agenda_paragraphs(el, item["id"], len(speeches))
                for rede in el.findall("rede"):
                    speeches += _split_rede(rede, len(speeches) + 1, item["id"])
    sub_items, no_debate = _split_sub_items(agenda_items, agenda_paragraphs)
    for item in agenda_items:
        item["no_debate"] = int(no_debate[item["id"]])
    return Protocol(
        wahlperiode=wp,
        number=nr,
        date=iso_date(root.get("sitzung-datum")),
        start_time=root.get("sitzung-start-uhrzeit") or None,
        end_time=root.get("sitzung-ende-uhrzeit") or None,
        agenda_items=agenda_items,
        speeches=speeches,
        agenda_paragraphs=agenda_paragraphs,
        agenda_sub_items=sub_items,
    )
