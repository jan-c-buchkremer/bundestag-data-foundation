"""Parse a Plenarprotokoll XML (DTD dbtplenarprotokoll, WP 19+) into sitting, agenda items,
speeches, paragraphs and the speakers seen.

Speech splitting rules (see docs/decisions.md):
- one ``<rede>`` normally is one speech by the speaker of its first ``<p klasse="redner">``;
- when another ``<redner>`` appears inside the same ``<rede>`` (Zwischenfrage), a new speech
  starts, id ``<rede id>-2``, ``-3`` …; the original speaker's continuation is another one;
- presidency remarks inside a ``<rede>`` (``<name>Vizepräsident …:</name>`` and the paragraphs
  that follow until the speaker resumes) are kept as paragraphs of kind ``chair``;
- ``<kommentar>`` is kind ``comment``; ``T_*`` classes are ``procedural``; all else is ``text``.
"""

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from bdf.names import DRUCKSACHE_RE, clean_text, iso_date, normalize_fraction

_TITLE_CLASSES_SKIPPED = {"T_Drs", "T_Ueberweisung"}


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


def parse(path: Path) -> Protocol:
    root = ET.parse(path).getroot()
    wp, nr = int(root.get("wahlperiode")), int(root.get("sitzung-nr"))
    sitting_id = f"{wp}/{nr}"
    verlauf = root.find("sitzungsverlauf")
    agenda_items: list[dict] = []
    speeches: list[Speech] = []
    for el in verlauf:
        if el.tag == "rede":
            speeches += _split_rede(el, len(speeches) + 1, None)
        elif el.tag == "tagesordnungspunkt":
            item = _agenda_item(el, sitting_id, len(agenda_items) + 1)
            agenda_items.append(item)
            for rede in el.findall("rede"):
                speeches += _split_rede(rede, len(speeches) + 1, item["id"])
    return Protocol(
        wahlperiode=wp,
        number=nr,
        date=iso_date(root.get("sitzung-datum")),
        start_time=root.get("sitzung-start-uhrzeit") or None,
        end_time=root.get("sitzung-ende-uhrzeit") or None,
        agenda_items=agenda_items,
        speeches=speeches,
    )
